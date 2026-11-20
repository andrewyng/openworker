// A private reverse proxy. The trusted application backend injects a workspace-slot
// bearer credential on every request, including upgrades; project code never receives it.
package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/netip"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

type target struct {
	IP   string
	UID  string
	Port int
}
type gateway struct {
	controller string
	client     *http.Client
}
type rejection int

func (r rejection) Error() string { return fmt.Sprintf("preview status %d", int(r)) }

func (g gateway) resolve(ctx context.Context, host, auth string) (target, error) {
	var t target
	req, err := http.NewRequestWithContext(ctx, "POST", g.controller+"/v1/preview/resolve", nil)
	if err != nil {
		return t, err
	}
	req.Header.Set("X-Preview-Host", host)
	req.Header.Set("Authorization", auth)
	resp, err := g.client.Do(req)
	if err != nil {
		return t, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return t, rejection(resp.StatusCode)
	}
	if err = json.NewDecoder(io.LimitReader(resp.Body, 4096)).Decode(&t); err != nil {
		return t, err
	}
	ip, err := netip.ParseAddr(t.IP)
	if err != nil || ip.IsLoopback() || ip.IsUnspecified() || ip.IsMulticast() || ip.IsLinkLocalUnicast() || t.Port < 5100 || t.Port > 5114 || t.UID == "" {
		return t, errors.New("invalid preview destination")
	}
	return t, nil
}
func failure(w http.ResponseWriter, err error) {
	var status rejection
	if errors.As(err, &status) && (status == 403 || status == 410) {
		http.Error(w, http.StatusText(int(status)), int(status))
		return
	}
	w.Header().Set("Retry-After", "2")
	http.Error(w, "Waiting for the workspace preview server.", 503)
}
func (g gateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "private, no-store")
	if r.Header.Get("Service-Worker") != "" {
		http.Error(w, "Forbidden", http.StatusForbidden)
		return
	}
	auth := r.Header.Get("Authorization")
	if len(auth) != 71 || !strings.HasPrefix(auth, "Bearer ") {
		http.Error(w, "Forbidden", 403)
		return
	}
	if r.Method == "CONNECT" || r.URL.IsAbs() {
		http.Error(w, "Invalid request", 400)
		return
	}
	host := strings.ToLower(r.Host)
	if origin := r.Header.Get("Origin"); origin != "" && origin != "https://"+host {
		http.Error(w, "Forbidden", 403)
		return
	}
	if up := r.Header.Get("Upgrade"); up != "" && !strings.EqualFold(up, "websocket") {
		http.Error(w, "Invalid upgrade", 400)
		return
	}
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	t, err := g.resolve(ctx, host, auth)
	if err != nil {
		failure(w, err)
		return
	}
	addr := net.JoinHostPort(t.IP, strconv.Itoa(t.Port))
	// Recheck AFTER TCP connect, before writing any request bytes. This closes the
	// lookup/IP-reuse race and binds the stream to one physical pod.
	tr := &http.Transport{DisableKeepAlives: true, DisableCompression: true, ResponseHeaderTimeout: 30 * time.Second}
	ctxDone := ctx.Done()
	tr.DialContext = func(ctx context.Context, network, unused string) (net.Conn, error) {
		c, err := (&net.Dialer{Timeout: 3 * time.Second}).DialContext(ctx, "tcp", addr)
		if err != nil {
			return nil, err
		}
		now, err := g.resolve(ctx, host, auth)
		if err != nil || now != t {
			c.Close()
			return nil, rejection(410)
		}

		go func() {
			timer := time.NewTicker(5 * time.Second)
			defer timer.Stop()
			for {
				select {
				case <-r.Context().Done():
					return
				case <-ctxDone:
					return
				case <-timer.C:
					current, err := g.resolve(r.Context(), host, auth)
					if err != nil || current != t {
						c.Close()
						return
					}
				}
			}
		}()
		return c, nil
	}
	proxy := httputil.ReverseProxy{Transport: tr, FlushInterval: -1,
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.SetURL(&url.URL{Scheme: "http", Host: addr})
			pr.Out.Host = "localhost:" + strconv.Itoa(t.Port)
			allowed := http.Header{}
			for _, k := range []string{"Accept", "Accept-Encoding", "Accept-Language", "Content-Type", "Range", "If-None-Match", "If-Modified-Since", "Cache-Control", "Connection", "Upgrade", "Sec-WebSocket-Key", "Sec-WebSocket-Version", "Sec-WebSocket-Protocol", "Sec-WebSocket-Extensions"} {
				if values := pr.Out.Header.Values(k); len(values) > 0 {
					allowed[k] = values
				}
			}
			pr.Out.Header = allowed
		},
		ModifyResponse: func(resp *http.Response) error {
			for _, k := range []string{"Set-Cookie", "Access-Control-Allow-Origin", "Access-Control-Allow-Credentials", "Clear-Site-Data", "Strict-Transport-Security", "Alt-Svc", "Report-To", "NEL"} {
				resp.Header.Del(k)
			}
			resp.Header.Set("Cache-Control", "private, no-store")
			resp.Header.Set("Referrer-Policy", "no-referrer")
			return nil
		},
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) { failure(w, err) },
	}
	proxy.ServeHTTP(w, r.WithContext(ctx))
	tr.CloseIdleConnections()
}
func main() {
	cert, err := os.ReadFile(os.Getenv("WORKSPACE_CA_FILE"))
	if err != nil {
		log.Fatal(err)
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(cert) {
		log.Fatal("invalid provider CA")
	}
	endpoint, err := url.Parse(os.Getenv("WORKSPACE_SERVICE_URL"))
	if err != nil || endpoint.Scheme != "https" || endpoint.Host == "" {
		log.Fatal("expected HTTPS workspace service")
	}
	client := &http.Client{Timeout: 5 * time.Second, Transport: &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: roots}}, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	server := http.Server{Addr: ":8443", Handler: gateway{endpoint.String(), client}, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 32768}
	log.Fatal(server.ListenAndServeTLS("/tls/tls.crt", "/tls/tls.key"))
}
