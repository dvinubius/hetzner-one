# Hetzner-One

Shared Caddy ingress for the VPS. This Compose project is the sole owner of
public TCP 80/443 and UDP 443, TLS certificate state, hostname routing, and
the gallery bind mount. It serves `zibs.app`, `art-gallery.dinubarbu.com`, and
`hooklook.app`.

Hostnames are explicit in [`Caddyfile`](Caddyfile). There is no
`CADDY_DOMAIN` environment variable to configure.

Zibs and Hooklook each own their Grafana server, dashboard configuration, and
collectors. The platform stack adds its own Grafana, Prometheus, and
node_exporter for host and Caddy metrics; operate it with the
[observability runbook](docs/observability-runbook.md).
The current Compose project remains named `caddy` to retain existing resources.

## Dependencies

The project owns both shared edge networks and reuses the existing certificate volumes:

- `caddy_caddy-data` and `caddy_caddy-config` retain Caddy's ACME certificates
  and runtime configuration. They are external, created outside Compose, so no
  Compose command can remove them.
- `zibs-edge` is created and owned by this project. zibs joins it externally;
  Caddy reaches `zibs` and `grafana` by their Docker service names.
- `hooklook-edge` is created and owned by this project. Hooklook joins it as
  an external network so Caddy can reach `hooklook:8080`. Network membership
  permits peer connectivity; it is not a per-port firewall.
- `/opt/art-gallery/public` is mounted read-only at `/srv/art-gallery`.

## Hooklook ingress policy

The Caddy image is built on the VPS from Caddy `2.11.4` plus
`github.com/mholt/caddy-ratelimit` at immutable commit
`5625512f24f6f59d6f64fb3aafe5eecff0b286db` (published 2026-06-12, more than
three weeks before this change). The module is a sliding-window limiter and
automatically returns `429 Too Many Requests` with `Retry-After`.

- `GET /` is limited per direct socket-peer IP to 10 requests per minute.
- Public captures at `/b/{code}` and `/b/{code}/...` are limited per direct
  socket-peer IP to 60 per minute, with a separately enforced 20-request
  maximum in every 20-second window. This is an explicit bounded-burst policy,
  not a claim of token-bucket behavior.
- The same capture paths reject request bodies larger than 10 MB
  (10,000,000 bytes) with `413`. Together with the per-IP rate limits, this is
  a minimal guard against accidental bursts of heavy requests from one IP,
  not effective DDoS protection. Inspector pages, APIs, and SSE routes do not
  receive that body handler.
- Every HTTPS host shares Caddy's `:443` listener, whose configured 32 KiB
  setting rejects HTTP/1.1 total request headers above approximately 36 KiB
  because Go adds a 4 KiB parser-buffer allowance before proxying.

Caddy is directly internet-facing, so these limits deliberately key on its
socket peer (`{remote_host}`) and do not trust client-supplied forwarding
headers.
The 10 MB policy is deployed in the current Caddy configuration.

Ensure those resources exist before starting this project. Do not run `docker
compose down -v`: the volumes are external, but the command is unnecessary and
may affect future project-managed resources.

## Deploy

Copy `.env.production.example` to the local, Git-ignored `.env.production` and
set the VPS address. Set an independent `GRAFANA_ADMIN_PASSWORD` there before
deploying observability. Then run:

```bash
./scripts/deploy.sh caddy          # default; ingress only
./scripts/deploy.sh observability  # platform monitoring only
./scripts/deploy.sh full           # monitoring, then ingress, then verification
```

The script stages, validates, activates, and checks the selected services. It saves
rollback files and the previous running image on the VPS. Read the
[deployment runbook](docs/deployment-runbook.md) for prerequisites, a Caddyfile
reload, verification, diagnostics, and rollback. The [documentation index](docs/README.md)
routes to the current operational and historical records.
