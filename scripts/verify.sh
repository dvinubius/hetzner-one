#!/usr/bin/env bash
set -euo pipefail

# Run on the VPS. Every Caddy deployment runs the staged copy as its last step
# and rolls back if it fails; the deployment installs it at
# /opt/caddy/scripts/verify.sh for manual checks.
cd /opt/caddy
container=$(docker compose ps --status running -q caddy)
[[ -n $container ]] || {
	printf '%s\n' 'Caddy container is not running.' >&2
	exit 1
}
started=$(docker inspect --format '{{.State.StartedAt}}' "$container")
curl --fail --silent --show-error --max-time 15 -o /dev/null https://zibs.app/
curl --fail --silent --show-error --max-time 15 -o /dev/null https://art-gallery.dinubarbu.com/
curl --fail --silent --show-error --max-time 15 -o /dev/null https://art-gallery.dinubarbu.com/drawings/
curl --fail --silent --show-error --max-time 15 -o /dev/null https://hooklook.app/health
printf '%s\n' 'Caddy and public routes are healthy.'

# After the route checks, so errors caused by those requests are included.
# Caddy logs 4xx responses (rate limits, body limits) below error level; error
# entries mean TLS, configuration, or 5xx upstream failures.
errors=$(docker logs --since "$started" "$container" 2>&1 | grep -E '"level":"(error|fatal|panic)"' || true)
if [[ -n $errors ]]; then
	printf 'Caddy logged errors since it started at %s:\n' "$started" >&2
	printf '%s\n' "$errors" | tail -n 20 >&2
	exit 1
fi
printf 'No Caddy errors logged since %s.\n' "$started"
