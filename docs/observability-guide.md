# Using the Caddy dashboard

This guide teaches you to answer real questions with **Hetzner-One → Caddy**.
The [observability runbook](observability-runbook.md) covers deploying,
verifying and restoring the monitoring stack; this document covers reading it.

Open the dashboard through the SSH tunnel described in the runbook
(`ssh -N -L 3002:127.0.0.1:3002 root@YOUR_VPS`, then `http://localhost:3002`).

## 1. What the dashboard can see

Caddy counts every HTTPS request once, at the top-level route of the site that
matched it, labelled with the site (`host`), status code and method. That is
the whole vocabulary. The dashboard tells you **which site** and **what kind
of outcome**, never **which URL** or **which client**; paths and client IPs are
deliberately kept out of the metrics. When you need those, go to the app's own
logs.

Some traffic never reaches these metrics:

- Plain-HTTP requests on port 80 that Caddy redirects to HTTPS.
- Requests rejected while parsing, such as the 431 for headers above about
  36 KiB. Caddy refuses them before any route runs.
- TLS handshakes that never become a request: scanners that connect to 443 and
  leave. They cost CPU (see scenario 8) but add no request.

Prometheus scrapes Caddy every 15 seconds and keeps 7 days. Rate panels use
`$__rate_interval`, which is at least one minute here, so a change needs about
a minute to show and a 10-second blip is smoothed away.

### How each kind of event is recorded

These rows were measured against the pinned Caddy build with a local mock
upstream, not taken from documentation. Keep this table in mind; most
scenarios below are applications of it.

| What happened | Status recorded | Middleware error? | Time-to-first-byte sample? | Response bytes |
| --- | --- | --- | --- | --- |
| App answered normally | App's status | No | Yes | Body, compressed if `encode` applied |
| App returned its own 500 or 404 | 500 / 404 | **No** | Yes | App's body |
| App unreachable: container down, network detached | 502 | **Yes** | No | 0 |
| Hooklook capture body over 10 MB | 413 | **Yes** | No | 0 |
| Hooklook rate limit hit | 429 | **Yes** | No | 0 |
| Missing file on the art gallery | 404 | **Yes** (`file_server` raises it) | No | 0 |
| Zibs private Grafana path | 404 | No (a static `respond`) | Yes | Small |
| Client hung up while the app was still thinking | **200** | No | No | 0 |
| Server-sent event (SSE) stream | 200 | No | Yes, when the stream opens | Streamed bytes |

Three consequences are worth memorising:

1. **Middleware errors separate Caddy's problems from the app's.** An app's own
   500 is a 5xx but not a middleware error. A 502 from a dead upstream is both.
2. **Streams distort request duration.** An SSE request lasts until the client
   leaves, so it lands above the 10-second top bucket. Time to first byte is
   recorded when the stream opens and stays honest.
3. **Client aborts look like successes.** They are 200s with no bytes and no
   time to first byte. If an app is so slow that users give up, the 5xx panels
   stay clean; watch time to first byte instead.

### Reading rules

- **Stat panels cover the whole selected time range.** At "Last 7 days" the
  5xx share is a weekly figure. During an incident, set the range to 15 or 30
  minutes so the stats describe now.
- **Quantiles are interpolated between bucket edges.** Duration buckets are
  5, 10, 25, 50, 100, 250, 500 ms, 1, 2.5, 5 and 10 s. A p95 of 0.3 s means
  "somewhere between 250 and 500 ms". A p95 that sits flat at exactly 10 s
  means more than 5% of requests took longer than 10 s, and Prometheus cannot
  say how much longer. On Hooklook that is usually streams, not slowness.
- **Few requests make noisy quantiles.** With three requests a minute, one slow
  request moves p95 a long way. Widen the time range before believing a spike.
- **"No data" is not zero.** A status code appears only after it has happened
  once. An empty 502 panel means no 502 was seen, not that the panel is broken.
- **Counters reset when Caddy restarts.** `rate()` and `increase()` handle it;
  you will see a gap of one or two scrapes, not a cliff.
