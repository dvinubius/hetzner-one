# Collector centralization plan

## Status and dependency

Unapproved, unscheduled historical proposal. The current
[host and Caddy observability plan](../.agents/HOST-OBSERVABILITY-PLAN.md)
keeps application Grafana servers and collectors independent. It takes
precedence over this document.

The design below still assumes centralized Grafana and removal of app-owned
collectors. Those assumptions are not accepted decisions: revise ownership,
network topology, dashboard deployment, and migration steps before scheduling
any collector consolidation. This document does not authorize changes to zibs
or Hooklook and is not a prerequisite for platform monitoring.

## Target ownership and topology

| Hetzner-One owns | App projects continue to own |
| --- | --- |
| Prometheus scrape config, one TSDB volume, retention, and alerts if added | Metrics instrumentation and listener configuration |
| Alloy Docker discovery/processing config and its read-position volume | Safe structured stdout and application log rotation |
| Loki config, one log volume, retention, and query endpoint | Application data volumes and services |
| One node_exporter for VM metrics | No host-wide exporter |
| Central Grafana, all data sources and dashboard provisioning | Health/readiness endpoints and app deployment |

Platform Prometheus attaches to stable, private scrape networks that reach the
two application metrics listeners and Caddy's metrics listener. It can initially
reuse `zibs_app-edge` and `hooklook_metrics`/a dedicated Hooklook metrics
attachment, then narrow those networks in a separately verified change. A
multi-network container's listener bound to `0.0.0.0` is reachable on all of
its attached networks; neither `expose` nor an internal Docker network is a
per-port firewall. Do not publish metrics on the host or proxy them through
Caddy. Keep explicit job names `zibs`, `hooklook`, `caddy`, and `node` so current
dashboard `up` queries remain meaningful.

One platform Alloy uses Docker discovery filtered by **both** Compose project
and service for `zibs`, `hooklook`, and `caddy`. It forwards to one platform
Loki with bounded labels such as `service` and `environment`; paths, codes,
IP addresses, request bodies, and secrets stay out of labels. Retain the
different application log policies only where they can be expressed safely;
decide on one default retention (currently zibs Loki: 14 days; Hooklook Loki:
7 days) and document any per-stream exception. Docker socket access is
effectively host-privileged even if mounted read-only; keep Alloy private.

Central Grafana keeps the existing dashboard and datasource **UIDs**. During
the cutover, change their backend URLs from the four app-owned sources and the
interim Caddy Prometheus to the new platform endpoints. Distinct old UIDs may
point to the same new Prometheus or Loki; that preserves dashboard JSON while
the migration is verified. A later cleanup may simplify those UIDs separately.
Keep the zibs public dashboard's saved queries and exact shared URL.

## Data-history decision

The current two Prometheus TSDB volumes and two Loki volumes are separate
histories. Do not assume they can be combined by copying files into one volume.
Choose and document one of these before cutover:

1. **Fresh central history (recommended for this small deployment):** keep old
   collectors and their volumes read-only/available for a bounded retention
   window, start the new stores empty, and record the cutover timestamp on
   dashboards. Historical cross-cutover queries will have a gap or require
   temporary access to the old data sources.
2. **Historical migration:** design and test supported export/import or remote
   read/write procedures for each backend separately. Preserve original
   timestamps and labels and validate representative queries before deleting
   old stores. This is substantially more work and is not implied by this plan.

Do not start duplicate Alloy pipelines forwarding the same container logs to
the same Loki. During parallel verification, keep old and new Loki targets
separate or start central Alloy only at log cutover.

## Preparation

1. Inventory effective Compose project and network names, service labels,
   volume mounts, retention flags, scrape jobs, dashboard UIDs, and available
   RAM/disk. Confirm Hooklook's storage/backup headroom remains satisfied.
2. Set one bounded metrics retention and size cap plus Loki retention and
   volume budget. Add platform-owned backup and restore instructions for
   Grafana runtime state and telemetry volumes. Pin image versions and verify
   their publication ages before pulling or installing them.
3. Add Prometheus, Alloy, and Loki to this repository's Compose and deployment
   workflow, with collector-only update and rollback commands separate from
   Caddy activation. Validate configs in candidate containers without changing
   the live ingress. Give Grafana private connectivity to both new backends.
4. Make app deployment scripts start only the apps; remove collector startup,
   telemetry secrets, dashboard uploads, and collector-specific checks from
   their profiles. Move zibs's node_exporter into this project after the new
   Prometheus has been verified, without briefly removing the `node` signal.
   Keep app-owned `metrics` listeners and log output. Update
   their runbooks to point to Hetzner-One for collection and dashboards.

## Rollout and checks

1. Start platform Prometheus and verify `up=1` for zibs, Hooklook, Caddy, and
   node exporter. Compare representative counters and
   histograms with each old Prometheus. Verify there is no public metrics
   route and no unintended host port.
2. Switch Grafana datasource URLs to the platform Prometheus, preserving UIDs.
   Check private dashboards and the exact zibs public shared URL and query
   results before stopping the old Prometheus containers. Keep their volumes.
3. Start platform Loki and validate ingestion from platform Alloy for each
   service using safe synthetic log events. Stop each old Alloy pipeline before
   sending that service to the central Loki; prevent duplicate delivery.
4. Switch Grafana Loki datasource URLs, preserving UIDs. Verify log panels for
   both apps and Caddy, retention settings, and that no sensitive field or
   high-cardinality label was introduced. Stop the old Loki containers only
   after log queries work; preserve their volumes through the agreed window.
5. Confirm ordinary zibs and Hooklook deployments no longer recreate old
   collector services. Run Caddy, gallery, both app, Grafana, and public-share
   smoke checks after each cutover step.

## Rollback and completion

For a metrics failure, restore Grafana's previous datasource URLs and restart
the retained app Prometheus services; leave central Prometheus data intact.
For a log failure, stop central Alloy first, restart the old Alloy/Loki pairs,
and restore Grafana's old Loki URLs. Preserve all old volumes until rollback is
closed. Avoid changing Caddy's public route or Grafana runtime database in
this phase.

Completion means Hetzner-One deploys, backs up, and checks one Prometheus,
one Alloy, one Loki, and the already-central Grafana; both app projects run
only their services and emit telemetry; Caddy traffic and edge rejections are
visible; and the original zibs public-dashboard URL still works.
