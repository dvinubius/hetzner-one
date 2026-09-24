# Hetzner-One

Shared Caddy ingress on one VPS. This repository owns Caddy's image, Compose
service, hostname routes, public ports, and its `hooklook-edge` and `zibs-edge` networks.

Before deployment or rollback, read [docs/deployment-runbook.md](docs/deployment-runbook.md).

Keep routes, deployment scripts, and the runbook consistent.

Preserve the external certificate volumes and both shared edge networks.
The retired `zibs_app-edge` network may remain empty for migration rollback.
Never use `docker compose down -v`.