- **Heatmap colour is a count per cell,** the number of requests in that time
  slice and bucket. Cells get wider when you zoom out, so the colours
  brighten; compare shapes, not absolute colours, across time ranges.

### The Site selector

**Site** at the top filters every per-site panel, the summary stats and the
heatmaps. Heatmaps cannot split by site, so they only make sense with one site
selected. Scrape availability, upstream state and the Caddy process row are
not per site and ignore it.

## 2. Panel map

| Panel | Question it answers | Normal looks like |
| --- | --- | --- |
| Summary stats | How has the selected period gone overall? | 5xx share green, middleware errors near zero outside Hooklook and gallery 404s |
| Requests by site | Who is receiving traffic, and how much? | Each site's usual daily rhythm |
| Responses by status class | What is the mix of 2xx/3xx/4xx/5xx? | Mostly 2xx; some 3xx and 4xx |
| 4xx and 5xx responses | Which exact error codes, on which site? | Occasional 404s; no sustained 5xx |
| 502 and 504 responses | Is Caddy failing to reach an app? | Empty |
| 413 and 429 responses (5m) | Are Hooklook's limits biting? | Empty or rare |
| Middleware errors by site | Is Caddy itself failing requests? | Near zero, apart from gallery 404s and Hooklook 429s |
| Reverse-proxy upstream state | What does Caddy believe about each upstream? | 1, but see scenario 1: this proves little |
| Caddy scrape availability | Can Prometheus read Caddy at all? | Flat at 1 |
| p95 request duration | How long do whole requests take? | Low; Hooklook may sit at 10 s because of streams |
| p95 time to first byte | How quickly do apps start answering? | Well under a second |
| Data transfer rate | How many bytes go in and out, per site? | Gallery dominates outbound when people browse |
| Requests in flight | What is open right now? | Near zero; Hooklook holds its open streams |
| Distributions row (heatmaps) | What is the full shape behind the averages? | Stable bands |
| Caddy process row | Is Caddy itself healthy as a process? | Flat memory, goroutines tracking load |

## 3. The two-minute check

Do this when you have not looked for a while.

1. Set the time range to **Last 24 hours**, Site = All.
2. Read the stats. Is the 5xx share green? Are middleware errors in line with
   what you usually see? Is the data sent in line with a normal day?
3. Scan **Requests by site** for a flat line where there should be traffic.
4. Scan **502 and 504** and **Middleware errors by site** for bursts. Hover a
   burst to note its time, then use the scenarios below.
5. Glance at **p95 time to first byte** for a site that has drifted upwards.

Write down the typical values the first few times: requests per second per
site at a busy hour, typical 4xx share, typical time to first byte. A
dashboard is only useful against a baseline, and yours will not look like
anyone else's.

## 4. Scenarios

Each scenario starts from something you notice, then walks the panels in an
order that narrows the cause.

### Scenario 1: a site returns errors. Is it the app or the ingress?

Someone reports errors on `zibs.app`, or the 5xx share turned orange.

1. Set Site = `zibs.app` and the range to **Last 30 minutes**.
2. Open **4xx and 5xx responses** and read the codes.
3. Compare with **Middleware errors by site** and **502 and 504 responses**.

| You see | Meaning | Next step |
| --- | --- | --- |
| 500s, middleware errors flat | The app returned the errors itself. Caddy delivered them faithfully. | The app's logs, in its own project |
| 502s, middleware errors rising with them | Caddy cannot connect to the app: container stopped, crashing, or detached from its edge network | Check the app is running, then `docker network inspect zibs-edge` for its membership |
| 504s | Caddy timed out connecting to the app or waiting for its answer | App resource use; Host dashboard CPU and pressure |
| Middleware errors with no matching 5xx | Probably 4xx rejections by Caddy: 429/413 on Hooklook, missing files on the gallery | **413 and 429 responses**, or the 404 series |

Do not trust **Reverse-proxy upstream state** as a health signal. No active
health checks are configured, so it stays at 1 while the app is down. The
502 panel and a public `curl` are the real evidence.

