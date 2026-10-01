package main

import (
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// cachedManifest serves one channel's live playlist with a 1-second TTL.
// Because the manifest is a pure function of the wall clock, one render per
// second serves every concurrent viewer; the same lever the Python origin uses.
type cachedManifest struct {
	snap   *Snapshot
	size   int
	mu     sync.Mutex
	expiry time.Time
	body   string
}

func (c *cachedManifest) get() (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	now := time.Now()
	if now.Before(c.expiry) {
		return c.body, nil
	}
	body, err := c.snap.Render(now.UTC().UnixMicro(), c.size)
	if err != nil {
		return "", err
	}
	c.body = body
	c.expiry = now.Add(time.Second)
	return body, nil
}

type server struct {
	channels map[string]*cachedManifest
}

func (s *server) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /channel/{channel}/playlist.m3u8", func(w http.ResponseWriter, r *http.Request) {
		c, ok := s.channels[r.PathValue("channel")]
		if !ok {
			http.Error(w, "unknown channel", http.StatusNotFound)
			return
		}
		body, err := c.get()
		if err == ErrNotStarted {
			http.Error(w, "channel not started", http.StatusNotFound)
			return
		} else if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/vnd.apple.mpegurl")
		w.Header().Set("Cache-Control", "max-age=1")
		_, _ = w.Write([]byte(body))
	})
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("ok"))
	})
	return mux
}

// loadChannels loads one snapshot per channel. A directory holds
// <channel>.json files; a single file is served as channel "demo".
func loadChannels(snapshotDir, snapshotFile string, size int) (map[string]*cachedManifest, error) {
	channels := map[string]*cachedManifest{}
	if snapshotDir != "" {
		matches, err := filepath.Glob(filepath.Join(snapshotDir, "*.json"))
		if err != nil {
			return nil, err
		}
		for _, path := range matches {
			snap, err := LoadSnapshot(path)
			if err != nil {
				return nil, err
			}
			id := strings.TrimSuffix(filepath.Base(path), ".json")
			channels[id] = &cachedManifest{snap: snap, size: size}
		}
		return channels, nil
	}
	snap, err := LoadSnapshot(snapshotFile)
	if err != nil {
		return nil, err
	}
	channels["demo"] = &cachedManifest{snap: snap, size: size}
	return channels, nil
}

func main() {
	addr := getenv("SHOWRUNNER_ADDR", ":8100")
	channels, err := loadChannels(
		os.Getenv("SHOWRUNNER_SNAPSHOT_DIR"),
		getenv("SHOWRUNNER_SNAPSHOT", "testdata/snapshot.json"),
		6,
	)
	if err != nil {
		log.Fatalf("load snapshots: %v", err)
	}
	if len(channels) == 0 {
		log.Fatal("no channel snapshots found")
	}

	srv := &server{channels: channels}
	log.Printf("showrunner origin listening on %s (%d channel(s))", addr, len(channels))
	log.Fatal(http.ListenAndServe(addr, srv.handler()))
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
