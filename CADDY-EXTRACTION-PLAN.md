# Extract Caddy into shared VPS infrastructure

## Goal

Move the VPS's public Caddy instance out of `/opt/zibs` and into a dedicated
Git repository checked out at `/opt/caddy`. Caddy remains the only process that
publishes TCP 80 and 443 (and UDP 443) and becomes the explicit shared ingress
for `zibs.app`, `art-gallery.dinubarbu.com`, and future applications.

This is a migration plan only. It intentionally does not change the running
deployment.

## Current state

- `/opt/zibs/compose.yaml` owns the `caddy` service, the host's public ports,
  Caddy's certificate/config volumes, and `/opt/zibs/Caddyfile`.
- `zibs.app` is reverse-proxied to the `zibs` service on the `zibs_app-edge`
  Docker network.
- The checked-in zibs Caddyfile currently contains the `zibs.app` route,
  including its deliberately narrow public-Grafana allowlist. Capture the
  deployed `/opt/zibs/Caddyfile` before cutover and reconcile it with this
  source copy: the planned `art-gallery.dinubarbu.com` route is not present in
  the supplied source file. If it is live, preserve its read-only
  `/opt/art-gallery/public` → `/srv/art-gallery` mount and `root`, `encode`,
  and `file_server browse` behavior in the new repository.
- The existing Caddy Docker volumes contain the valid certificates and must be
  retained during the handoff.
- The source deployment script's `DEPLOY_ALL=1` path starts and health-checks
  `caddy`; leaving that behavior in place after the extraction would cause a
  port-ownership conflict on a later zibs deployment.

## Target layout

```text
/opt/caddy/                         # new repository working tree
  compose.yaml                       # Caddy alone; owns public ports
  Caddyfile                          # all hostname routing / static sites
  README.md                          # operations and deployment procedure

/opt/zibs/                          # application-only repository
  compose.yaml                       # zibs + observability; no Caddy service
  README.md / deployment docs         # point to /opt/caddy for ingress

/opt/art-gallery/public/            # read-only gallery content mounted by Caddy
```

The Caddy project joins `zibs_app-edge` as an external Docker network. Future
application projects should expose no host ports; Caddy joins each app's
dedicated external backend network and routes to a unique service name.

## New `caddy` repository

1. Create a private Git repository, `caddy-one`, in the
   chosen Git host. Do not place application code or secrets in it.
2. Clone it to `/opt/caddy` and add:
   - `compose.yaml` containing only the pinned Caddy image, public mappings
     `80:80`, `443:443`, and `443:443/udp`, the Caddyfile bind mount, the
     read-only gallery mount, and `restart: unless-stopped`.
   - `Caddyfile` containing the current `zibs.app` route and its narrow public
     Grafana allowlist, plus the gallery's `root`, `encode`, and
     `file_server browse` directives if confirmed in the deployed
     configuration.
   - `README.md` documenting validation, reload, deployment, rollback, and the
     ownership boundary.
3. In the new Compose file, reference the *existing* state as external volumes
   named `zibs_caddy-data` and `zibs_caddy-config`. This preserves the current
   certificates and avoids an unnecessary ACME issuance during migration.
4. Declare `zibs_app-edge` as an external network. Use additional external
   per-app networks as new backend applications are added; avoid putting every
   app on one broad shared network.
5. Commit the initial infrastructure configuration before the live cutover.

The new repository is the owner of every Caddy setting: host ports, TLS state,
hostname routing, Caddy image version, the gallery mount, and Caddy-specific
configuration. Do not leave a second editable Caddyfile or an alternate Caddy
deployment path in zibs.

## Safe cutover

1. Take a VPS backup of `/opt/zibs/Caddyfile`, `/opt/zibs/compose.yaml`, and
   the zibs deployment script. Record `docker volume inspect zibs_caddy-data
   zibs_caddy-config`, `docker network inspect zibs_app-edge`, and the output
   of `docker compose --profile production config`. Treat the deployed
   Caddyfile as the routing source of truth for this migration.