On the VPS, Caddy's own log records the proxy errors with their cause (for
example "connection refused" or "no such host"):

```bash
cd /opt/caddy
docker compose logs --since 30m caddy | grep -i error
```

### Scenario 2: Hooklook feels slow

The p95 request duration for `hooklook.app` looks alarming.

1. Set Site = `hooklook.app`. Compare **p95 request duration** with
   **p95 time to first byte**.
2. If duration is pinned at 10 s but time to first byte is low, nothing is
   slow. More than 5% of requests are streams that stay open. Confirm it in
   **Requests in flight**: Hooklook holds a steady handful of open requests.
3. Open the **Distributions** row. In **Request duration**, the streams form a
   band in the top rows (5 s, 10 s and +Inf), separate from the fast requests
   at the bottom. **Time to first byte** has no such band.
4. Real slowness shows up as the **Time to first byte** mass drifting upwards,
   from the 10–50 ms rows into the 250 ms and higher rows.

If time to first byte really rose:

- **Requests in flight rising at the same time** means requests queue inside
  the app. Look at the app and at **Pressure** and **CPU Basic** on the Host
  dashboard; all three apps share this one machine.
- **Requests by site rising at the same time** means load drives the slowness.
  Scenario 3 tells you whose load it is.
- **Neither rising** means the app got slower on its own: a deployment, a
  database, a background job. That is the app's investigation, not Caddy's.

Remember that users who give up are recorded as 200s without a
time-to-first-byte sample. Heavy slowness can therefore look like a drop in
time-to-first-byte samples rather than a rise in errors.

### Scenario 3: traffic spike. Users, a scanner, or abuse?

**Requests by site** jumps for one site.

Put these panels side by side for the spike window: **Responses by status
class**, **4xx and 5xx responses**, **Data transfer rate**, and the
**Response size** heatmap with that site selected.

| Signature | Likely cause |
| --- | --- |
| 2xx rise in proportion, response sizes in their usual rows, transfer up | Real visitors: a link was shared, a crawler indexed you |
| Mostly 404s, tiny responses (bottom heatmap rows), little outbound transfer | A vulnerability scanner probing paths such as `/wp-login.php` |
| On the gallery, the same scanner signature plus **middleware errors** rising | A scanner hitting missing files; `file_server` counts each 404 as an error |
| On Hooklook, **429s** climbing in **413 and 429 responses** | A client exceeding the per-IP rate limits |
| On Hooklook, inbound transfer up and the **Request size** heatmap filling its top rows | Large capture bodies; see scenario 4 |

Some arithmetic for Hooklook: its capture limit is 60 requests a minute per
IP, with at most 20 in any 20 seconds. Creation (`GET /`) allows 10 a minute.
A capture spike of, say, 5 requests per second with no 429s therefore needs
at least five distinct clients. A spike with many 429s might be just one.

The dashboard deliberately cannot name the client. If you need to, Hooklook
sees the client address through the forwarded headers Caddy adds; use its own
logs.

Scanners are background noise on any public IP. They matter only if they
cause 5xx responses, raise time to first byte, or show up as Caddy CPU
(scenario 8).

### Scenario 4: is Hooklook's upload limit right?

Hooklook captures accept bodies up to 10 MB. Are real senders close to that?

1. Set Site = `hooklook.app`, range **Last 7 days**, and open the
   **Request size** heatmap.
2. The buckets top out at 4 MiB. The top row therefore mixes accepted bodies
   of 4–10 MB with rejected bodies over 10 MB, because Caddy counts the
   declared `Content-Length` even when it rejects the body.
3. **413 and 429 responses** gives the rejected share.

To count accepted captures above 4 MiB, run this in Explore:

```promql
sum(increase(caddy_http_request_size_bytes_count{job="caddy",handler="subroute",host="hooklook.app",code!="413"}[7d]))
- sum(increase(caddy_http_request_size_bytes_bucket{job="caddy",handler="subroute",host="hooklook.app",code!="413",le="4.194304e+06"}[7d]))
```

