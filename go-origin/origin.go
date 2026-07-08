// Package main implements the showrunner manifest origin: a stateless HLS
// origin that serves live media playlists from a precompiled cycle snapshot.
//
// The snapshot is produced by the Python control plane (app/core/snapshot.py).
// This origin re-implements the hot path only — resolve the wall clock to a
// window and render the .m3u8 — mirroring app/core/manifest.render_media_playlist
// byte-for-byte. Correctness is pinned by go-origin/testdata/golden.json, which
// is generated from the canonical Python renderer.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"sort"
	"strings"
	"time"
)

// ErrNotStarted is returned when the requested time precedes the channel epoch.
var ErrNotStarted = errors.New("channel not started")

type Segment struct {
	URI          string   `json:"uri"`
	Duration     float64  `json:"duration"`
	AssetID      string   `json:"asset_id"`
	AssetTitle   string   `json:"asset_title"`
	StartOffset  float64  `json:"start_offset"`
	InternalDisc int      `json:"internal_disc"`
	CueOut       *float64 `json:"cue_out"`
	CueIn        bool     `json:"cue_in"`
}

type Snapshot struct {
	Epoch          string    `json:"epoch"`
	CycleDuration  float64   `json:"cycle_duration"`
	TargetDuration int       `json:"target_duration"`
	WrapDisc       int       `json:"wrap_disc"`
	Segments       []Segment `json:"segments"`

	epochMicros   int64
	starts        []float64
	internalTotal int64
}

func LoadSnapshot(path string) (*Snapshot, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var s Snapshot
	if err := json.Unmarshal(data, &s); err != nil {
		return nil, err
	}
	if err := s.prepare(); err != nil {
		return nil, err
	}
	return &s, nil
}

func (s *Snapshot) prepare() error {
	t, err := time.Parse(time.RFC3339, s.Epoch)
	if err != nil {
		return fmt.Errorf("parse epoch: %w", err)
	}
	s.epochMicros = t.UTC().UnixMicro()
	s.starts = make([]float64, len(s.Segments))
	for i := range s.Segments {
		s.starts[i] = s.Segments[i].StartOffset
		s.internalTotal += int64(s.Segments[i].InternalDisc)
	}
	return nil
}

func (s *Snapshot) n() int64 { return int64(len(s.Segments)) }

func (s *Snapshot) disc(g int64) bool {
	if g == 0 {
		return false
	}
	idx := g % s.n()
	if idx == 0 {
		return s.WrapDisc != 0
	}
	return s.Segments[idx].InternalDisc != 0
}

func (s *Snapshot) discBefore(g int64) int64 {
	if g <= 0 {
		return 0
	}
	n := s.n()
	full := g / n
	rem := g % n
	total := full * s.internalTotal
	for i := int64(1); i < rem; i++ {
		total += int64(s.Segments[i].InternalDisc)
	}
	total += ((g - 1) / n) * int64(s.WrapDisc)
	return total
}

func (s *Snapshot) currentIndex(nowMicros int64) (int64, error) {
	posMicros := nowMicros - s.epochMicros
	if posMicros < 0 {
		return 0, ErrNotStarted
	}
	position := float64(posMicros) / 1e6
	cycle := math.Floor(position / s.CycleDuration)
	inCycle := position - cycle*s.CycleDuration
	// bisect_right(starts, inCycle) - 1
	idx := int64(sort.Search(len(s.starts), func(i int) bool {
		return s.starts[i] > inCycle
	})) - 1
	return int64(cycle)*s.n() + idx, nil
}

// pdt formats a segment's PROGRAM-DATE-TIME exactly as the Python renderer:
// epoch + (cycle*cycleDuration + startOffset) seconds, truncated to ms, "Z".
func (s *Snapshot) pdt(g int64) string {
	n := s.n()
	cycle := g / n
	idx := g % n
	off := float64(cycle)*s.CycleDuration + s.starts[idx]
	us := s.epochMicros + int64(math.Round(off*1e6))
	t := time.UnixMicro(us).UTC()
	ms := (us % 1_000_000) / 1000
	return t.Format("2006-01-02T15:04:05") + fmt.Sprintf(".%03dZ", ms)
}

func (s *Snapshot) Render(nowMicros int64, size int) (string, error) {
	current, err := s.currentIndex(nowMicros)
	if err != nil {
		return "", err
	}
	first := current - int64(size) + 1
	if first < 0 {
		first = 0
	}

	var b strings.Builder
	fmt.Fprintf(&b, "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:%d\n", s.TargetDuration)
	fmt.Fprintf(&b, "#EXT-X-MEDIA-SEQUENCE:%d\n#EXT-X-DISCONTINUITY-SEQUENCE:%d\n",
		first, s.discBefore(first))

	for g := first; g <= current; g++ {
		seg := s.Segments[g%s.n()]
		if s.disc(g) {
			b.WriteString("#EXT-X-DISCONTINUITY\n")
		}
		if seg.CueOut != nil {
			fmt.Fprintf(&b, "#EXT-X-CUE-OUT:%.3f\n", *seg.CueOut)
		}
		if seg.CueIn {
			b.WriteString("#EXT-X-CUE-IN\n")
		}
		fmt.Fprintf(&b, "#EXT-X-PROGRAM-DATE-TIME:%s\n", s.pdt(g))
		fmt.Fprintf(&b, "#EXTINF:%.3f,%s\n%s\n", seg.Duration, seg.AssetTitle, seg.URI)
	}
	return b.String(), nil
}
