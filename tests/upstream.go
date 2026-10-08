// Local integration fixture: consume uploads so Caddy exercises its body limit,
// answer with the port and path that reached it, and echo upgraded connections.
package main

import (
	"io"
	"net/http"
)

func handler(port string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Upgrade") == "websocket" {
			conn, _, err := http.NewResponseController(w).Hijack()
			if err != nil {
				return
			}
			defer conn.Close()
			conn.Write([]byte("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"))
			io.Copy(conn, conn)
			return
		}
		if _, err := io.Copy(io.Discard, r.Body); err != nil {
			return
		}
		w.Write([]byte(port + " " + r.URL.Path))
	}
}

func main() {
	go http.ListenAndServe(":3000", handler("3000"))
	http.ListenAndServe(":8080", handler("8080"))
}