Prometheus 3 stores bucket edges in a normalised form: `4.194304e+06` is
4 MiB, and `1.0` is one second, not `1`. If a bucket query returns nothing,
list the stored edges with `count by(le)(caddy_http_request_size_bytes_bucket{job="caddy"})`.

Reading the answer: many 413s and few accepted bodies above 4 MiB means the
limit only stops junk. A steady stream of accepted 4–10 MB bodies plus some
413s means real senders are close to the limit, and raising it is worth a
thought. Chunked uploads send no `Content-Length`, so they look smaller than
they are.

### Scenario 5: where does the bandwidth go?

1. Set Site = All and open **Data transfer rate**. Outbound traffic is drawn
   below the axis, inbound above.
2. Expect the art gallery to dominate outbound: images do not compress, and
   `encode` skips them. Zibs and Hooklook responses are mostly compressed
   text.
3. Open the **Response size** heatmap for the gallery. The rows from 256 KiB
   upwards are images. Anything in the top row is over 4 MiB, a candidate for
   resizing.

Compare with **Network Traffic Basic** on the Host dashboard. Host transmit is
always higher than Caddy's outbound, because Caddy counts body bytes only: no
headers, no TLS or HTTP/3 framing, and nothing the host sends for its own
sake (backups, SSH, package downloads). A large and growing gap that is not
explained by Caddy's traffic means something else on the machine is talking.

For a monthly projection from the 7 days Prometheus keeps, run this in
Explore and compare it with your plan's included traffic:

```promql
sum(increase(caddy_http_response_size_bytes_sum{job="caddy",handler="subroute"}[7d])) * 30 / 7
```

### Scenario 6: how long did an app deploy take the site down?

Zibs and Hooklook deploy from their own repositories. Caddy keeps sending
traffic to their container names, so an app deploy that recreates its
container produces 502s until the new one answers.

1. Set the range around the deploy, Site = the app.
2. **502 and 504 responses** shows the outage window as a burst. Its width is
   roughly how long requests failed; with the one-minute rate window, a burst
   narrower than a minute still looks about a minute wide.
3. To count failed requests exactly, use Explore with the deploy window as
   the range:

```promql
sum(increase(caddy_http_request_duration_seconds_count{job="caddy",handler="subroute",host="hooklook.app",code=~"502|504"}[10m]))
```

Failed requests return instantly, and webhook senders retry, so
**Requests by site** can rise during an outage rather than fall. Read the
request rate together with the status codes, never alone.

No 502s is good news only if there was traffic during the deploy. Check
**Requests by site** for that minute; a quiet site cannot show downtime. For
Hooklook, also watch **Requests in flight**: open streams drop to zero when
the app restarts, then return as clients reconnect.

### Scenario 7: after a Caddy deploy

A push that changes the Caddyfile, Dockerfile or `compose.yaml` recreates
Caddy.

1. Open the **Caddy process** row. **Caddy uptime** drops to zero at the
   restart; that is your timestamp.
2. **Caddy scrape availability** may show a scrape or two missing around it.
3. Hooklook's **Requests in flight** falls to zero and recovers as stream
   clients reconnect, often with a small burst in **Requests by site**.
4. Over the next 15 minutes the 5xx share, **Middleware errors by site** and
   **p95 time to first byte** should look as they did before the restart.

A middleware-error step change that starts exactly at the restart points to
the new configuration: a route, matcher or limit that behaves differently.
`scripts/verify.sh` catches logged errors at deploy time; the dashboard
catches problems that only real traffic triggers.

### Scenario 8: is Caddy itself under pressure?

Caddy has no container memory limit, and all three sites go through it.

- **Caddy memory**: a sawtooth (the Go heap growing and being collected) is
  normal. Resident memory climbing steadily over days at a similar traffic
  level is not; it grows with held connections and buffered bodies.
- **Goroutines and open files**: both roughly follow open connections,
  including idle keep-alive connections and open streams. Compare them with
  **Requests in flight**. Goroutines rising while in-flight requests stay flat
  means many idle or slow connections. A slow-drip client can do this, and so
  can many browsers holding HTTP/2 connections open.
