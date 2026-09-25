# Platform observability runbook

Implementation is prepared locally; VPS deployment and verification are
user-run. Keep the current Caddy deployment at `/opt/caddy`, Compose project
`caddy`. Zibs and Hooklook retain all their own observability services.

## Services and limits

| Service | Image | State / exposure |
| --- | --- | --- |
| `platform-node` | `prom/node-exporter:v1.12.1` | Read-only host mounts; no published port; 128 MiB memory limit |
| `platform-prometheus` | `prom/prometheus:v3.5.5` | `caddy_platform-prometheus-data`; no published port; 512 MiB memory limit |
| `platform-grafana` | `grafana/grafana:12.4.10` | `caddy_platform-grafana-data`; `127.0.0.1:3002`; 512 MiB memory limit |

These images were age-checked on 2026-09-25 against upstream releases:
[node_exporter 2026-07-14](https://github.com/prometheus/node_exporter/releases/tag/v1.12.1),
[Prometheus 2026-07-09](https://github.com/prometheus/prometheus/releases/tag/v3.5.5),
and [Grafana 2026-09-02](https://github.com/grafana/grafana/releases/tag/v12.4.10).
All exceed the three-week minimum. Repeat the age check when changing a pin.

Prometheus scrapes `node` and `caddy` every 15 seconds, retaining up to 7 days
or 1 GB of blocks, whichever limit is reached first. WAL, head chunks,
compaction, images, and update snapshots require additional disk space. Keep the
application's 12 GB storage allowance plus at least 3 GB platform headroom;
refresh capacity measurements before deploying. Docker logs rotate at 3 ×
10 MB per new service. No access-log pipeline, Loki, or Alloy is added.

`host-observability` is internal and connects the three monitoring services.
`platform-metrics` is internal and connects Prometheus to Caddy. Grafana also
joins `grafana-access`, a non-internal bridge needed for loopback publication.
Neither Prometheus nor node_exporter joins either application's edge network.
Caddy's `:9180` listener also exists on its edge interfaces: trusted containers
on those networks can read metrics. It is not an admin API or a public route.

## 1. Preflight — run on the VPS

Use root or an account with Docker and `/opt/caddy` access. Docker Compose,
Python 3, `curl`, and `flock` must already be installed.

```bash
cd /opt/caddy
docker compose ps
docker version --format '{{.Server.Version}}'
docker compose version
python3 --version
command -v flock
free -m
df -h /
findmnt -no PROPAGATION /
docker system df
ss -lntp | grep -E ':(3002|9090|9100|9180|2019)\b' || true
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker volume ls
docker network ls
```

Port 3002 must be free. Inspect retained experimental resources; these new
`platform-*` service/volume names deliberately avoid them. First installation
refuses to adopt an existing platform data volume without a live observability
Compose file. Do not delete old data to bypass that check. Review its origin
and choose an explicit recovery action instead.

The Linux host root must support the `rslave` bind used by node_exporter.
Docker Desktop integration tests substitute a fixture root because its old VM
mounts differ; those tests cannot establish real VPS filesystem identity.

Run the existing [ingress checks](deployment-runbook.md#verify-and-diagnose)
and verify the exact existing public Zibs dashboard link before deployment.
Record the results. Check external reachability from your workstation too.
Send the preflight output for review before proceeding.

## 2. Set the independent Grafana password — workstation

Copy `.env.production.example` to the ignored `.env.production` and set
`DEPLOY_HOST` and a new `GRAFANA_ADMIN_PASSWORD` of at least 20 characters.
Save the password in your password manager. The file is sourced by Bash, so
quote a password containing shell metacharacters. Use a password without an
apostrophe so the deployment script can preserve it in Compose's `.env` syntax.
The deployment script sends the password over SSH to a mode-0600 `.env` file
in its VPS staging directory; activation installs it as `/opt/caddy/.env` and
then deletes the staged copy, even on failure. It never copies the local
`.env.production` file. The password is passed to Grafana through its
`GF_SECURITY_ADMIN_PASSWORD` environment variable, matching the Zibs pattern.
The remote `.env` and each update snapshot's copy of it contain the password;
restrict access. Do not print either file in shared output.

The username is `admin`. This password bootstraps an empty Grafana database.
Changing `.env.production` and deploying again does not change an existing
account password. To rotate, change the password in Grafana first, then update
the local file to match before deploying; the verifier authenticates with the
deployed value. Never copy either application's database or credentials.

## 3. Deploy — run locally, one stage at a time

Configure the ignored `.env.production` as above. The
following commands upload files and invoke activation on the VPS; you run them.

```bash
./scripts/deploy.sh observability
```

This validates the candidate, starts the platform services, and checks host
metrics, Grafana authentication, provisioning, and the datasource. Caddy is
not reconciled. Its scrape will fail until the next stage; that is expected.
Review the results, then enable its metrics listener and network attachment:

```bash
./scripts/deploy.sh caddy
```

Caddy is recreated, so a brief ingress interruption is possible. Routes,
certificate volumes, and rate/body limits are preserved. The private-Grafana
404 rule now uses a matched `handle` so it executes before the Zibs fallback.

For subsequent combined updates, `./scripts/deploy.sh full` runs observability,
then Caddy, then complete platform verification. This is a staged deployment,
not an atomic transaction: a failed Caddy phase restores Caddy and leaves the
healthy monitoring phase in place. A final combined-verification failure leaves
services running for diagnosis and explicit rollback.

The deployment lock prevents concurrent modes. Only selected services are
recreated. No orphan removal or project-wide `down` is used. On monitoring
updates, cold Grafana and Prometheus snapshots precede activation; monitoring
pauses during that backup while ingress stays up. Failed monitoring activation
restores its prior configuration and both snapshots. First-install rollback
stops the new services and retains their files and volumes for inspection.

## 4. Verify — VPS, then workstation

On the VPS after both stages:

```bash
cd /opt/caddy
bash scripts/verify.sh
python3 scripts/verify-observability.py
docker compose -f compose.observability.yaml ps
docker stats --no-stream
```

Expect both scrape jobs up, datasource health OK, two dashboards provisioned,
login required, host CPU/memory/load/root/disk/network series present, bounded
Caddy labels, and only loopback Grafana publication. Compare root filesystem
capacity against `df -B1 /` and network device names against `ip -br link`.
The verifier compares root capacity with host `statvfs` and interface names
with `/proc/1/net/dev`. node_exporter disables the netlink netdev collector and
mounts `/proc/1/net` at its procfs `net` path, avoiding the container namespace.
Only CPU, memory, load, filesystem, disk, network, and uname collectors are enabled.

From the workstation, use your actual VPS host and SSH identity:

```bash
ssh -N -L 3002:127.0.0.1:3002 root@YOUR_VPS
```

Open `http://localhost:3002`, log in with the independent admin credentials,
and view **Hetzner-One → Host** and **Hetzner-One → Caddy**. Wait a minute for
rate panels. Quiet status codes may have no series until observed. Dashboard
UIDs are `hetzner-host` and `hetzner-caddy`; datasource UID is
`hetzner-prometheus`.

From another terminal on the workstation, direct connections to
`http://YOUR_VPS:3002/api/health`, `:9090/-/ready`, `:9100/metrics`,
`:9180/metrics`, and `:2019/config/` must fail. Test the public hostnames over
HTTPS as in the ingress runbook and use the saved exact Zibs dashboard URL,
including its query results. No public path may return the platform Prometheus
metrics or Grafana workspace. A private scrape endpoint is reachable by trusted
edge-network containers; the admin API must remain container-loopback only.

Public routes must still succeed (expect `200`):

```bash
for u in \
  https://zibs.app/ \
  https://art-gallery.dinubarbu.com/ \
  https://art-gallery.dinubarbu.com/drawings/ \
  https://hooklook.app/health
do
  printf '%s  %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "$u")" "$u"
done
```

Private Grafana workspace paths on `zibs.app` must return `404`:

```bash
for p in /login /dashboards /api/datasources/ /api/v1/ /grafana /rules
do
  printf '%s  %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "https://zibs.app$p")" "$p"
done
```

No public hostname may serve platform metrics or health (expect `clean`):

```bash
for h in zibs.app hooklook.app art-gallery.dinubarbu.com
do
  for p in /metrics /-/ready /api/health
  do
    body=$(curl -s --max-time 10 "https://$h$p")
    if printf '%s' "$body" | grep -qE 'caddy_http_|node_cpu_seconds|prometheus_tsdb|"database": *"ok"'
    then r=LEAK
    else r=clean
    fi
    printf '%-6s %s%s\n' "$r" "$h" "$p"
  done
done
```

Then open the saved Zibs public dashboard URL in a private window, without the
tunnel, and confirm its panels show query results.

Caddy dashboards select `handler="subroute"` from the pinned build, once per
matched HTTPS route. Status labels come from histogram counts, not
`requests_total`. Local tests validate Caddy-produced 413/429 and show that
431 is rejected before these metrics. The 413/429 panels count HTTP responses,
including possible upstream responses; they are not per-zone rejection counts.
The verifier checks sensitive label names on `caddy_*` series; the Go runtime's
`go_build_info.path` names a module and is unrelated to request paths.
The upstream-state gauge has no active health check configured and cannot prove
availability. Public HTTPS checks provide that independent signal.

Do not run bursts or large uploads during routine deploys. If confirming
boundaries on production, run the existing Hooklook public verifier once from
a trusted workstation using its app runbook. Coordinate rate-limit tests to
avoid throttling your own session. Report the results before rollout acceptance.

## Dashboard changes and diagnostics

Edit JSON in `observability/grafana/dashboards/`, validate it locally, then run
`./scripts/deploy.sh observability`. Provisioning is authoritative; UI saves are
disabled. This operation restarts only the platform stack, not Caddy or apps.

```bash
cd /opt/caddy
docker compose -f compose.observability.yaml logs --tail=100
python3 scripts/verify-observability.py --host-only
docker compose exec caddy wget -qO- http://127.0.0.1:9180/metrics
```

If node is up but host filesystem metrics are wrong, inspect its mounts and
path flags before accepting the dashboard. If Caddy is down as a scrape target,
check its metrics listener and shared `caddy_platform-metrics` membership.
Check container `OOMKilled` and restart counts if memory limits are reached;
measure usage before adjusting limits.

## State and rollback — VPS

The platform stack is disposable; there is no standalone or off-host backup.
Dashboards, the datasource, and Prometheus configuration are provisioned from
this repository, and UI saves are disabled. Grafana's volume holds little more
than the admin account, which bootstraps from `GRAFANA_ADMIN_PASSWORD` on an
empty database. Prometheus holds at most 7 days of metrics. Losing both volumes
costs a metrics gap and a redeploy, nothing else.

Each observability update on an existing installation takes cold snapshots of
the configuration, `.env`, and both volumes under the printed
`rollback/<stamp>/observability` directory. They exist so an image upgrade that
migrates Grafana's SQLite schema or the TSDB can be undone. Keep the pinned
images through the rollback window. Prune old snapshots only after accepting
deployments; these scripts never delete them.

To undo an observability update:

```bash
cd /opt/caddy
stamp=YYYYMMDDTHHMMSSZ
flock -n /opt/caddy/.deploy.lock bash scripts/rollback-observability.sh "$stamp"
python3 scripts/verify-observability.py
```

Rollback stops only platform services, archives the current failed data, and
restores the cold snapshots and config. Metrics collected after the snapshot
are absent from the restored TSDB but retained in the failed-state archive.
The snapshot also restores the matching `.env`; reconcile the local
`.env.production` before the next deployment if the password changed.

If a platform volume is lost or unrecoverable, remove only its service's
container and volume, then redeploy from the workstation. For Grafana:

```bash
cd /opt/caddy
docker compose -f compose.observability.yaml rm -sf platform-grafana
docker volume rm caddy_platform-grafana-data
```

Then run `./scripts/deploy.sh observability`. Grafana re-bootstraps the admin
account from the deployed password. Use `platform-prometheus` and
`caddy_platform-prometheus-data` for Prometheus.

For Caddy rollback use the [existing procedure](deployment-runbook.md#roll-back-after-a-successful-deployment).
Restoring a pre-observability Caddy removes its metrics listener/attachment,
leaving a failed Caddy scrape until re-enabled; platform host metrics can remain.
Preserve both app edge networks and external certificate volumes. Never run
`docker compose down -v`.

## Local validation

With already age-checked images and Go available:

```bash
python3 tests/integration.py
python3 tests/deployment.py
```

The integration test uses isolated local Docker resources, a mock upstream, and
random loopback ports. It checks rejection boundaries, private/public routing,
metrics labels, Grafana authentication, datasource health, all dashboard query
syntax, state persistence, and Caddy independence during monitoring restarts.
It does not exercise public TLS/ACME or prove VPS-specific filesystem identity.
