# Hetzner-One

Shared Caddy ingress for the VPS. This Compose project is the sole owner of
public TCP 80/443 and UDP 443, TLS certificate state, hostname routing, and
the gallery bind mount. It serves `zibs.app`, `art-gallery.dinubarbu.com`,
`hooklook.app`, and `saga.dinubarbu.com`.

Hostnames are explicit in [`Caddyfile`](Caddyfile). There is no
`CADDY_DOMAIN` environment variable to configure.

Zibs, Hooklook, and Saga Lab each own their Grafana server, dashboard configuration, and
collectors. The platform stack adds its own Grafana, Prometheus, and
node_exporter for host and Caddy metrics; operate it with the
[observability runbook](docs/observability-runbook.md).
The current Compose project remains named `caddy` to retain existing resources.

## Dependencies

The project owns the shared edge networks and reuses the existing certificate volumes:

- `caddy_caddy-data` and `caddy_caddy-config` retain Caddy's ACME certificates
  and runtime configuration. They are external, created outside Compose, so no
  Compose command can remove them.
- `zibs-edge` is created and owned by this project.
  [zibs](https://github.com/dvinubius/zibs) joins it externally;
  Caddy reaches the app as `zibs` and its Grafana as `zibs-grafana-1`, the
  Compose container name. The bare service name `grafana` is ambiguous once
  Hooklook's Grafana also joins `hooklook-edge`.
- `hooklook-edge` is created and owned by this project.
  [Hooklook](https://github.com/dvinubius/hooklook) joins it as an external
  network so Caddy can reach `hooklook:8080`, and its Grafana under the alias
  `hooklook-grafana` for the public-dashboard allowlist. Network membership
  permits peer connectivity; it is not a per-port firewall.
- `saga-lab-edge` is created and owned by this project.
  [Saga Lab](https://github.com/dvinubius/saga-lab) joins it as an external
  network with its Transfer Service under the alias `saga-lab` and its Grafana
  under `saga-lab-grafana`; nothing else of Saga Lab joins it.
- `/opt/art-gallery/public` is mounted read-only at `/srv/art-gallery`.

## Hooklook ingress policy

GitHub Actions builds the Caddy image and publishes it to GHCR as
`ghcr.io/dvinubius/hetzner-one-caddy`; the VPS runs it by digest. It is Caddy
`2.11.4` plus
`github.com/mholt/caddy-ratelimit` at immutable commit
`5625512f24f6f59d6f64fb3aafe5eecff0b286db` (published 2026-06-12, more than
three weeks before this change). The module is a sliding-window limiter and
automatically returns `429 Too Many Requests` with `Retry-After`.

- `GET /` is limited per direct socket-peer IP to 20 requests per minute.
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

## Saga Lab ingress policy

`saga.dinubarbu.com` proxies `/grafana/*` to `saga-lab-grafana:3000` with the
prefix kept, since Saga Lab's Grafana serves from that sub-path, and passes
WebSocket upgrades for Grafana Live. Everything else goes to `saga-lab:8080`.
Its DNS points at the VPS without Cloudflare proxying, and Caddy obtains its
certificate.

Unlike `zibs.app` and `hooklook.app`, which expose only the narrow
externally shared dashboard allowlist, Saga Lab's whole Grafana UI is
proxied. Each transfer page links to a Trace dashboard with its `traceId`
as a dashboard variable, and an externally shared dashboard cannot take
variables. Saga Lab's Grafana therefore grants anonymous visitors the Viewer
role; its own configuration, not Caddy, keeps them read-only.

Both limits key on the direct socket peer (`{remote_host}`) with metrics
disabled, like Hooklook's:

- `POST` to `/transfers`, `/top-ups`, `/reset`, or `/api/*`: 20 per minute per
  client address, one budget across all of them. Page and API reads are not
  limited.
- Any request under `/grafana/*`: 300 per minute per client address.

Until Saga Lab is deployed and joins `saga-lab-edge`, the site answers `502`.

Ensure those resources exist before starting this project. Do not run `docker
compose down -v`: the volumes are external, but the command is unnecessary and
may affect future project-managed resources.

## Deploy

Pushes to `main` that change `Caddyfile`, `Dockerfile`, `compose.yaml`,
`compose.observability.yaml`, or `observability/` deploy through the
[`Deploy production`](.github/workflows/deploy.yml) GitHub Actions workflow.
Other pushes start no run. Pull requests into `main` run the same tests
through [`test.yml`](.github/workflows/test.yml), which branch protection
requires. The deploy workflow tests the change, compares it with the
last verified deployment recorded on the VPS, and runs only the affected mode:
`caddy`, `observability`, or `full`. Caddy deployments use an image published
to GHCR, rebuilt only when the Dockerfile changes.

The workstation script stays for the first observability installation,
Grafana password changes, and recovery. Copy `.env.production.example` to the
local, Git-ignored `.env.production`, set the VPS address and deployment
account, and set an independent `GRAFANA_ADMIN_PASSWORD` before deploying
observability. Then run:

```bash
./scripts/deploy.sh caddy          # default; ingress only
./scripts/deploy.sh observability  # platform monitoring only
./scripts/deploy.sh full           # monitoring, then ingress, then verification
```

Both paths stage, validate, activate, and check the selected services with
the same VPS scripts, saving rollback files and the previous running image.
Read the [deployment runbook](docs/deployment-runbook.md) for one-time GitHub
and VPS setup, prerequisites, a Caddyfile reload, verification, diagnostics,
and rollback. The [documentation index](docs/README.md)
routes to the current operational and historical records.

## License

[MIT](LICENSE)
