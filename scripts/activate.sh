#!/usr/bin/env bash
# Run on the VPS by deploy.sh. The only argument is its generated UTC stamp.
set -euo pipefail

stamp=${1:?Missing deployment stamp}
[[ $stamp =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 1
live=/opt/caddy
stage="$live/.staging/$stamp"
backup="$live/rollback/$stamp"
image=caddy-hooklook:2.11.4-ratelimit
rollback_image="caddy-hooklook:rollback-$stamp"
had_running=0
activated=0

restore() {
	printf '%s\n' 'Deployment failed; restoring the prior Caddy image and any changed live files.' >&2
	if (( had_running == 1 )); then
		docker image tag "$rollback_image" "$image" || true
	fi
	if (( activated == 1 )); then
		for file in Caddyfile Dockerfile compose.yaml; do
			if [[ -f "$backup/$file" ]]; then cp "$backup/$file" "$live/$file"; fi
		done
	fi
	if (( activated == 1 && had_running == 1 )); then
		(cd "$live" && docker compose up -d --no-deps --force-recreate caddy) || true
	fi
}
trap restore ERR

for file in Caddyfile Dockerfile compose.yaml; do
	[[ -f "$stage/$file" ]] || { printf 'Missing staged %s\n' "$file" >&2; exit 1; }
done
[[ -f "$stage/scripts/verify.sh" ]] || { printf '%s\n' 'Missing staged verify.sh' >&2; exit 1; }
install -d -m 0750 "$backup"
for file in Caddyfile Dockerfile compose.yaml; do
	if [[ -f "$live/$file" ]]; then cp "$live/$file" "$backup/$file"; fi
done
if [[ -f "$live/compose.yaml" ]]; then
	(cd "$live" && docker compose config) > "$backup/compose.resolved.yaml"
	current_id=$(cd "$live" && docker compose ps -q caddy)
	if [[ -n $current_id ]]; then
		previous_id=$(docker inspect --format '{{.Image}}' "$current_id")
		docker image tag "$previous_id" "$rollback_image"
		printf '%s\n' "$previous_id" > "$backup/previous-image-id"
		had_running=1
	fi
fi

cd "$stage"
docker compose config --quiet
docker compose build caddy
docker compose run -T --rm --no-deps caddy caddy list-modules </dev/null | grep -x 'http.handlers.rate_limit' >/dev/null
docker compose run -T --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null

activated=1
for file in Caddyfile Dockerfile compose.yaml; do cp "$stage/$file" "$live/$file"; done
cd "$live"
docker compose up -d --no-deps --force-recreate caddy
bash "$stage/scripts/verify.sh"
activated=0
printf 'Rollback files: %s\n' "$backup"
