#!/usr/bin/env bash

# GitHub Actions side of production deployment. It reads the VPS deployment
# manifest, chooses a mode from every change since that verified deployment,
# and uploads an allowlisted bundle taken from the exact target commit. The VPS
# then activates it with the same activate-mode.sh a workstation deployment
# uses, pulling the Caddy image that CI published to GHCR. It never builds and
# never gets Git access.
#
# Usage:
#   ci-deploy.sh plan <target-sha> [full]    Print none|caddy|observability|full.
#   ci-deploy.sh deploy <mode> <target-sha> [image]
#
# Both need DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY_FILE, and
# DEPLOY_KNOWN_HOSTS_FILE (a verified host-key entry). A caddy or full deploy
# needs CADDY_IMAGE_REPOSITORY (ghcr.io/<owner>/hetzner-one-caddy) and its
# image digest, and pulls with GHCR_USER/GHCR_PULL_TOKEN when both are set.

set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"

live=/opt/caddy

die() {
	printf '%s\n' "$*" >&2
	exit 1
}

require_target() {
	[[ $1 =~ ^[0-9a-f]{40}$ ]] || die 'Target must be a full commit SHA.'
	git cat-file -e "$1^{commit}" 2>/dev/null || die "Target commit is not available: $1"
}

# Production follows main only. A run whose commit is no longer the head of
# main is stale; the newer run will deploy from the same unchanged baseline.
require_main_head() {
	local head
	head=$(git ls-remote --exit-code origin refs/heads/main | cut -f1) || die 'Could not read the head of main.'
	[[ $head == "$1" ]] || die "Stale run: main is at $head, not $1."
}

ssh_options=()
require_ssh() {
	[[ ${DEPLOY_HOST:-} =~ ^[A-Za-z0-9._-]+$ ]] || die 'DEPLOY_HOST must be a DNS hostname or IPv4 address.'
	[[ ${DEPLOY_USER:-} =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || die 'DEPLOY_USER is missing or contains unsupported characters.'
	[[ -s ${DEPLOY_SSH_KEY_FILE:-} ]] || die 'DEPLOY_SSH_KEY_FILE must name the deployment key.'
	[[ -s ${DEPLOY_KNOWN_HOSTS_FILE:-} ]] || die 'DEPLOY_KNOWN_HOSTS_FILE must hold the verified host key.'
	ssh_options=(
		-i "$DEPLOY_SSH_KEY_FILE"
		-o BatchMode=yes
		-o ConnectTimeout=15
		-o IdentitiesOnly=yes
		-o StrictHostKeyChecking=yes
		-o UserKnownHostsFile="$DEPLOY_KNOWN_HOSTS_FILE"
	)
}

remote() {
	ssh "${ssh_options[@]}" "$DEPLOY_USER@$DEPLOY_HOST" "$@"
}

plan() {
	local target=$1 force=${2:-} manifest deployed
	require_target "$target"
	require_ssh
	# A missing manifest means no verified CI deployment, or a workstation
	# deployment since; an SSH failure stops the run instead of guessing.
	manifest=$(remote "cat '$live/.deploy/manifest' 2>/dev/null || true") ||
		die 'Could not read the deployment manifest over SSH.'
	deployed=$(sed -n 's/^commit=//p' <<<"$manifest" | tail -n 1)
	printf 'Last verified deployment: %s\n' "${deployed:-none}" >&2
	if [[ $force == full ]]; then
		printf '%s\n' full
		return
	fi
	bash scripts/classify-deploy.sh "$deployed" "$target"
}

# The same payload scripts/deploy.sh stages, without the workstation and CI
# entry points. Never includes secrets: the VPS owns /opt/caddy/.env.
build_bundle() {
	git archive --format=tar "$1" -- \
		Caddyfile compose.yaml compose.observability.yaml observability scripts \
		':(exclude)scripts/deploy.sh' \
		':(exclude)scripts/ci-deploy.sh' \
		':(exclude)scripts/classify-deploy.sh'
}

deploy() {
	local mode=$1 target=$2 image=${3:-} stamp stage checks command
	case $mode in
	caddy | full)
		[[ -n ${CADDY_IMAGE_REPOSITORY:-} ]] || die 'CADDY_IMAGE_REPOSITORY is required for a Caddy deployment.'
		[[ $image =~ ^${CADDY_IMAGE_REPOSITORY//./\\.}@sha256:[0-9a-f]{64}$ ]] ||
			die "Image must be $CADDY_IMAGE_REPOSITORY@sha256:<digest>."
		;;
	observability) [[ -z $image ]] || die 'An observability deployment does not take an image.' ;;
	*) die 'Mode must be caddy, observability, or full.' ;;
	esac
	require_target "$target"
	require_ssh
	require_main_head "$target"

	stamp=$(date -u +%Y%m%dT%H%M%SZ)
	stage=$live/.staging/$stamp
	checks='command -v flock >/dev/null && command -v docker >/dev/null && command -v curl >/dev/null && docker compose version >/dev/null && docker volume inspect caddy_caddy-data caddy_caddy-config >/dev/null && test -d /opt/art-gallery/public'
	if [[ $mode != caddy ]]; then
		# Observability reuses the Grafana password already installed on the VPS.
		checks+=" && command -v python3 >/dev/null && test -r '$live/.env'"
	fi
	printf 'Staging %s from %s at %s...\n' "$mode" "$target" "$stage"
	build_bundle "$target" |
		remote "$checks && install -d -m 0750 '$live/.staging' && mkdir -m 0750 '$stage' && tar -x -C '$stage'" ||
		die 'VPS prerequisites or upload failed; production is unchanged.'

	# The same host lock as workstation deployments. The staged bundle is
	# removed afterwards; rollback files live under /opt/caddy/rollback.
	command="flock -n '$live/.deploy.lock' bash '$stage/scripts/activate-mode.sh' '$stamp' '$mode' '$target' '$image'; rc=\$?; rm -rf '$stage'; exit \$rc"
	# The registry token goes on stdin, never in arguments.
	if [[ $mode != observability && -n ${GHCR_USER:-} && -n ${GHCR_PULL_TOKEN:-} ]]; then
		[[ $GHCR_USER =~ ^[A-Za-z0-9-]+(\[bot\])?$ ]] || die 'GHCR_USER is not a GitHub login.'
		remote "GHCR_USER='$GHCR_USER' $command" <<<"$GHCR_PULL_TOKEN"
	else
		remote "$command" </dev/null
	fi
	printf '%s deployment of %s succeeded. Rollback stamp: %s\n' "$mode" "$target" "$stamp"
}

case ${1:-} in
plan)
	[[ $# -ge 2 && $# -le 3 ]] || die 'Usage: ci-deploy.sh plan <target-sha> [full]'
	plan "$2" "${3:-}"
	;;
deploy)
	[[ $# -ge 3 && $# -le 4 ]] || die 'Usage: ci-deploy.sh deploy <mode> <target-sha> [image]'
	deploy "$2" "$3" "${4:-}"
	;;
*) die 'Usage: ci-deploy.sh plan <target-sha> [full] | deploy <mode> <target-sha> [image]' ;;
esac
