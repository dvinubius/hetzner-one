# Testing strategy

This repository has almost no program of its own. What it ships is
instructions for other programs: the Caddyfile for Caddy, and the Prometheus
configuration and Grafana dashboards for the monitoring stack. Reading such a
file cannot prove what it does; only loading it into the program and sending
it traffic can. The rate-limit handler is not part of standard Caddy, so the
program is the custom build the [Dockerfile](../Dockerfile) compiles, which
exists only as an image. The tests therefore need that image.

All of it runs in [test.yml](../.github/workflows/test.yml), for every pull
request into `main` and inside [deploy.yml](../.github/workflows/deploy.yml).
In a deployment it runs against the exact digest the deployment pulls: the
`image` job before it reuses the published `dockerfile-<blob>` image, or builds
and pushes it when the Dockerfile changed (see the
[deployment runbook](deployment-runbook.md#how-a-push-deploys)). A pull request
publishes nothing: it pulls the same `dockerfile-<blob>` image when it exists,
and otherwise builds it locally for the run. Nothing contacts the VPS before
these tests pass. To run them locally, see
[Local validation](observability-runbook.md#local-validation).

| Layer | What it covers | Needs Docker |
| --- | --- | --- |
| Script syntax | `bash -n` on every script, `py_compile` on the Python files | No |
| [`tests/ci_deploy.py`](../tests/ci_deploy.py) | Change classification, workflow push paths, plan and upload over a fake SSH | No |
| [`tests/deployment.py`](../tests/deployment.py) | Activation and rollback failure paths with a fake Docker CLI | No |
| Compose render | Both Compose files parse | Only the CLI |
| Image checks | The rate-limit module and Caddyfile validation | The Caddy image |
| [`tests/integration.py`](../tests/integration.py) | Live ingress, metrics and monitoring stack | All four images and Go |

The rest of this document covers the last two layers, which run the real
Caddy image.

## Image checks

The workflow runs the same two checks `activate.sh` repeats on the VPS before
it switches Caddy over. Both run with `--network none`.

| Check | What it proves |
| --- | --- |
| `caddy list-modules` includes `http.handlers.rate_limit` | The build really contains the module; stock Caddy fails here. |
| `caddy validate` on the real Caddyfile | It parses, adapts to JSON, and every module it names provisions, so a malformed `rate_limit` zone fails here. |

## Integration test

### How Caddy runs

The test adapts the real Caddyfile to JSON inside the image, then changes only
what a local run cannot provide:

- the admin API is disabled;
- the TLS app and automatic HTTPS are removed, and `:443` becomes plain HTTP
  on `:8080`;
- every upstream dial (`hooklook:8080`, `zibs:8080`, `grafana:3000`) points at
  [`tests/upstream.go`](../tests/upstream.go), a fixture that consumes the
  request body and answers `ok`.

The binary, routes, limits and metrics settings are the production ones.

### Ingress rules

Requests use `Host: hooklook.app` unless noted.

| Request | Expected | Caddyfile rule |
| --- | --- | --- |
| `/health` | 200 | Proxying reaches the upstream. |
| `POST /b/test`, exactly 10,000,000 bytes | 200 | `request_body max_size 10MB` admits the limit. |
| The same, one byte more | 413 | …and rejects one byte over it. |
| 11 × `GET /` | ten 200, then 429 | The `hooklook_create` zone: 10 per minute per client. |
| `/health` with a 40 KB header | 431 | `max_header_size 32KiB`; Go's parser allows about 4 KiB more, so the real boundary is about 36 KiB. |
| `zibs.app/login` | 404 | Private Grafana routes are answered by Caddy and reach no upstream. |
| `zibs.app/public-dashboards/test` | 200 | The public shared-dashboard route is proxied. |

### Metrics listener

- `hooklook.app` counts exactly one 413 and one 429: rejections are recorded
  with the right host and status.
- No 431 is counted. Caddy rejects an oversized header block before any
  handler runs, so no metric records it; the dashboard relies on this.
- Every series carries `handler="subroute"`, the label the dashboard filters
  on.
- The scrape contains neither `rate_limit` nor `key=`: `disable_metrics`
  keeps client IPs out of it.
- `:9180/config/` returns 404: the scrape listener serves only `/metrics`.

### Caddy in the monitoring stack

- Prometheus reports both targets, Caddy and node-exporter, as up: Caddy is
  reachable over `platform-metrics`.
- Every Caddy dashboard query runs with its variables substituted. This checks
  that the PromQL is valid; an empty result passes.
- `caddy_http_request_errors_total`, `caddy_http_response_duration_seconds_bucket`,
  `caddy_http_requests_in_flight` and `process_start_time_seconds` exist for
  the `caddy` job.

### Caddy independence

- Recreating node-exporter, Prometheus and Grafana leaves the Caddy container
  ID unchanged.
- So does the real [`rollback-observability.sh`](../scripts/rollback-observability.sh)
  restore.

The test also uses the Caddy image as the fixture's runtime and to read the
host's `/proc/net/dev` for the network-interface check; neither tests Caddy.

### Why observability-only changes still run the ingress checks

An observability-only deployment leaves the Caddyfile and the image as they
were at the last verified deployment, so the ingress checks cannot change
their result. The run keeps them anyway:

- They are the traffic source. The 413 and 429 requests create the error and
  duration series the monitoring checks then require, so dropping them would
  weaken exactly the checks an observability change needs.
- They are cheap. They take seconds; the run's cost is starting Grafana and
  Prometheus, which an observability change needs anyway, and the image,
  which is reused unless the Dockerfile changed.
- The `test` job does not know the mode. `plan` reads it from the VPS only
  after the tests pass, so that nothing contacts the VPS first. Classifying
  the pushed commit instead would be unsafe: an earlier failed Caddy
  deployment leaves its change pending, and a later observability-only push
  would deploy it with the reduced checks.
- They test the commit, not the diff, so they also catch drift in the
  ingress fixture or the test itself.

An observability change still needs Caddy itself: it is the `caddy` scrape
target and the source of every Caddy dashboard series, and it shares the
`caddy` Compose project, so the independence checks above guard it against
monitoring restarts and rollbacks.

## Not covered

These parts of the Caddy configuration are not exercised before deployment:

- TLS and ACME: certificate issuance, the `:80` to HTTPS redirect, and the real
  `:443` listener.
- `art-gallery.dinubarbu.com`: no request is sent, so `file_server browse` and
  the `/srv/art-gallery` mount are unchecked. The host appears only as a
  dashboard variable value.
- The `/b/*` capture zones: the 60-per-minute and 20-per-20-seconds limits are
  never reached. Only the `GET /` zone is.
- `encode zstd gzip`.
- Upstream behaviour: the fixture always answers 200, so failing upstreams and
  streamed (SSE) responses are not exercised.
- Production wiring: the `hooklook-edge` and `zibs-edge` networks, the
  certificate volumes, and the admin API on `localhost:2019`.

After activation, [`verify.sh`](../scripts/verify.sh) closes part of this gap
on the VPS: it requests `https://zibs.app/`, `https://hooklook.app/health` and
two gallery pages over real TLS and the edge networks, fails on any Caddy
error log since the container started, and the deployment rolls back if it
fails.
