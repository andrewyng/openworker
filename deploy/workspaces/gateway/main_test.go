package main

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestUnauthenticatedAndCrossOriginUpgrades(t *testing.T) {
	for _, tc := range []struct{ auth, origin string }{
		{"", "https://abc-00.preview.test"},
		{"Bearer " + strings.Repeat("f", 64), "https://abc-01.preview.test"},
	} {
		r := httptest.NewRequest("GET", "/", nil)
		r.Host = "abc-00.preview.test"
		r.Header.Set("Authorization", tc.auth)
		r.Header.Set("Origin", tc.origin)
		r.Header.Set("Connection", "Upgrade")
		r.Header.Set("Upgrade", "websocket")
		w := httptest.NewRecorder()
		(gateway{}).ServeHTTP(w, r)
		if w.Code != 403 {
			t.Fatalf("got %d", w.Code)
		}
	}
}
func TestResolverDestinations(t *testing.T) {
	for _, tc := range []struct {
		ip    string
		port  int
		valid bool
	}{
		{"10.0.0.12", 5100, true}, {"fd01::1", 5114, true}, {"127.0.0.1", 5100, false},
		{"169.254.1.2", 5100, false}, {"upstream.example", 5100, false}, {"10.0.0.12", 5115, false},
	} {
		t.Run(fmt.Sprintf("%s-%d", tc.ip, tc.port), func(t *testing.T) {
			host := "abc-00.preview.test"
			token := "Bearer test"
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != "POST" || r.URL.Path != "/v1/preview/resolve" || r.Header.Get("Authorization") != token || r.Header.Get("X-Preview-Host") != host {
					t.Error("unexpected resolver request")
				}
				fmt.Fprintf(w, `{"ip":%q,"uid":"pod-uid","port":%d}`, tc.ip, tc.port)
			}))
			defer s.Close()
			_, err := (gateway{s.URL, s.Client()}).resolve(context.Background(), host, token)
			if (err == nil) != tc.valid {
				t.Fatalf("unexpected resolution: %v", err)
			}
		})
	}
}
