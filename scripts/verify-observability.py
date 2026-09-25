#!/usr/bin/env python3
"""Run on the VPS. Read-only checks; never generates large requests or bursts."""
import argparse
import base64
import json
import os
import re
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-only', action='store_true', help='Before Caddy metrics activation, verify only the host path')
    parser.add_argument('--directory', default='/opt/caddy', help='Deployment directory')
    args = parser.parse_args()
    os.chdir(args.directory)
    compose = ['docker', 'compose', '-f', 'compose.observability.yaml']
    def api(path, authenticated=True):
        request = urllib.request.Request('http://127.0.0.1:3002' + path,
                                         headers={'Authorization': auth} if authenticated else {})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def wait_for(label, check):
        deadline = time.monotonic() + 120
        while True:
            try:
                if check():
                    print('PASS:', label, flush=True)
                    return
            except (OSError, ValueError, KeyError, AssertionError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('Timed out: ' + label)
            time.sleep(3)

    def query(expr):
        result = api('/api/datasources/proxy/uid/hetzner-prometheus/api/v1/query?' + urllib.parse.urlencode({'query': expr}))
        assert result['status'] == 'success', 'Prometheus query failed'
        return result['data']['result']

    for service in ('platform-node', 'platform-prometheus', 'platform-grafana'):
        cid = subprocess.check_output(compose + ['ps', '-q', service], text=True).strip()
        assert cid, service + ' missing'
        info = json.loads(subprocess.check_output(['docker', 'inspect', cid], text=True))[0]
        assert info['State']['Running'], service + ' stopped'
        bindings = info['HostConfig'].get('PortBindings') or {}
        if service == 'platform-grafana':
            assert bindings == {'3000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '3002'}]}, 'Unexpected Grafana publication'
            password = next(value.split('=', 1)[1] for value in info['Config']['Env']
                            if value.startswith('GF_SECURITY_ADMIN_PASSWORD='))
        else:
            assert not bindings, service + ' has host-published ports'
    print('PASS: services running and host port bindings private', flush=True)
    auth = 'Basic ' + base64.b64encode(('admin:' + password).encode()).decode()
    wait_for('Grafana database health', lambda: api('/api/health', False).get('database') == 'ok')
    wait_for('Grafana datasource health', lambda: api('/api/datasources/uid/hetzner-prometheus/health').get('status') == 'OK')
    try:
        api('/api/search', False)
    except urllib.error.HTTPError as error:
        assert error.code == 401, 'Unexpected anonymous API response'
    else:
        raise AssertionError('Grafana permits anonymous API access')
    print('PASS: anonymous dashboard access denied', flush=True)
    jobs = ['node'] if args.host_only else ['node', 'caddy']
    for job in jobs:
        wait_for(job + ' scrape', lambda job=job: bool(query(f'up{{job="{job}"}} == 1')))
    required = [
        'node_cpu_seconds_total{job="node"}',
        'node_memory_MemAvailable_bytes{job="node"}',
        'node_load1{job="node"}',
        'node_filesystem_size_bytes{job="node",mountpoint="/",fstype!~"tmpfs|overlay"}',
        'node_disk_read_bytes_total{job="node",device!~"loop.*|ram.*"}',
        'node_network_receive_bytes_total{job="node",device!~"lo|veth.*|docker.*|br-.*"}',
    ]
    for expr in required:
        assert query(expr), 'Missing host series: ' + expr
    native_net = Path('/proc/1/net/dev').read_text()
    expected_devices = {line.split(':')[0].strip() for line in native_net.splitlines() if ':' in line}
    expected_devices = {device for device in expected_devices if not re.fullmatch(r'lo|veth.*|docker.*|br-.*', device)}
    observed_devices = {row['metric']['device'] for row in query('node_network_receive_bytes_total{job="node"}')}
    assert observed_devices == expected_devices, 'Exporter interfaces differ from the host: ' + str((observed_devices, expected_devices))
    fs = os.statvfs('/')
    root_size = fs.f_blocks * fs.f_frsize
    sizes = query('node_filesystem_size_bytes{job="node",mountpoint="/",fstype!~"tmpfs|overlay"}')
    assert any(abs(float(row['value'][1]) - root_size) <= root_size * 0.01 for row in sizes), 'Exporter root capacity differs from host statvfs'
    print('PASS: host CPU, memory, load, root capacity, disk, and actual host interfaces', flush=True)
    for uid in ('hetzner-host', 'hetzner-caddy'):
        wait_for(uid + ' provisioning', lambda uid=uid: api('/api/dashboards/uid/' + uid)['dashboard']['uid'] == uid)
    if not args.host_only:
        caddy = subprocess.check_output(['docker', 'compose', 'ps', '-q', 'caddy'], text=True).strip()
        assert caddy, 'Caddy missing'
        info = json.loads(subprocess.check_output(['docker', 'inspect', caddy], text=True))[0]
        assert set(info['HostConfig']['PortBindings']) == {'80/tcp', '443/tcp', '443/udp'}, 'Unexpected Caddy port publication'
        metrics = subprocess.check_output(['docker', 'exec', caddy, 'wget', '-qO-', 'http://127.0.0.1:9180/metrics'], text=True)
        series = [line for line in metrics.splitlines() if line and not line.startswith('#')]
        caddy_series = [line for line in series if line.startswith('caddy_')]
        assert not any('rate_limit' in line or 'key=' in line for line in caddy_series), 'Per-client rate-limit metrics exposed'
        assert not any(label + '=' in line for line in caddy_series for label in ('path', 'uri', 'client_ip', 'remote_ip')), 'Unexpected sensitive Caddy label'
        assert query('caddy_http_request_duration_seconds_count{job="caddy",handler="subroute",host=~"zibs.app|hooklook.app|art-gallery.dinubarbu.com"}'), 'No public site traffic yet; run ingress verification then retry'
        print('PASS: Caddy traffic metrics, bounded labels, and unpublished scrape port', flush=True)
    print('Platform verification passed. Also run the external checks in the runbook.')


if __name__ == '__main__':
    main()
