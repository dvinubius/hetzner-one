#!/usr/bin/env python3
"""Local Docker integration test. Requires Go and the four pinned images already pulled.
Uses a unique Compose project, private test state and random loopback ports.
Never calls deploy.sh, SSH, or a production endpoint.
"""
import base64
import json
import os
import re
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

    def replace_upstreams(item):
        if isinstance(item, dict):
            if 'dial' in item:
                item['dial'] = 'fixture:8080'
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
        def request(path, data=None, headers=None):
            req = urllib.request.Request(base + path, data=data, headers={'Host': 'hooklook.app', **(headers or {})})
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code
        wait(lambda: request('/health') == 200)
        assert request('/b/test', b'x' * 10000000) == 200
        assert request('/b/test', b'x' * 10000001) == 413
        assert [request('/') for _ in range(11)] == [200] * 10 + [429]
        assert request('/health', headers={'X-Large': 'x' * 40000}) == 431
        assert request('/login', headers={'Host': 'zibs.app'}) == 404
        assert request('/public-dashboards/test', headers={'Host': 'zibs.app'}) == 200
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
        observed_devices = {row['metric']['device'] for row in query('node_network_receive_bytes_total{job="node"}')}
        assert observed_devices == expected_devices, (observed_devices, expected_devices)
        print('PASS: exporter network interfaces match the native Docker host namespace', flush=True)
        assert query('node_filesystem_size_bytes{job="node",mountpoint="/"}')
        for uid in ('hetzner-host', 'hetzner-caddy'):
            dashboard = wait(lambda uid=uid: api('/api/dashboards/uid/' + uid)['dashboard'])
            for panel in dashboard['panels']:
                query(panel['targets'][0]['expr'].replace('$__rate_interval', '1m'))
        print('PASS: actual exporters, Grafana auth/provisioning/datasource, all dashboard PromQL expressions', flush=True)
        # Exercise file ownership/state persistence and service scoping on restart.
        api('/api/user/preferences', payload={'theme': 'dark'}, method='PUT')
        caddy_id = output(*compose, 'ps', '-q', 'caddy')
        subprocess.run(compose + ['up', '-d', '--no-deps', '--force-recreate', 'platform-node', 'platform-prometheus', 'platform-grafana'], check=True)
        assert output(*compose, 'ps', '-q', 'caddy') == caddy_id
        grafana = 'http://127.0.0.1:' + port('platform-grafana', 3000)
        wait(lambda: api('/api/dashboards/uid/hetzner-host')['dashboard']['uid'] == 'hetzner-host')
        assert api('/api/user/preferences')['theme'] == 'dark'
        print('PASS: observability recreation preserves Caddy and Grafana runtime preferences', flush=True)
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
