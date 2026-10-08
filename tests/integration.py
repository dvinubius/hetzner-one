#!/usr/bin/env python3
"""Local Docker integration test. Requires Go and the four pinned images already pulled.
Uses a unique Compose project, private test state and random loopback ports.
Never calls deploy.sh, SSH, or a production endpoint.
"""
import base64
from datetime import datetime
import json
import os
import re
import socket
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'hetzner-test-' + uuid.uuid4().hex[:10]
CADDY = 'caddy-hooklook:2.11.4-ratelimit'


def output(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def wait(check):
    deadline = time.monotonic() + 120
    while True:
        try:
            value = check()
            if value:
                return value
        except (OSError, ValueError, AssertionError, KeyError):
            pass
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for local test service')
        time.sleep(2)


with tempfile.TemporaryDirectory(prefix='hetzner-integration-') as tmp:
    work = Path(tmp)
    password = uuid.uuid4().hex
    env = {**os.environ, 'GRAFANA_ADMIN_PASSWORD': password}
    # Applications join these networks externally by name; Caddy owns them.
    ingress = json.loads(output('docker', 'compose', '-f', str(ROOT / 'compose.yaml'), 'config', '--format', 'json'))
    for network in ('zibs-edge', 'hooklook-edge', 'saga-lab-edge'):
        assert ingress['networks'][network]['name'] == network and not ingress['networks'][network].get('external'), network
        assert network in ingress['services']['caddy']['networks'], network
    config = json.loads(output('docker', 'compose', '-f', str(ROOT / 'compose.observability.yaml'), 'config', '--format', 'json', env=env))
    config['name'] = PROJECT
    for section in ('volumes', 'networks'):
        for key, value in config[section].items():
            value['name'] = PROJECT + '_' + key
    config['services']['platform-grafana']['ports'][0]['published'] = '0'
    # Exercise production flags; only Desktop's root filesystem needs a fixture.
    if 'Docker Desktop' in output('docker', 'info', '--format', '{{.OperatingSystem}}'):
        # Old Desktop cannot bind the VM root with rslave. Only the test uses
        # a fixture root; real VPS filesystem identity remains a rollout check.
        config['services']['platform-node']['volumes'][0]['source'] = str(work)
        config['services']['platform-node']['volumes'][0]['bind']['propagation'] = 'rprivate'
    arch = output('docker', 'info', '--format', '{{.Architecture}}')
    arch = {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(arch, arch)
    subprocess.run(['go', 'build', '-o', str(work / 'backend'), str(ROOT / 'tests/upstream.go')],
                   env={**os.environ, 'GOOS': 'linux', 'GOARCH': arch, 'CGO_ENABLED': '0'}, check=True)
    adapted = json.loads(output('docker', 'run', '--rm', '--network', 'none', '-v', f'{ROOT}/Caddyfile:/etc/caddy/Caddyfile:ro', CADDY, 'caddy', 'adapt', '--config', '/etc/caddy/Caddyfile'))
    adapted['admin'] = {'disabled': True}
    adapted['apps'].pop('tls', None)
    for server in adapted['apps']['http']['servers'].values():
        if server['listen'] == [':443']:
            server['listen'] = [':8080']
            server.pop('tls_connection_policies', None)
            server['automatic_https'] = {'disable': True}

    def upstreams(item, found):
        if isinstance(item, dict):
            if 'dial' in item:
                found.add(item['dial'])
            for value in item.values():
                upstreams(value, found)
        elif isinstance(item, list):
            for value in item:
                upstreams(value, found)
        return found
    # Caddy joins every edge network, where each app's Grafana also answers to
    # the Compose service name "grafana"; every upstream must be unambiguous.
    dials = {}
    for server in adapted['apps']['http']['servers'].values():
        for route in server.get('routes', []):
            for host in (host for match in route.get('match', []) for host in match.get('host', [])):
                dials[host] = upstreams(route, set())
    assert dials['zibs.app'] == {'zibs:8080', 'zibs-grafana-1:3000'}, dials
    assert dials['hooklook.app'] == {'hooklook:8080', 'hooklook-grafana:3000'}, dials
    assert dials['saga.dinubarbu.com'] == {'saga-lab:8080', 'saga-lab-grafana:3000'}, dials

    def replace_upstreams(item):
        if isinstance(item, dict):
            if 'dial' in item:
                # The fixture answers on both upstream ports, naming the port.
                item['dial'] = 'fixture:' + item['dial'].rsplit(':', 1)[1]
            for value in item.values():
                replace_upstreams(value)
        elif isinstance(item, list):
            for value in item:
                replace_upstreams(value)
    replace_upstreams(adapted)
    (work / 'caddy.json').write_text(json.dumps(adapted))
    config['services']['caddy'] = {
        'image': CADDY, 'tmpfs': ['/data', '/config'], 'command': ['caddy', 'run', '--config', '/etc/caddy/test.json'],
        'volumes': [f'{work}/caddy.json:/etc/caddy/test.json:ro'],
        'ports': ['127.0.0.1::8080', '127.0.0.1::9180'],
        'networks': ['platform-metrics', 'grafana-access'],
    }
    config['services']['fixture'] = {'image': CADDY, 'tmpfs': ['/data', '/config'], 'entrypoint': ['/backend'], 'volumes': [f'{work}/backend:/backend:ro'], 'networks': ['platform-metrics']}
    path = work / 'compose.json'
    path.write_text(json.dumps(config))
    compose = ['docker', 'compose', '-p', PROJECT, '-f', str(path)]
    try:
        subprocess.run(compose + ['up', '-d'], check=True)
        def port(service, number):
            return output(*compose, 'port', service, str(number)).rsplit(':', 1)[1]
        base = 'http://127.0.0.1:' + port('caddy', 8080)
        metrics_url = 'http://127.0.0.1:' + port('caddy', 9180)
        grafana = 'http://127.0.0.1:' + port('platform-grafana', 3000)
        auth = 'Basic ' + base64.b64encode(('admin:' + password).encode()).decode()
        def api(path, authenticated=True, payload=None, method=None):
            headers = {'Authorization': auth} if authenticated else {}
            if payload is not None:
                headers['Content-Type'] = 'application/json'
            request = urllib.request.Request(grafana + path, headers=headers,
                data=json.dumps(payload).encode() if payload is not None else None, method=method)
            return json.load(urllib.request.urlopen(request, timeout=10))
        def fetch(path, data=None, headers=None, method=None):
            req = urllib.request.Request(base + path, data=data, headers={'Host': 'hooklook.app', **(headers or {})}, method=method)
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    return response.status, response.read().decode()
            except urllib.error.HTTPError as error:
                return error.code, ''
        def request(path, data=None, headers=None, method=None):
            return fetch(path, data, headers, method)[0]
        saga = {'Host': 'saga.dinubarbu.com'}
        def websocket_echo(path):
            with socket.create_connection(('127.0.0.1', int(base.rsplit(':', 1)[1])), timeout=10) as conn:
                conn.sendall((f'GET {path} HTTP/1.1\r\nHost: saga.dinubarbu.com\r\nConnection: Upgrade\r\n'
                              'Upgrade: websocket\r\nSec-WebSocket-Version: 13\r\n'
                              'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n').encode())
                reply = b''
                while b'\r\n\r\n' not in reply:
                    reply += conn.recv(4096)
                assert reply.startswith(b'HTTP/1.1 101 '), reply
                conn.sendall(b'ping')
                echoed = b''
                while len(echoed) < 4:
                    echoed += conn.recv(4)
                return echoed.decode()
        wait(lambda: request('/health') == 200)
        assert request('/b/test', b'x' * 10000000) == 200
        assert request('/b/test', b'x' * 10000001) == 413
        assert [request('/') for _ in range(21)] == [200] * 20 + [429]
        assert request('/health', headers={'X-Large': 'x' * 40000}) == 431
        assert request('/login', headers={'Host': 'zibs.app'}) == 404
        assert request('/public-dashboards/test', headers={'Host': 'zibs.app'}) == 200
        assert request('/public-dashboards/test') == 200
        metrics = urllib.request.urlopen(metrics_url + '/metrics').read().decode()
        lines = [line for line in metrics.splitlines() if line.startswith('caddy_http_request_duration_seconds_count') and 'host="hooklook.app"' in line]
        assert sum(float(line.rsplit(' ', 1)[1]) for line in lines if 'code="413"' in line) == 1
        assert sum(float(line.rsplit(' ', 1)[1]) for line in lines if 'code="429"' in line) == 1
        assert not any('code="431"' in line for line in lines)
        assert all('handler="subroute"' in line for line in lines)
        assert 'rate_limit' not in metrics and 'key=' not in metrics
        try:
            urllib.request.urlopen(metrics_url + '/config/')
            raise AssertionError('Private scrape listener served another path')
        except urllib.error.HTTPError as error:
            assert error.code == 404
        print('PASS: ingress boundaries, public dashboard route, metrics labels and rejection counters', flush=True)
        assert fetch('/', headers=saga) == (200, '8080 /')
        assert fetch('/transfers/abc', headers=saga) == (200, '8080 /transfers/abc')
        assert fetch('/grafana/d/saga-lab-trace', headers=saga) == (200, '3000 /grafana/d/saga-lab-trace')
        assert websocket_echo('/grafana/api/live/ws') == 'ping'
        print('PASS: saga.dinubarbu.com routes /grafana/* with its prefix to Grafana, passing WebSocket upgrades, the rest to the app', flush=True)
        submissions = ['/transfers', '/top-ups', '/reset', '/api/transfers'] * 5 + ['/api/top-ups']
        assert [request(path, b'', saga) for path in submissions] == [200] * 20 + [429]
        assert request('/api/transfers', headers=saga) == 200
        # The two Grafana requests above are the zone's first.
        assert [request('/grafana/api/health', headers=saga) for _ in range(299)] == [200] * 298 + [429]
        assert request('/', headers=saga) == 200
        print('PASS: saga.dinubarbu.com limits submissions to 20 POSTs and Grafana to 300 requests a minute', flush=True)
        wait(lambda: api('/api/health', False).get('database') == 'ok')
        wait(lambda: api('/api/datasources/uid/hetzner-prometheus/health').get('status') == 'OK')
        try:
            api('/api/search', False)
            raise AssertionError('Anonymous access allowed')
        except urllib.error.HTTPError as error:
            assert error.code == 401
        def query(expr):
            result = api('/api/datasources/proxy/uid/hetzner-prometheus/api/v1/query?' + urllib.parse.urlencode({'query': expr}))
            assert result['status'] == 'success'
            return result['data']['result']
        wait(lambda: len(query('up == 1')) == 2)
        assert query('node_cpu_seconds_total{job="node"}')
        assert query('node_memory_MemAvailable_bytes{job="node"}')
        native_net = output('docker', 'run', '--rm', '--network', 'host', '--entrypoint', 'cat', CADDY, '/proc/net/dev')
        expected_devices = {line.split(':')[0].strip() for line in native_net.splitlines() if ':' in line}
        expected_devices = {device for device in expected_devices if not re.fullmatch(r'lo|veth.*|docker.*|br-.*', device)}
        for metric in ('node_network_receive_bytes_total', 'node_network_up'):
            observed_devices = {row['metric']['device'] for row in query(metric + '{job="node"}')}
            assert observed_devices == expected_devices, (metric, observed_devices, expected_devices)
        print('PASS: exporter network interfaces match the native Docker host namespace', flush=True)
        assert query('node_filesystem_size_bytes{job="node",mountpoint="/"}')
        assert query('node_boot_time_seconds{job="node"}')
        assert query('node_processes_pids{job="node"}')
        [uname] = query('node_uname_info{job="node"}')
        assert uname['metric']['nodename'] == 'hetzner-one', uname
        variables = {'$__rate_interval': '1m', '$__range': '1h', '$job': 'node', '$node': uname['metric']['instance'], '$host': 'zibs.app|hooklook.app|art-gallery.dinubarbu.com'}
        def panels(items):
            for panel in items:
                yield panel
                yield from panels(panel.get('panels', []))
        for uid in ('hetzner-host', 'hetzner-caddy'):
            dashboard = wait(lambda uid=uid: api('/api/dashboards/uid/' + uid)['dashboard'])
            for panel in panels(dashboard['panels']):
                for target in panel.get('targets', []):
                    expr = target['expr']
                    for name, value in variables.items():
                        expr = expr.replace(name, value)
                    # $1 in label_replace is a capture group, not a variable.
                    assert not re.search(r'\$\{?[A-Za-z_]', expr), (uid, panel['title'], expr)
                    query(expr)
        # Caddy dashboard series beyond the request histogram, after local 413/429 traffic.
        for expr in ('caddy_http_request_errors_total{job="caddy",handler="subroute"}',
                     'caddy_http_response_duration_seconds_bucket{job="caddy",handler="subroute"}',
                     'caddy_http_requests_in_flight{job="caddy",handler="subroute"}',
                     'process_start_time_seconds{job="caddy"}'):
            wait(lambda expr=expr: query(expr))
        print('PASS: actual exporters, Grafana auth/provisioning/datasource, all dashboard PromQL expressions', flush=True)
        # Exercise file ownership/state persistence and service scoping on restart.
        api('/api/user/preferences', payload={'theme': 'dark'}, method='PUT')
        caddy_id = output(*compose, 'ps', '-q', 'caddy')
        subprocess.run(compose + ['up', '-d', '--no-deps', '--force-recreate', 'platform-node', 'platform-prometheus', 'platform-grafana'], check=True)
        assert output(*compose, 'ps', '-q', 'caddy') == caddy_id
        grafana = 'http://127.0.0.1:' + port('platform-grafana', 3000)
        wait(lambda: api('/api/dashboards/uid/hetzner-host')['dashboard']['uid'] == 'hetzner-host')
        assert api('/api/user/preferences')['theme'] == 'dark'
        # Pre-restart samples remain queryable; the verifier waits for a newer scrape.
        def started_at(service):
            stamp = output('docker', 'inspect', '--format', '{{.State.StartedAt}}', output(*compose, 'ps', '-q', service))
            return datetime.fromisoformat(re.sub(r'(\.\d{6})\d*', r'\1', stamp).replace('Z', '+00:00')).timestamp()
        since = max(started_at('platform-node'), started_at('platform-prometheus'))
        wait(lambda: query(f'up{{job="node"}} == 1 and timestamp(up{{job="node"}}) > {since}'))
        assert query(f'timestamp(node_boot_time_seconds{{job="node"}}) > {since}')
        print('PASS: observability recreation preserves Caddy and Grafana runtime preferences; fresh scrape after restart', flush=True)
        # Exercise the real restore helper against this isolated project's state.
        import shutil
        stamp = '20260925T000000Z'
        backup = work / 'rollback' / stamp / 'observability'
        backup.mkdir(parents=True)
        (work / 'compose.observability.yaml').write_text(json.dumps(config))
        (backup / 'compose.observability.yaml').write_text(json.dumps(config))
        (work / '.env').write_text('GRAFANA_ADMIN_PASSWORD=' + password + '\n')
        (backup / '.env').write_text('GRAFANA_ADMIN_PASSWORD=' + password + '\n')
        shutil.copytree(ROOT / 'observability', work / 'observability')
        shutil.copytree(ROOT / 'observability', backup / 'observability')
        subprocess.run(compose + ['stop', 'platform-prometheus', 'platform-grafana'], check=True)
        for kind in ('prometheus', 'grafana'):
            subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--user', '0', '--entrypoint', 'tar',
                '-v', PROJECT + '_platform-' + kind + '-data:/data:ro', '-v', str(backup) + ':/backup',
                'grafana/grafana:12.4.10', '-czf', '/backup/' + kind + '-data.tgz', '-C', '/data', '.'], check=True)
        restore = (ROOT / 'scripts/rollback-observability.sh').read_text().replace('/opt/caddy', str(work)).replace('caddy_platform-', PROJECT + '_platform-')
        (work / 'restore.sh').write_text(restore)
        subprocess.run(['bash', str(work / 'restore.sh'), stamp], check=True)
        grafana = 'http://127.0.0.1:' + port('platform-grafana', 3000)
        wait(lambda: api('/api/dashboards/uid/hetzner-host')['dashboard']['uid'] == 'hetzner-host')
        wait(lambda: len(query('up == 1')) == 2)
        assert output(*compose, 'ps', '-q', 'caddy') == caddy_id
        assert list(backup.glob('grafana-failed-*.tgz')) and list(backup.glob('prometheus-failed-*.tgz'))
        assert api('/api/user/preferences')['theme'] == 'dark'
        print('PASS: real cold snapshot rollback of Grafana and Prometheus; Caddy untouched', flush=True)
    except Exception:
        subprocess.run(compose + ['logs', '--tail', '25'])
        raise
    finally:
        # Only this randomly named test project's resources. Never down -v.
        subprocess.run(compose + ['down'], check=True)
        for name in output('docker', 'volume', 'ls', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECT).splitlines():
            subprocess.run(['docker', 'volume', 'rm', name], check=True)
