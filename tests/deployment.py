#!/usr/bin/env python3
"""Exercise deployment failure paths with a fake Docker CLI; no SSH or live Docker."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STAMP = '20260925T010101Z'
DOCKER = '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
a=sys.argv[1:]
with open(os.environ['DOCKER_LOG'],'a') as f: f.write(json.dumps(a)+'\\n')
fault=os.environ.get('FAULT','')
if a[:2]==['volume','inspect']:
 sys.exit(0 if os.environ.get('HAS_VOLUMES')=='1' else 1)
if a[:2]==['ps','-aq']: sys.exit(0)
if 'promtool' in a and fault=='validation': sys.exit(1)
if 'up' in a and fault=='activation':
 marker=Path(os.environ['DOCKER_LOG']+'.failed')
 if not marker.exists(): marker.touch(); sys.exit(1)
if a and a[0]=='run' and '-czf' in a:
 if fault=='backup': sys.exit(1)
 dest=next(x[:-8] for x in a if x.endswith(':/backup'))
 Path(dest,Path(a[a.index('-czf')+1]).name).write_text('fake archive')
'''


def scenario(name, existing=False, fault='', collision=False):
    with tempfile.TemporaryDirectory(prefix='hetzner-deploy-test-') as tmp:
        live = Path(tmp)
        stage = live / '.staging' / STAMP
        (stage / 'scripts').mkdir(parents=True)
        (stage / 'observability').mkdir()
        (stage / 'observability' / 'version').write_text('new')
        (stage / 'compose.observability.yaml').write_text('new compose')
        (stage / '.env').write_text('GRAFANA_ADMIN_PASSWORD=new-password\n')
        if existing:
            (live / 'compose.observability.yaml').write_text('old compose')
            (live / 'observability').mkdir()
            (live / 'observability/version').write_text('old')
            (live / '.env').write_text('GRAFANA_ADMIN_PASSWORD=old-password\n')
        for file in ('activate-observability.sh', 'rollback-observability.sh'):
            (stage / 'scripts' / file).write_text((ROOT / 'scripts' / file).read_text().replace('/opt/caddy', str(live)))
        (stage / 'scripts/verify-observability.py').write_text('print("verification fixture")\n')
        bin_dir = live / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'docker').write_text(DOCKER)
        (bin_dir / 'docker').chmod(0o755)
        log = live / 'calls'
        env = {**os.environ, 'PATH': str(bin_dir) + ':' + os.environ['PATH'], 'DOCKER_LOG': str(log), 'FAULT': fault,
               'HAS_VOLUMES': '1' if existing or collision else '0'}
        result = subprocess.run(['bash', str(stage / 'scripts/activate-observability.sh'), STAMP], env=env, capture_output=True, text=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        expected_success = not fault and not collision
        assert (result.returncode == 0) == expected_success, (name, result.stdout, result.stderr)
        for call in calls:
            assert 'down' not in call and '--remove-orphans' not in call and 'caddy' not in call, call
        if fault == 'validation' or collision:
            assert not any('up' in call or 'stop' in call for call in calls)
            if existing:
                assert (live / 'observability/version').read_text() == 'old'
                assert (live / '.env').read_text() == 'GRAFANA_ADMIN_PASSWORD=old-password\n'
        elif fault == 'backup':
            assert any('start' in call for call in calls), calls
            assert (live / 'observability/version').read_text() == 'old'
            assert (live / '.env').read_text() == 'GRAFANA_ADMIN_PASSWORD=old-password\n'
        elif fault == 'activation' and existing:
            assert (live / 'observability/version').read_text() == 'old'
            assert (live / 'compose.observability.yaml').read_text() == 'old compose'
            assert (live / '.env').read_text() == 'GRAFANA_ADMIN_PASSWORD=old-password\n'
            assert sum('up' in call for call in calls) == 2
            assert sum('--entrypoint' in call and 'sh' in call for call in calls) == 2
        elif fault == 'activation':
            assert any('stop' in call for call in calls)
            assert (live / 'observability/version').read_text() == 'new'
        else:
            assert (live / 'observability/version').read_text() == 'new'
            assert (live / '.env').read_text() == 'GRAFANA_ADMIN_PASSWORD=new-password\n'
            assert (live / '.env').stat().st_mode & 0o777 == 0o600
            if existing:
                backup = live / 'rollback' / STAMP / 'observability'
                assert (backup / 'grafana-data.tgz').exists()
                assert (backup / 'prometheus-data.tgz').exists()
                assert (backup / '.env').read_text() == 'GRAFANA_ADMIN_PASSWORD=old-password\n'
                assert (backup / '.env').stat().st_mode & 0o777 == 0o600
        print('PASS:', name)


scenario('fresh installation')
scenario('existing installation makes cold snapshots', existing=True)
scenario('validation failure leaves live config untouched', existing=True, fault='validation')
scenario('unknown first-install volume rejected', collision=True)
scenario('failed snapshot restarts previous monitoring', existing=True, fault='backup')
scenario('activation failure restores both snapshots and previous config', existing=True, fault='activation')
scenario('first-install failure stops only monitoring and preserves state', fault='activation')

# Exercise dispatch order with harmless activation fixtures.
with tempfile.TemporaryDirectory(prefix='hetzner-mode-test-') as tmp:
    live = Path(tmp)
    stage = live / '.staging' / STAMP / 'scripts'
    stage.mkdir(parents=True)
    log = live / 'dispatch'
    for script, label in [('activate.sh', 'caddy'), ('activate-observability.sh', 'observability')]:
        (stage / script).write_text(f'echo {label} >> "{log}"\n')
    (stage / 'verify-observability.py').write_text(f'open({str(log)!r}, "a").write("verify\\n")\n')
    runner = live / 'activate-mode.sh'
    runner.write_text((ROOT / 'scripts/activate-mode.sh').read_text().replace('/opt/caddy', str(live)))
    for mode, expected in [('caddy', ['caddy']), ('observability', ['observability']), ('full', ['observability', 'caddy', 'verify'])]:
        log.write_text('')
        staged_env = stage.parent / '.env'
        staged_env.write_text('GRAFANA_ADMIN_PASSWORD=staged\n')
        subprocess.run(['bash', str(runner), STAMP, mode], check=True)
        assert log.read_text().splitlines() == expected
        assert not staged_env.exists()
        print('PASS: dispatch mode', mode)
    # A failed activation must also remove the staged password.
    (stage / 'activate-observability.sh').write_text('exit 1\n')
    staged_env.write_text('GRAFANA_ADMIN_PASSWORD=staged\n')
    assert subprocess.run(['bash', str(runner), STAMP, 'observability']).returncode != 0
    assert not staged_env.exists()
    print('PASS: failed activation removes staged password')
