# Hooklook Caddy changes

- Build Caddy 2.11.4 with an immutable, compatibility-tested rate-limit module
  pin that is at least three weeks old; confirm it with `caddy list-modules`.
- Create the external `hooklook-edge` network and attach Caddy to it.
- Add `hooklook.dinubarbu.com`, proxying to `hooklook:8080` over
  `hooklook-edge`.
- Apply `request_body { max_size 256KiB }` only to `/b/{code}` and
  `/b/{code}/*`.
- Rate-limit per socket peer IP: `GET /` at 10/minute; `/b/*` at 60/minute,
  with an explicit burst of 20. Return `429` with `Retry-After`.
- Set `servers :443 { max_header_size 32KiB }`.
- Validate the candidate image and Caddyfile before reload; preserve the current
  image and configuration for rollback. Verify zibs, the art gallery, and
  Hooklook after reload.
