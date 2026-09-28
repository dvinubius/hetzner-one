# Node Exporter Full

[`../../dashboards/hetzner-host.json`](../../dashboards/hetzner-host.json) is
[Node Exporter Full](https://github.com/rfmoz/grafana-dashboards#node-exporter-full)
by [rfmoz](https://github.com/rfmoz), licensed under the Apache License 2.0 ([LICENSE](LICENSE)).

Source: `prometheus/node-exporter-full.json` at upstream commit
[`99a25cc154c21c4fcbae1aa24352bf1b6764d847`](https://github.com/rfmoz/grafana-dashboards/blob/99a25cc154c21c4fcbae1aa24352bf1b6764d847/prometheus/node-exporter-full.json)
(committed 2026-09-07, age-checked 2026-09-28).

Hetzner-One changes, and nothing else:

- `uid` is `hetzner-host` and `title` is `Hetzner-One · Host`; the verifier and
  tests depend on the UID.
- `editable` is `false` and `tags` is `["hetzner-one"]`, like the other
  provisioned dashboard.
- The `ds_prometheus` variable defaults to the `hetzner-prometheus` datasource.
- A dashboard `description` names this source and the empty panels.
- Panels this VPS cannot populate are removed: the Systemd and Hardware Misc
  rows with all their panels, CPU Frequency Scaling, IRQ Detail, and the TCP
  Stat Persistent, TCP Stat Transient, and TCP Socket Queue panels
  (`tcpstat` would report only the exporter container's sockets). The
  remaining panels in System Misc and Network Netstat are reflowed two per
  line, and the rows below each removed row move up one grid unit.
- The NF Conntrack panel description notes that its values come from the
  exporter container's network namespace.

To update, pick an upstream commit at least two weeks old, reapply exactly
these changes, and compare the result with `python3 -m json.tool` output of
the upstream file. Then update the commit and dates above.
