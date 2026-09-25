// Local integration fixture: consume uploads so Caddy exercises its body limit.
package main

import (
 "io"
 "net/http"
)

func main() {
 http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
  if _, err := io.Copy(io.Discard, r.Body); err != nil { return }
  w.Write([]byte("ok"))
 })
 http.ListenAndServe(":8080", nil)
}
