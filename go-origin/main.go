package main

import (
	"log"
	"net/http"
	"os"
	"sync"
	"time"
)

// cachedManifest serves the live playlist with a 1-second TTL. Because the
// manifest is a pure function of the wall clock, one render per second serves
// every concurrent viewer — the same lever the Python origin uses.
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

func main() {
	snapshotPath := getenv("SHOWRUNNER_SNAPSHOT", "testdata/snapshot.json")
	addr := getenv("SHOWRUNNER_ADDR", ":8100")

	snap, err := LoadSnapshot(snapshotPath)
	if err != nil {
		log.Fatalf("load snapshot: %v", err)
	}
	cache := &cachedManifest{snap: snap, size: 6}

	mux := http.NewServeMux()
	mux.HandleFunc("/channel/demo/playlist.m3u8", func(w http.ResponseWriter, r *http.Request) {
		body, err := cache.get()
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
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("ok"))
	})

	log.Printf("showrunner origin listening on %s (snapshot: %s, %d segments)",
		addr, snapshotPath, len(snap.Segments))
	log.Fatal(http.ListenAndServe(addr, mux))
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
