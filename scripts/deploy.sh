#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"

mode=${1:-caddy}
case "$mode" in
 caddy|observability|full) ;;
 *) printf 'Usage: %s [caddy|observability|full]\n' "$0" >&2; exit 2 ;;
esac

if [[ ! -f .env.production ]]; then
	printf '%s\n' 'Copy .env.production.example to .env.production and set DEPLOY_HOST.' >&2
	exit 1
fi
set -a
# shellcheck disable=SC1091
source .env.production
set +a
grafana_admin_password=${GRAFANA_ADMIN_PASSWORD:-}
unset GRAFANA_ADMIN_PASSWORD

[[ -n ${DEPLOY_HOST:-} && $DEPLOY_HOST != your-vps-hostname-or-ip ]] || {
	printf '%s\n' 'Set DEPLOY_HOST in .env.production.' >&2
	exit 1
}
[[ $DEPLOY_HOST =~ ^[A-Za-z0-9._-]+$ ]] || {
	printf '%s\n' 'DEPLOY_HOST must be a DNS hostname or IPv4 address.' >&2
	exit 1
}
deploy_user=${DEPLOY_USER:-root}
[[ $deploy_user =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || {
	printf '%s\n' 'DEPLOY_USER contains unsupported characters.' >&2
	exit 1
}
if [[ -n ${DEPLOY_SSH_KEY:-} && ! -r $DEPLOY_SSH_KEY ]]; then
	printf '%s\n' 'DEPLOY_SSH_KEY does not name a readable file.' >&2
	exit 1
fi
for command_name in ssh rsync; do
	command -v "$command_name" >/dev/null || {
		printf 'Missing local command: %s\n' "$command_name" >&2
		exit 1
	}
done

ssh_options=(-o BatchMode=yes -o ConnectTimeout=15)
if [[ -n ${DEPLOY_SSH_KEY:-} ]]; then
	ssh_options+=(-i "$DEPLOY_SSH_KEY")
fi
ssh_command=$(printf '%q ' ssh "${ssh_options[@]}")
target="$deploy_user@$DEPLOY_HOST"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
stage="/opt/caddy/.staging/$stamp"

printf 'Checking %s...\n' "$target"
ssh "${ssh_options[@]}" "$target" \
	"command -v flock >/dev/null && command -v docker >/dev/null && command -v curl >/dev/null && docker compose version >/dev/null && docker volume inspect zibs_caddy-data zibs_caddy-config >/dev/null && test -d /opt/art-gallery/public && install -d -m 0750 /opt/caddy/.staging && mkdir -m 0750 '$stage'"

if [[ $mode != caddy ]]; then
 [[ ${#grafana_admin_password} -ge 20 && $grafana_admin_password != *$'\n'* && $grafana_admin_password != *$'\r'* && $grafana_admin_password != *"'"* ]] || {
  printf '%s\n' "Set a single-line GRAFANA_ADMIN_PASSWORD of at least 20 characters without an apostrophe in .env.production." >&2
  exit 1
 }
 ssh "${ssh_options[@]}" "$target" "command -v python3 >/dev/null"
fi
printf 'Staging %s configuration on %s...\n' "$mode" "$target"
# Include the full payload for validation and rollback helpers, but activate
# only the explicitly selected services. Never upload local secret files.
rsync -aR -e "$ssh_command" \
 Caddyfile Dockerfile compose.yaml compose.observability.yaml observability/ scripts/ \
 "$target:$stage/"

if [[ $mode != caddy ]]; then
 # Single quotes keep $, #, backslashes, and double quotes literal in Compose .env.
 printf "GRAFANA_ADMIN_PASSWORD='%s'\n" "$grafana_admin_password" | \
  ssh "${ssh_options[@]}" "$target" "umask 077; cat > '$stage/.env'"
 unset grafana_admin_password
fi

# Serialize all deployment modes on the VPS. Full activation is intentionally
# staged: healthy monitoring can remain if the Caddy phase rolls back.
# Remove the staged password even when the lock is refused before activation.
ssh "${ssh_options[@]}" "$target" \
 "flock -n /opt/caddy/.deploy.lock bash '$stage/scripts/activate-mode.sh' '$stamp' '$mode' </dev/null; rc=\$?; rm -f '$stage/.env'; exit \$rc"
printf '%s deployment succeeded. Rollback stamp: %s\n' "$mode" "$stamp"
