#!/usr/bin/env bash
# User-run rollback, also used after failed activation. Never affects Caddy/apps.
set -euo pipefail
stamp=${1:?Usage: rollback-observability.sh YYYYMMDDTHHMMSSZ}
[[ $stamp =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 2
cd /opt/caddy
backup="/opt/caddy/rollback/$stamp/observability"
[[ -d "$backup" ]] || { echo 'Backup not found.' >&2; exit 1; }
compose=(docker compose -f compose.observability.yaml)
services=(platform-node platform-prometheus platform-grafana)
if [[ -f compose.observability.yaml ]]; then
 "${compose[@]}" stop "${services[@]}"
fi
if [[ ! -f "$backup/compose.observability.yaml" ]]; then
 echo 'First-install rollback: platform services stopped; configuration and volumes retained for inspection.'
 exit 0
fi
# Retain failed state before restoring cold snapshots. Only platform volumes.
for kind in prometheus grafana; do
 if [[ -f "$backup/$kind-data.tgz" ]]; then
  docker run --rm --network none --user 0 --entrypoint sh \
   -e "BACKUP_KIND=$kind" -v "caddy_platform-$kind-data:/data" -v "$backup:/backup" grafana/grafana:12.4.10 -ec '
    tar -czf "/backup/$BACKUP_KIND-failed-$(date -u +%Y%m%dT%H%M%SZ).tgz" -C /data .
    find /data -mindepth 1 -maxdepth 1 -exec rm -rf {} \;
    tar -xzf "/backup/$BACKUP_KIND-data.tgz" -C /data
   '
 fi
done
cp "$backup/compose.observability.yaml" compose.observability.yaml
install -m 0600 "$backup/.env" .env
rm -rf observability
cp -a "$backup/observability" observability
"${compose[@]}" up -d --no-deps --force-recreate "${services[@]}"
echo 'Previous platform config and Grafana state restored. Both data stores restored to the snapshot timestamp. Run verification.'
