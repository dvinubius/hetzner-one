#!/usr/bin/env bash
set -euo pipefail

# Run on the VPS. The local deploy script includes this in its staged payload.
cd /opt/caddy
[[ -n $(docker compose ps --status running -q caddy) ]] || {
	printf '%s\n' 'Caddy container is not running.' >&2
	exit 1
}
curl --fail --silent --show-error --max-time 15 -o /dev/null https://zibs.app/
curl --fail --silent --show-error --max-time 15 -o /dev/null https://art-gallery.dinubarbu.com/
curl --fail --silent --show-error --max-time 15 -o /dev/null https://art-gallery.dinubarbu.com/drawings/
curl --fail --silent --show-error --max-time 15 -o /dev/null https://hooklook.app/health
printf '%s\n' 'Caddy and public routes are healthy.'