- **Caddy CPU**: compare it with **Requests by site**. CPU rising with requests
  is normal. CPU rising without requests usually means TLS handshakes from
  clients that never send a request, such as scanners or broken bots. Those
  handshakes are invisible everywhere else on this dashboard.

For the whole-machine view, use the Host dashboard: **CPU Basic**,
**Memory Basic** and **Pressure**. Caddy competes with the apps for the same
cores.

### Scenario 9: a site has gone quiet

**Requests by site** shows one site flat at zero while the others carry on.

The dashboard only sees traffic that arrives. Zero requests is consistent with
no visitors, a DNS mistake, an expired or failed certificate, or a client-side
outage. Run the public checks from your workstation, as in the runbook:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://zibs.app/
```

If every site goes quiet at once but **Caddy scrape availability** stays at 1,
Caddy is healthy but nothing reaches it: a firewall, port publication or
network problem outside the container. If scrape availability drops too,
start with `docker compose ps` in `/opt/caddy`.

## 5. Explore recipes

Open **Explore** in Grafana's left menu and choose the Hetzner-One Prometheus
data source. Set the range picker to cover the question. These queries answer
questions the panels do not.

Which status codes did each site serve in the last day?

```promql
sort_desc(sum by(host,code)(increase(caddy_http_request_duration_seconds_count{job="caddy",handler="subroute"}[24h])))
```

Which HTTP methods reach each site? Useful for Hooklook, which captures any
method:

```promql
sum by(host,method)(increase(caddy_http_request_duration_seconds_count{job="caddy",handler="subroute"}[24h]))
```

What share of responses started within 250 ms, per site? An SLO-style number
that streams cannot distort:

```promql
sum by(host)(rate(caddy_http_response_duration_seconds_bucket{job="caddy",handler="subroute",le="0.25"}[1h]))
/ sum by(host)(rate(caddy_http_response_duration_seconds_count{job="caddy",handler="subroute"}[1h]))
```

How many times did Caddy restart in the last week?

```promql
changes(process_start_time_seconds{job="caddy"}[7d])
```

Is the scrape size still far from its limit of 20000 samples?

```promql
scrape_samples_scraped{job="caddy"}
```

## 6. Practice drills

These are harmless: a handful of requests, well below any limit. Do not run
bursts or large uploads against production; the runbook explains why.

1. **Find your own request.** Request a missing gallery file three times:

   ```bash
   for i in 1 2 3; do curl -s -o /dev/null -w '%{http_code}\n' https://art-gallery.dinubarbu.com/no-such-file.jpg; done
   ```

   Set Site = the gallery and the range to the last 15 minutes. Within about a
   minute, 404s appear in **4xx and 5xx responses** and the same three appear
   as **Middleware errors**. Now do the same with `https://zibs.app/login`: a
   404 again, but no middleware error. The first table in this guide explains
   the difference.
2. **Watch a stream.** Note Hooklook's **Requests in flight**, then open a
   Hooklook bin page and leave it open for a few minutes. If the count rises
   by one while the page is open, that page holds a live stream. Close it
   and watch the count drop.
3. **Read a heatmap.** With Site = `hooklook.app` and 24 hours selected, find
   the stream band in the **Request duration** heatmap and check it is absent
   from **Time to first byte**.
4. **Estimate a month of traffic** with the scenario 5 query, then check it
   against Host transmit for the same 7 days.

## 7. When to leave this dashboard

| Question | Where |
| --- | --- |
| Which URL, which client, which user? | The app's own logs and observability |
| Is the site reachable from the internet right now? | The public `curl` checks in the runbook |
| Is the machine short of CPU, memory or disk? | **Hetzner-One → Host** |
| Why did Caddy reject or fail a request? | `docker compose logs caddy` in `/opt/caddy` |
| Is a certificate close to expiry? | Not collected. Caddy renews automatically; check with `curl -vI` if in doubt |
