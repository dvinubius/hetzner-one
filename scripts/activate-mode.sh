#!/usr/bin/env bash
# VPS entry point, serialized by deploy.sh's flock.
set -euo pipefail
stamp=${1:?Missing stamp}
mode=${2:?Missing mode}
[[ $stamp =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 2
case "$mode" in caddy|observability|full) ;; *) exit 2 ;; esac
stage="/opt/caddy/.staging/$stamp"
# The staged password is only needed during activation; the live .env and any
# update snapshot keep their own copies.
trap 'rm -f "$stage/.env"' EXIT
if [[ $mode != caddy ]]; then
 bash "$stage/scripts/activate-observability.sh" "$stamp"
fi
if [[ $mode != observability ]]; then
 bash "$stage/scripts/activate.sh" "$stamp"
fi
if [[ $mode == full ]]; then
 python3 "$stage/scripts/verify-observability.py"
fi
