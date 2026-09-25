# Hetzner-One

Shared Caddy ingress on one VPS. This repository owns Caddy's image, Compose
service, hostname routes, public ports, and its `hooklook-edge` and `zibs-edge` networks.

Before deployment or rollback, read [docs/deployment-runbook.md](docs/deployment-runbook.md).

Keep routes, deployment scripts, and the runbook consistent.

Preserve the external certificate volumes and both shared edge networks.
The retired `zibs_app-edge` network may remain empty for migration rollback.
Never use `docker compose down -v`.

Platform monitoring lives in `compose.observability.yaml` under the same `caddy`
project. Keep service-scoped deployment modes; never reconcile Caddy during an
observability-only update. Read `docs/observability-runbook.md` before changing
monitoring. The user executes VPS deployment and verification; prepare and
validate locally, then provide the commands. Do not run those VPS actions.
