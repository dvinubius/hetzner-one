#!/usr/bin/env bash
# VPS entry point, serialized by the caller's flock (deploy.sh or ci-deploy.sh).
# Usage: activate-mode.sh <stamp> <mode> [commit] [image]
# CI passes the deployed commit; a workstation deployment passes an empty one.
# The Caddy phase deploys the given GHCR image, or keeps the deployed one. With
# GHCR_USER set, a registry token for that image arrives on stdin.
set -euo pipefail
stamp=${1:?Missing stamp}
mode=${2:?Missing mode}
commit=${3:-}
image=${4:-}
[[ $stamp =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 2
case "$mode" in caddy|observability|full) ;; *) exit 2 ;; esac
[[ -z $commit || $commit =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ -z $image || $mode != observability ]] || exit 2
live=/opt/caddy
stage="$live/.staging/$stamp"
state="$live/.deploy"
# The staged password is only needed during activation; the live .env and any
# update snapshot keep their own copies.
trap 'rm -f "$stage/.env"' EXIT
token=
if [[ $mode != observability && -n ${GHCR_USER:-} ]]; then
 IFS= read -r token || true
 [[ -n $token ]] || { echo 'GHCR_USER is set, but no registry token arrived on stdin.' >&2; exit 1; }
fi
install -d -m 0700 "$state"
if [[ -z $commit ]]; then
 # A workstation deployment need not match any commit on main. Forget the
 # verified baseline so the next CI deployment runs in full mode.
 rm -f "$state/manifest"
fi
if [[ $mode != caddy && ! -e "$stage/.env" ]]; then
 # CI never carries the Grafana password; reuse the one the VPS already holds.
 [[ -s "$live/.env" ]] || { echo 'No Grafana environment on the VPS; install observability from a workstation first.' >&2; exit 1; }
 install -m 0600 "$live/.env" "$stage/.env"
fi
if [[ $mode != caddy ]]; then
 bash "$stage/scripts/activate-observability.sh" "$stamp" </dev/null
fi
if [[ $mode != observability ]]; then
 if [[ -n $token ]]; then
  bash "$stage/scripts/activate.sh" "$stamp" "$image" <<<"$token"
 else
  bash "$stage/scripts/activate.sh" "$stamp" "$image" </dev/null
 fi
fi
if [[ $mode == full ]]; then
 python3 "$stage/scripts/verify-observability.py"
fi
if [[ -n $commit ]]; then
 # Written only after every phase passed: the next run classifies all
 # changes since this commit.
 deployed_image=
 if [[ -f "$live/compose.override.yaml" ]]; then
  deployed_image=$(sed -n 's/^    image: //p' "$live/compose.override.yaml" | tail -n 1)
 fi
 (umask 077 && printf 'commit=%s\nmode=%s\nimage=%s\nstamp=%s\ndeployed_at=%s\n' \
  "$commit" "$mode" "$deployed_image" "$stamp" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$state/manifest.tmp")
 mv -f "$state/manifest.tmp" "$state/manifest"
fi
