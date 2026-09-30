# Deferred Work for v2

## Public-dashboard edge rate limiting

**Potential change:** rate-limit the narrow Caddy routes that serve the public
Grafana dashboard and its anonymous shared-dashboard API.

**Revisit when:** public traffic or query load warrants a limit.

**Why deferred:** the dashboard is intentionally small, uses saved aggregate
queries, and has a fixed time range. Any future limit must not block required
Grafana assets or public-dashboard API requests.

## Observability

### Alerting

**Potential change:** choose an SMTP or transactional-email sender, keep its
credentials outside the repository, and add alerts for zibs availability,
failed Prometheus scrapes, SQLite busy/locked errors, and low disk space. Add
Caddy-down and unexpected-restart alerts only after their corresponding safe
edge and lifecycle metrics exist.

**Revisit when:** an external sender and credential-storage approach have been
chosen, and delivery, acknowledgement or silencing, and resolved notifications
can be tested end to end.

**Why deferred:** notification delivery is an external operational dependency.
Thresholds must be absolute and appropriate for low traffic, and normal
operation must not create repeated notifications.

Caddy access/error log collection is owned by [Hetzner-One](https://github.com/dvinubius/hetzner-one) as a host-level
shared concern. The zibs Alloy pipeline currently collects no Caddy logs.

#### Caddy-down alert

**Potential change:** add a safe Caddy availability metric and an alert.

**Revisit when:** a safe edge-health metric is available.

**Why deferred:** Caddy does not currently expose a collected safe health
metric. Application-target health alone cannot establish public-edge health.

#### Expanded alert set

**Potential change:** add low-disk, Caddy, and restart alerts beyond the
initial availability, scrape-failure, and SQLite-busy alerts.

**Revisit when:** the required host, edge, and lifecycle metrics exist and
each alert can be tested end to end.

**Why deferred:** every alert needs a dependable signal and a tested delivery
path before it is enabled.