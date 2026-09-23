# Caddy One

Shared Caddy ingress for the VPS. This Compose project is the sole owner of
public TCP 80/443 and UDP 443, TLS certificate state, hostname routing, and
the gallery bind mount. It currently serves `zibs.app` and
`art-gallery.dinubarbu.com`.

Hostnames are explicit in [`Caddyfile`](Caddyfile). There is no
`CADDY_DOMAIN` environment variable to configure.

The independent plan to rename this project to Hetzner-One and add private host
metrics is in [`HOST-OBSERVABILITY-PLAN.md`](HOST-OBSERVABILITY-PLAN.md).
Hooklook application observability is deliberately outside that plan and does
not depend on host monitoring.

## Dependencies

The project deliberately reuses Docker resources created by the zibs project:

- `zibs_caddy-data` and `zibs_caddy-config` retain Caddy's ACME certificates
  and runtime configuration.
- `zibs_app-edge` lets Caddy reach the `zibs` and `grafana` services by their
  Docker service names. zibs owns this network's lifecycle.
- `/opt/art-gallery/public` is mounted read-only at `/srv/art-gallery`.

Ensure those resources exist before starting this project. Do not run `docker
compose down -v`: the volumes are external, but the command is unnecessary and
may affect future project-managed resources.

## Validate

Run on the VPS from `/opt/caddy`:

```sh
docker compose config
docker compose run --rm --no-deps caddy caddy validate \
  --config /etc/caddy/Caddyfile --adapter caddyfile
```

The validation container does not publish ports. It requires the external
network and volumes to exist.

## Deploy and reload

For a first cutover, stop the old `/opt/zibs` Caddy service, then start this
project immediately:

```sh
cd /opt/zibs && docker compose --profile production stop caddy
cd /opt/caddy && docker compose up -d
```

For a Caddyfile-only change, validate first and reload the running service:

```sh
cd /opt/caddy
docker compose run --rm --no-deps caddy caddy validate \
  --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose exec caddy caddy reload \
  --config /etc/caddy/Caddyfile --adapter caddyfile
```

Do not use `/opt/zibs/scripts/deploy.sh` to manage Caddy after the extraction.

## Verify and rollback

```sh
docker compose ps
curl --fail -I https://zibs.app
curl --fail -I https://art-gallery.dinubarbu.com
curl --fail -I https://art-gallery.dinubarbu.com/drawings/
```

If the new container fails or either hostname is unhealthy, stop this Caddy
project and restore the backed-up zibs Compose file and Caddyfile, then start
the old `caddy` service. The certificate volumes are shared rather than copied,
so certificate state survives the rollback.
