#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"

if [[ ! -f .env.production ]]; then
	printf '%s\n' 'Copy .env.production.example to .env.production and set DEPLOY_HOST.' >&2
	exit 1
fi
set -a
# shellcheck disable=SC1091
source .env.production
set +a

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
	"command -v docker >/dev/null && command -v curl >/dev/null && docker compose version >/dev/null && docker volume inspect zibs_caddy-data zibs_caddy-config >/dev/null && test -d /opt/art-gallery/public && install -d -m 0750 '$stage'"

printf 'Staging Caddy configuration on %s...\n' "$target"
rsync -aR -e "$ssh_command" \
	Caddyfile Dockerfile compose.yaml scripts/activate.sh scripts/verify.sh \
	"$target:$stage/"

printf '%s\n' 'Validating, building, and activating Caddy on the VPS...'
ssh "${ssh_options[@]}" "$target" \
	"bash '$stage/scripts/activate.sh' '$stamp' </dev/null"

printf '%s\n' 'Caddy deployment succeeded.'
