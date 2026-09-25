# Hooklook Caddy rollout — 2026-09-23

Hooklook is now prepared at the shared Caddy edge. This record replaces the
completed implementation plan and describes the deployed ingress change.

## Delivered configuration

- Caddy is built from `caddy:2.11.4` with
  `github.com/mholt/caddy-ratelimit` pinned to immutable commit
  `5625512f24f6f59d6f64fb3aafe5eecff0b286db` (published 2026-06-12).
- The shared Caddy project owns the `hooklook-edge` Docker bridge network.
  Hooklook joins it externally; Caddy proxies to `hooklook:8080` over that
  network.
- `hooklook.app` has a Caddy-managed Let's Encrypt certificate.
- Caddy's configured 32 KiB setting rejects HTTPS HTTP/1.1 request headers
  above approximately 36 KiB for every site sharing the `:443` listener,
  because Go adds a 4 KiB parser-buffer allowance.
- Only capture URLs matching `/b/{code}` or `/b/{code}/...` receive a 256 KiB
  request-body limit. Inspector pages, APIs, and SSE are not affected.
- Limits use Caddy's direct socket peer (`{remote_host}`), not untrusted
  forwarding headers: `GET /` permits 10 requests per minute; capture paths
  permit 60 requests per minute and no more than 20 in any rolling 20-second
  window. The latter is an explicit bounded-burst policy for the module's
  sliding-window algorithm, not token-bucket semantics.

## Rollout record

Before replacement, the VPS retained a timestamped copy of the Caddyfile,
Compose configuration, Dockerfile, resolved Compose configuration, and the
previous stock Caddy image under `/opt/caddy/rollback/`.

The candidate image was built and verified with:

```sh
docker compose config
docker compose build caddy
docker compose run --rm --no-deps caddy caddy list-modules
docker compose run --rm --no-deps caddy caddy validate \
  --config /etc/caddy/Caddyfile --adapter caddyfile
```

The running Caddy container was recreated with the custom image after those
checks passed. `zibs.app`, the art gallery root, and the gallery drawings path
all continued to return HTTP 200. Caddy subsequently obtained the production
certificate for `hooklook.app`.

## Current expected state

Until the Hooklook application is deployed and joins `hooklook-edge`, requests
to `hooklook.app` return Caddy `502` responses because Docker cannot resolve
the `hooklook` upstream. This confirms public DNS, TLS, and host routing are
active; it is not an ingress failure. Application deployment and the public
413, 429, and 431 boundary checks remain subsequent Ship work.
