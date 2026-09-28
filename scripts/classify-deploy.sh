#!/usr/bin/env bash

# Choose the production deployment mode for every change between the last
# verified deployment and a target commit. Prints exactly one of: none, caddy,
# observability, full. Exits non-zero, printing nothing on stdout, when the
# target cannot be verified or Git inspection fails; callers must then make no
# production change.
#
# Usage: classify-deploy.sh <deployed-sha-or-empty> <target-sha>

set -euo pipefail

die() {
	printf '%s\n' "$*" >&2
	exit 2
}

[[ $# -eq 2 ]] || die 'Usage: classify-deploy.sh <deployed-sha-or-empty> <target-sha>'
deployed=$1
target=$2

[[ $target =~ ^[0-9a-f]{40}$ ]] || die 'Target must be a full commit SHA.'
git cat-file -e "$target^{commit}" 2>/dev/null || die "Target commit is not available: $target"

# Without a trustworthy baseline, never guess a narrower mode.
if [[ ! $deployed =~ ^[0-9a-f]{40}$ ]] ||
	! git cat-file -e "$deployed^{commit}" 2>/dev/null ||
	! git merge-base --is-ancestor "$deployed" "$target" 2>/dev/null; then
	printf '%s\n' full
	exit 0
fi

# --no-renames reports both sides of a rename, so a moved file is classified
# by its old and its new path.
changed=$(git diff --name-only --no-renames "$deployed" "$target") || die 'git diff failed.'

# Only files that change what the services run count. Deployment machinery
# (scripts, the workflow, tests, docs) reaches the VPS with the next
# deployment. Keep these patterns in step with the workflow's push paths.
caddy=false
observability=false
while IFS= read -r path; do
	case $path in
	Caddyfile | Dockerfile | compose.yaml) caddy=true ;;
	compose.observability.yaml | observability/*) observability=true ;;
	esac
done <<<"$changed"

if $caddy && $observability; then
	printf '%s\n' full
elif $caddy; then
	printf '%s\n' caddy
elif $observability; then
	printf '%s\n' observability
else
	printf '%s\n' none
fi
