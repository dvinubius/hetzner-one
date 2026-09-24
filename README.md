# Caddy One

Shared Caddy ingress for the VPS. This Compose project is the sole owner of
public TCP 80/443 and UDP 443, TLS certificate state, hostname routing, and
the gallery bind mount. It serves `zibs.app`, `art-gallery.dinubarbu.com`, and
`hooklook.app`.

Hostnames are explicit in [`Caddyfile`](Caddyfile). There is no
`CADDY_DOMAIN` environment variable to configure.

The independent plan to rename this project to Hetzner-One and add private host
metrics is in [`HOST-OBSERVABILITY-PLAN.md`](HOST-OBSERVABILITY-PLAN.md).
Hooklook application observability is deliberately outside that plan and does
not depend on host monitoring.

## Dependencies

The project owns both shared edge networks and reuses the existing certificate volumes:

- `zibs_caddy-data` and `zibs_caddy-config` retain Caddy's ACME certificates
  and runtime configuration.
- `zibs-edge` is created and owned by this project. zibs joins it externally;
  Caddy reaches `zibs` and `grafana` by their Docker service names.
- `hooklook-edge` is created and owned by this project. Hooklook joins it as
  an external network so Caddy can reach `hooklook:8080`; no Hooklook port is
  exposed through this network to any other service.
- `/opt/art-gallery/public` is mounted read-only at `/srv/art-gallery`.

## Hooklook ingress policy

The Caddy image is built locally from Caddy `2.11.4` plus
`github.com/mholt/caddy-ratelimit` at immutable commit
`5625512f24f6f59d6f64fb3aafe5eecff0b286db` (published 2026-06-12, more than
three weeks before this change). The module is a sliding-window limiter and
automatically returns `429 Too Many Requests` with `Retry-After`.

- `GET /` is limited per direct socket-peer IP to 10 requests per minute.
- Public captures at `/b/{code}` and `/b/{code}/...` are limited per direct
  socket-peer IP to 60 per minute, with a separately enforced 20-request
  maximum in every 20-second window. This is an explicit bounded-burst policy,
  not a claim of token-bucket behavior.
- The same capture paths reject request bodies larger than 256 KiB with `413`.
  Inspector pages, APIs, and SSE routes do not receive that body handler.
- Every HTTPS host shares Caddy's `:443` listener, which now rejects total
  request headers larger than 32 KiB before proxying.

Caddy is directly internet-facing, so these limits deliberately key on its
socket peer (`{remote_host}`) and do not trust client-supplied forwarding
headers.

Ensure those resources exist before starting this project. Do not run `docker
compose down -v`: the volumes are external, but the command is unnecessary and
may affect future project-managed resources.

## Validate

Run on the VPS from `/opt/caddy`:

```sh
docker compose config
docker compose build caddy
docker compose run --rm --no-deps caddy caddy list-modules | grep \
  '^http.handlers.rate_limit$'
docker compose run --rm --no-deps caddy caddy validate \
  --config /etc/caddy/Caddyfile --adapter caddyfile
```

The validation container does not publish ports. It requires the external
certificate volumes to exist; this project creates both edge networks.

## Deploy and reload

For a first cutover, stop the old `/opt/zibs` Caddy service, then start this
project immediately:

```sh
cd /opt/zibs && docker compose --profile production stop caddy
cd /opt/caddy && docker compose up -d
```

For a Caddyfile-only change, validate first and reload the running service. A
custom-image change needs `docker compose up -d --no-deps caddy` after the same
validation instead, so the running container uses the newly built binary.

```sh
cd /opt/caddy
docker compose run --rm --no-deps caddy caddy validate \
  --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose exec caddy caddy reload \
  --config /etc/caddy/Caddyfile --adapter caddyfile
```

For the first Hooklook rollout or any custom-image change, retain immediate
rollback artifacts before replacing the live container:

```sh
cd /opt/caddy
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p rollback/$stamp
docker image inspect caddy-hooklook:2.11.4-ratelimit >/dev/null 2>&1 && \
  docker image tag caddy-hooklook:2.11.4-ratelimit caddy-hooklook:rollback-$stamp
cp Caddyfile rollback/$stamp/Caddyfile
cp compose.yaml rollback/$stamp/compose.yaml
docker compose config > rollback/$stamp/compose.resolved.yaml
docker compose build caddy
docker compose run --rm --no-deps caddy caddy list-modules | grep \
  '^http.handlers.rate_limit$'
docker compose run --rm --no-deps caddy caddy validate \
  --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose up -d --no-deps caddy
```

Do not reload a container with a Caddyfile that requires a module its running
binary does not have.

Do not use `/opt/zibs/scripts/deploy.sh` to manage Caddy after the extraction.

## Verify and rollback

```sh
docker compose ps
curl --fail -I https://zibs.app
curl --fail -I https://art-gallery.dinubarbu.com
curl --fail -I https://art-gallery.dinubarbu.com/drawings/
curl --fail -I https://hooklook.app/health
```

If the new container fails or either hostname is unhealthy, stop this Caddy
project and restore the backed-up zibs Compose file and Caddyfile, then start
the old `caddy` service. The certificate volumes are shared rather than copied,
so certificate state survives the rollback.

For a Hooklook candidate rollback, restore its preserved Caddyfile and Compose
file, retag the saved `caddy-hooklook:rollback-<timestamp>` image as
`caddy-hooklook:2.11.4-ratelimit`, then run `docker compose up -d --no-deps
caddy`. If the prior setup used the official image, change the saved compose
file back to `caddy:2.11.4-alpine` before starting it.
