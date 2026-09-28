#!/usr/bin/env bash
# Run on the VPS by activate-mode.sh. Arguments: the generated UTC stamp and an
# immutable GHCR image (ghcr.io/<owner>/<name>@sha256:<digest>). Without an
# image, the currently deployed one is kept. With GHCR_USER set, a short-lived
# registry token arrives on stdin and is used only for this pull.
set -euo pipefail

stamp=${1:?Missing deployment stamp}
image=${2:-}
[[ $stamp =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 1
live=/opt/caddy
stage="$live/.staging/$stamp"
backup="$live/rollback/$stamp"
# Written here, merged by Compose into compose.yaml: pins the image digest.
override=compose.override.yaml
# The compose.yaml tag, used only by host-built installs that predate GHCR.
base_image=caddy-hooklook:2.11.4-ratelimit
rollback_image="caddy-hooklook:rollback-$stamp"
had_running=0
activated=0

die() {
	printf '%s\n' "$*" >&2
	exit 1
}

token=
if [[ -n ${GHCR_USER:-} ]]; then
	IFS= read -r token || true
	[[ -n $token ]] || die 'GHCR_USER is set, but no registry token arrived on stdin.'
fi

if [[ -z $image && -f "$live/$override" ]]; then
	image=$(sed -n 's/^    image: //p' "$live/$override" | tail -n 1)
fi
[[ -n $image ]] || die 'No image given and none deployed yet; deploy from GitHub first.'
[[ $image =~ ^ghcr\.io/[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_./-]*@sha256:[0-9a-f]{64}$ ]] ||
	die "Image must be ghcr.io/<owner>/<name>@sha256:<digest>, not $image."

restore() {
	printf '%s\n' 'Deployment failed; restoring the prior Caddy image and any changed live files.' >&2
	if (( activated == 1 )); then
		for file in Caddyfile Dockerfile compose.yaml; do
			if [[ -f "$backup/$file" ]]; then cp "$backup/$file" "$live/$file"; fi
		done
		if [[ -f "$backup/$override" ]]; then
			cp "$backup/$override" "$live/$override"
		else
			# The previous install was host-built: return to its local tag.
			rm -f "$live/$override"
			if (( had_running == 1 )); then docker image tag "$rollback_image" "$base_image" || true; fi
		fi
		if (( had_running == 1 )); then
			(cd "$live" && docker compose up -d --no-deps --force-recreate caddy) || true
		fi
	fi
}
trap restore ERR

# Digests are immutable, so a present image needs no registry access.
pull_image() {
	local docker_config status=0
	if docker image inspect "$image" >/dev/null 2>&1; then return 0; fi
	if [[ -z $token ]]; then
		docker pull --quiet "$image" >/dev/null
		return
	fi
	docker_config=$(mktemp -d)
	printf '%s\n' "$token" | DOCKER_CONFIG=$docker_config docker login ghcr.io --username "$GHCR_USER" --password-stdin >/dev/null &&
		DOCKER_CONFIG=$docker_config docker pull --quiet "$image" >/dev/null || status=$?
	rm -rf "$docker_config"
	return "$status"
}

for file in Caddyfile compose.yaml scripts/verify.sh; do
	[[ -f "$stage/$file" ]] || die "Missing staged $file"
done
install -d -m 0750 "$backup"
for file in Caddyfile Dockerfile compose.yaml "$override"; do
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

printf 'Using %s\n' "$image"
pull_image
token=
printf '# Written by scripts/activate.sh; the image of deployment %s.\nservices:\n  caddy:\n    image: %s\n' \
	"$stamp" "$image" > "$stage/$override"
cd "$stage"
docker compose config --quiet
docker run --rm --network none "$image" caddy list-modules </dev/null | grep -x 'http.handlers.rate_limit' >/dev/null
docker run --rm --network none -v "$stage/Caddyfile:/etc/caddy/Caddyfile:ro" "$image" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null

activated=1
for file in Caddyfile compose.yaml "$override"; do cp "$stage/$file" "$live/$file"; done
cd "$live"
docker compose up -d --no-deps --force-recreate caddy
bash "$stage/scripts/verify.sh"
install -d "$live/scripts"
cp "$stage/scripts/verify.sh" "$live/scripts/verify.sh"
activated=0
# The VPS no longer builds Caddy; the backup keeps the last Dockerfile.
rm -f "$live/Dockerfile"
printf 'Rollback files: %s\n' "$backup"
