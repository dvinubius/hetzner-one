# Caddy deployment runbook

This is the current procedure for redeploying shared Caddy ingress from this
repository. It assumes a prepared Docker VPS; it does not provision the host,
manage DNS, or deploy any upstream application. The live project is `/opt/caddy`.

## What this project owns

`compose.yaml` publishes TCP 80/443 and UDP 443, builds the pinned Caddy image,
and mounts `Caddyfile`. Caddy serves `zibs.app`, `art-gallery.dinubarbu.com`, and
`hooklook.app`. It also owns both `hooklook-edge` and `zibs-edge` Docker networks. Hooklook
and zibs join their respective edge networks externally. The
`caddy_caddy-data`/`caddy_caddy-config` volumes must already exist; they are
external resources and must be preserved. Gallery files
must exist at `/opt/art-gallery/public` and are mounted read-only. No secrets
or domain values are needed by the Caddy Compose file.

## Prerequisites

- Local `bash`, `ssh`, and `rsync`, with SSH access to the VPS. The deployment
  user needs Docker access and write access to `/opt/caddy`.
- On the VPS: Docker with Compose, `curl`, the external resources above, and
  `/opt/art-gallery/public`.
- Public DNS and ports 80/443 already point to this VPS.
- Copy `.env.production.example` to `.env.production` and set `DEPLOY_HOST`.
  Observability and full deployments also require an independent
  `GRAFANA_ADMIN_PASSWORD` there.
  `DEPLOY_USER` defaults to `root`; `DEPLOY_SSH_KEY` is optional. The local
  `.env.production` is ignored by Git. Caddy-only deployments use its
  deployment coordinates; observability deployments upload the Grafana password
  through SSH into a protected VPS `.env`.

## Deployment modes

The user runs deployment and VPS verification commands. For initial monitoring
setup, secrets, staged activation, verification, and independent rollback, follow
the [observability runbook](observability-runbook.md).

| Local command | Services activated |
| --- | --- |
| `./scripts/deploy.sh caddy` (default) | Caddy only |
| `./scripts/deploy.sh observability` | Platform node_exporter, Prometheus, Grafana only |
| `./scripts/deploy.sh full` | Observability, then Caddy, then complete verification |

The two Compose files use the existing `caddy` project with distinct services.
Always select the file and services explicitly; do not use orphan removal.
The new Caddy metrics listener is unpublished on `:9180`, on the internal
`platform-metrics` network shared with Prometheus. Its edge-network peers can
also reach that listener. The admin API stays on container loopback.

## Deploy an image, Compose, or Caddyfile change

From this repository:

```bash
./scripts/deploy.sh
```

The script checks VPS prerequisites, uploads `Caddyfile`, `Dockerfile`,
`compose.yaml`, and its activation and verification scripts to a timestamped staging directory
under `/opt/caddy/.staging`. It uploads the observability configuration and
helpers too, but only activates the selected mode. It saves the current three files, resolved Compose
configuration, and running image under `/opt/caddy/rollback/<UTC stamp>`. It
builds the candidate image on the VPS, checks that the rate-limit module is
present, and validates the candidate Caddyfile before replacing live files.
It then recreates only `caddy` and verifies the container and public routes.

If activation or verification fails, the script restores the saved files and
image and recreates the previous Caddy container. Inspect the reported error
and verify recovery. A first deployment with no previous container cannot
restore one automatically. Deployments are serialized with `flock`. The script does not run `docker compose down`,
remove volumes, or change upstream services.

## Reload a Caddyfile-only edit on the VPS

Use this only when `Dockerfile` and `compose.yaml` have not changed and the
running image already contains every module required by the new Caddyfile.
The full deployment script above is the standard path from the local checkout.
For an urgent edit already present at `/opt/caddy/Caddyfile`:

```bash
cd /opt/caddy
docker compose exec caddy caddy validate \
  --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose exec caddy caddy reload \
  --config /etc/caddy/Caddyfile --adapter caddyfile
```

Then run the public checks below. Copy the current Caddyfile before editing it
so it can be restored if validation or verification fails.

## Verify and diagnose

On the VPS:

```bash
cd /opt/caddy
docker compose ps
docker compose logs --tail=100 caddy
curl --fail -I https://zibs.app/
curl --fail -I https://art-gallery.dinubarbu.com/
curl --fail -I https://art-gallery.dinubarbu.com/drawings/
curl --fail -I https://hooklook.app/health
```

If Caddy is running but an application route returns `502`, inspect its Docker
network attachment and upstream availability. A `502` at `hooklook.app` was an
expected temporary state before Hooklook was first deployed; the current
deployment verifier requires its `/health` route to succeed. Do not read the
related application repositories unless the user explicitly asks; Docker
status, network inspection, and Caddy logs are enough for initial triage.

After deploying a Hooklook capture-body policy change, run Hooklook's
`scripts/verify-public.sh` from a trusted workstation. Its 10 MB boundary
check sends one accepted 10,000,000-byte capture and checks that fixed-length
and chunked 10,000,001-byte captures receive `413`. Run the new verifier only
after the matching Caddyfile is live.

## Roll back after a successful deployment

Use the timestamp printed by the deploy script. On the VPS:

```bash
cd /opt/caddy
stamp=YYYYMMDDTHHMMSSZ
cp "rollback/$stamp/Caddyfile" Caddyfile
cp "rollback/$stamp/Dockerfile" Dockerfile
cp "rollback/$stamp/compose.yaml" compose.yaml
docker image tag "caddy-hooklook:rollback-$stamp" caddy-hooklook:2.11.4-ratelimit
docker compose up -d --no-deps --force-recreate caddy
```

Then repeat the public checks above. The image tag is present only if a Caddy
container was running when that backup was made. Keep the certificate volumes;
never use `docker compose down -v`.
