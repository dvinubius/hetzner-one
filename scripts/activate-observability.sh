#!/usr/bin/env bash
set -euo pipefail
stamp=${1:?Missing stamp}
[[ $stamp =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 2
live=/opt/caddy
stage="$live/.staging/$stamp"
backup="$live/rollback/$stamp/observability"
services=(platform-node platform-prometheus platform-grafana)
compose=(docker compose -f compose.observability.yaml)
install -d -m 0700 "$(dirname "$backup")"
mkdir -m 0700 "$backup"
[[ -s "$stage/.env" ]] || { echo 'Staged Grafana environment is missing.' >&2; exit 1; }
cd "$stage"
"${compose[@]}" config --quiet
"${compose[@]}" pull "${services[@]}"
docker run --rm --network none --entrypoint promtool -v "$stage/observability/prometheus:/etc/prometheus:ro" prom/prometheus:v3.5.5 check config /etc/prometheus/prometheus.yml

# Refuse to adopt unknown state during first installation.
if [[ ! -f "$live/compose.observability.yaml" ]]; then
 for volume in caddy_platform-prometheus-data caddy_platform-grafana-data; do
  if docker volume inspect "$volume" >/dev/null 2>&1; then
   printf 'Existing %s: inspect its history before adopting it. See runbook.\n' "$volume" >&2
   exit 1
  fi
 done
 for service in "${services[@]}"; do
  [[ -z $(docker ps -aq --filter label=com.docker.compose.project=caddy --filter "label=com.docker.compose.service=$service") ]] || {
   printf 'Existing %s container without a live Compose file. Inspect before proceeding.\n' "$service" >&2; exit 1;
  }
 done
fi

# All candidate validation precedes changes to running services.
if [[ -f "$live/compose.observability.yaml" ]]; then
 cp "$live/compose.observability.yaml" "$backup/"
 cp -a "$live/observability" "$backup/"
 [[ -s "$live/.env" ]] || { echo 'Live Grafana environment is missing.' >&2; exit 1; }
 install -m 0600 "$live/.env" "$backup/.env"
 cd "$live"
 # Cold snapshots make image rollback safe for both SQLite and the TSDB.
 # If snapshot creation fails, restart the existing monitoring services.
 trap '"${compose[@]}" start platform-prometheus platform-grafana' ERR
 "${compose[@]}" stop platform-prometheus platform-grafana
 for kind in prometheus grafana; do
  volume="caddy_platform-$kind-data"
  if docker volume inspect "$volume" >/dev/null 2>&1; then
   docker run --rm --network none --user 0 --entrypoint tar \
    -v "$volume:/data:ro" -v "$backup:/backup" grafana/grafana:12.4.10 \
    -czf "/backup/$kind-data.tgz" -C /data .
  fi
 done
 trap - ERR
fi

restore() {
 trap - ERR
 echo 'Observability activation failed; restoring previous platform state.' >&2
 bash "$stage/scripts/rollback-observability.sh" "$stamp" || echo "Rollback failed; inspect $backup and the runbook." >&2
}
trap restore ERR
cp "$stage/compose.observability.yaml" "$live/compose.observability.yaml"
install -m 0600 "$stage/.env" "$live/.env"
# Replace the tree so removed dashboards/provisioning files do not linger.
rm -rf "$live/observability"
cp -a "$stage/observability" "$live/observability"
install -d "$live/scripts"
cp "$stage/scripts/verify-observability.py" "$stage/scripts/rollback-observability.sh" "$live/scripts/"
cd "$live"
"${compose[@]}" up -d --no-deps --force-recreate "${services[@]}"
python3 "$live/scripts/verify-observability.py" --host-only
trap - ERR
printf 'Observability activated; complete Caddy verification separately. Backup: %s\n' "$backup"