2. Validate the new configuration without touching public traffic:

   ```sh
   cd /opt/caddy
   docker compose config
   docker compose run --rm caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
   ```

3. Confirm the external network and existing certificate volumes are named
   exactly as expected.
4. Stop only the old Caddy service in `/opt/zibs`; then immediately start Caddy
   from `/opt/caddy`. This creates a brief public-web interruption but leaves
   `zibs`, Grafana, Prometheus, Loki, and the gallery files running.
5. Verify:

   ```sh
   docker compose -f /opt/caddy/compose.yaml ps
   curl -I https://zibs.app
   curl -I https://art-gallery.dinubarbu.com
   curl -I https://art-gallery.dinubarbu.com/drawings/
   ```

6. Keep the old `zibs` Caddy configuration and the old service definition in a
   dated backup outside the active `/opt/zibs` working tree until the new stack
   has been stable for an agreed period.

## Clean up `zibs`

After the new Caddy container is verified:

1. Remove the `caddy` service, Caddy-only port mappings, Caddy environment
   variables, and Caddy volume declarations from `/opt/zibs/compose.yaml`.
2. Remove `/opt/zibs/Caddyfile` from the zibs repository. Its contents must
   now live only in `/opt/caddy/Caddyfile`; retain only the dated operational
   backup described above.
3. Change `scripts/deploy.sh` so `DEPLOY_ALL=1` means **zibs plus its
   observability services**, not Caddy. Remove Caddy from its service start,
   expected-running-service checks, and any Caddy-specific validation. The
   application-only deployment remains unchanged. Update the script's messages
   and comments so a future operator cannot infer that zibs deploys ingress.
4. Update the active zibs documentation (not historical `docs/learning/` or
   `docs/explainers/` material) to describe Caddy as separately deployed shared
   infrastructure:
   - `README.md`: revise the overview and diagram so Caddy is outside the zibs
     Compose project and link operators to `/opt/caddy` for ingress changes.
   - `docs/deployment-runbook.md`: remove the claim that `DEPLOY_ALL=1`
     deploys TLS/Caddy; document the two independent deployment commands and
     verify both the loopback health endpoint and the public endpoint.
   - `docs/deployment-architecture.md` and `docs/observability.md`: redraw the
     Caddy boundary as a separate Compose project attached to the external
     `zibs_app-edge` network. Preserve the fact that the narrow public Grafana
     dashboard route still reaches Grafana over that network.
   - `docs/architecture.md`, `docs/v2-deferred-observability.md`, and any
     active ADR/runbook found by a repository-wide Caddy-reference search:
     correct ownership, commands, and cross-repository links without rewriting
     historical records. In particular, retain the current security guarantees:
     `/metrics` is never proxied publicly and normal Grafana workspace/API
     routes remain private.
5. Keep the `zibs_app-edge` network. Declare it by its existing Docker name as
   an external network from Caddy, while zibs continues to own its lifecycle.
   Caddy needs it to reach zibs and the deliberately restricted Grafana
   endpoints; it must not require host-published zibs or Grafana ports.
6. Review observability assumptions: the existing Alloy/Loki pipeline focuses
   on the zibs Compose project. Decide explicitly whether Caddy access/error
   logs remain outside it or move to a host-level/shared collection mechanism;
   do not silently lose or duplicate a log stream.
7. Commit and deploy the zibs cleanup separately from the Caddy cutover. Before
   that deployment, run `docker compose config` and exercise the revised
   `DEPLOY_ALL=1` path in a safe environment. On the VPS, confirm it neither
   creates a `caddy` container nor binds 80/443, then confirm the separately
   managed Caddy container remains serving both public hostnames.

## Rollback

If the `/opt/caddy` service does not start or either hostname fails validation,
stop the new Caddy container, restore the backed-up `/opt/zibs` Compose and
Caddyfile, then start its original Caddy service. Because the certificate
volumes are shared rather than copied, certificate state remains intact.
