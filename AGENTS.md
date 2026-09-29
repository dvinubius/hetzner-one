# Hetzner-One

Shared Caddy ingress on one VPS. This repository owns Caddy's image, Compose
service, hostname routes, public ports, and its `hooklook-edge` and `zibs-edge` networks.

Before deployment or rollback, read [docs/deployment-runbook.md](docs/deployment-runbook.md).
Pushes to `main` that change deployed files run `.github/workflows/deploy.yml`,
which publishes the Caddy image to GHCR; the VPS never builds it. Its tests
live in `.github/workflows/test.yml`, which also runs for pull requests into
`main` and never publishes. Keep the
workflow's push `paths`, `scripts/classify-deploy.sh`, and the bundle in
`scripts/ci-deploy.sh` in step (`tests/ci_deploy.py` checks the first two).

Keep routes, deployment scripts, the workflow, and the runbook consistent.

Preserve the external certificate volumes and both shared edge networks.
Never use `docker compose down -v`.

Platform monitoring lives in `compose.observability.yaml` under the same `caddy`
project. Keep service-scoped deployment modes; never reconcile Caddy during an
observability-only update. Read `docs/observability-runbook.md` before changing
monitoring. The user executes VPS deployment and verification; prepare and
validate locally, then provide the commands. Do not run those VPS actions.
