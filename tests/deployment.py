#!/usr/bin/env python3
"""Exercise deployment failure paths with a fake Docker CLI; no SSH or live Docker."""
import atexit
import json
import os
from pathlib import Path
import shutil
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

# Caddy activation pins a GHCR digest in compose.override.yaml and never builds.
CADDY_DOCKER = """#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
a=sys.argv[1:]
log=os.environ['DOCKER_LOG']
with open(log,'a') as f: f.write(json.dumps({'args':a,'config':os.environ.get('DOCKER_CONFIG')})+'\\n')
fault=os.environ.get('FAULT','')
if a[:2]==['compose','ps']:
 print('container-1' if os.environ.get('RUNNING')=='1' else '')
elif a[:1]==['inspect']: print('sha256:previous')
elif a[:2]==['image','inspect']: sys.exit(0 if os.environ.get('PRESENT')=='1' else 1)
elif a[:1]==['login']:
 Path(log+'.token').write_text(sys.stdin.read())
 assert Path(os.environ['DOCKER_CONFIG']).is_dir()
elif a[:1]==['pull'] and fault=='pull': sys.exit(1)
elif 'list-modules' in a: print('http.handlers.rate_limit')
elif 'validate' in a and fault=='validation': sys.exit(1)
"""
OLD_IMAGE = 'ghcr.io/example/hetzner-one-caddy@sha256:' + '1' * 64
NEW_IMAGE = 'ghcr.io/example/hetzner-one-caddy@sha256:' + '2' * 64


def caddy_scenario(name, image='', previous=None, token=None, fault='', present=False, verify_fails=False, expect_success=True):
    """previous: None (first install), 'host' (host-built), or a GHCR image."""
    # Kept until exit so callers can inspect the resulting state.
    tmp = tempfile.mkdtemp(prefix='hetzner-caddy-test-')
    atexit.register(shutil.rmtree, tmp, True)
    if True:
        live = Path(tmp)
        stage = live / '.staging' / STAMP
        (stage / 'scripts').mkdir(parents=True)
        for file in ('Caddyfile', 'compose.yaml'):
            (stage / file).write_text('new ' + file)
        (stage / 'scripts/verify.sh').write_text('exit 1\n' if verify_fails else 'exit 0\n')
        if previous:
            for file in ('Caddyfile', 'compose.yaml'):
                (live / file).write_text('old ' + file)
        if previous == 'host':
            (live / 'Dockerfile').write_text('old Dockerfile')
        elif previous:
            (live / 'compose.override.yaml').write_text(f'services:\n  caddy:\n    image: {previous}\n')
        script = live / 'activate.sh'
        script.write_text((ROOT / 'scripts/activate.sh').read_text().replace('/opt/caddy', str(live)))
        bin_dir = live / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'docker').write_text(CADDY_DOCKER)
        (bin_dir / 'docker').chmod(0o755)
        log = live / 'calls'
        log.touch()
        env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'DOCKER_LOG': str(log), 'FAULT': fault,
               'RUNNING': '1' if previous else '0', 'PRESENT': '1' if present else '0'}
        if token is not None:
            env['GHCR_USER'] = 'github-actions[bot]'
        result = subprocess.run(['bash', str(script), STAMP, image], env=env, input=token or '',
                                capture_output=True, text=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        args = [call['args'] for call in calls]
        assert (result.returncode == 0) == expect_success, (name, result.stdout, result.stderr)
        assert not any('build' in call or 'down' in call for call in args), args
        ups = [call for call in args if 'up' in call]
        override = live / 'compose.override.yaml'
        if expect_success:
            wanted = image or previous
            assert override.read_text().endswith(f'    image: {wanted}\n')
            assert (live / 'Caddyfile').read_text() == 'new Caddyfile'
            assert not (live / 'Dockerfile').exists()
            assert (live / 'scripts/verify.sh').exists()
        elif verify_fails:
            assert (live / 'Caddyfile').read_text() == 'old Caddyfile'
            assert (live / 'compose.yaml').read_text() == 'old compose.yaml'
            assert len(ups) == 2, ups
        else:
            assert not ups, ups
            assert previous is None or (live / 'Caddyfile').read_text() == 'old Caddyfile'
        return live, args, calls, result


caddy_scenario('first GHCR install', NEW_IMAGE)
live, args, calls, _ = caddy_scenario('host-built install moves to GHCR with a token', NEW_IMAGE, previous='host', token='secret\n')
logins = [call for call in calls if call['args'][:1] == ['login']]
pulls = [call for call in calls if call['args'][:1] == ['pull']]
assert logins and pulls and pulls[0]['config'] == logins[0]['config'] and not Path(pulls[0]['config']).exists()
assert (live / 'calls.token').read_text() == 'secret\n'
assert (live / 'rollback' / STAMP / 'Dockerfile').read_text() == 'old Dockerfile'
print('PASS: token-authenticated pull uses a temporary Docker config; the host Dockerfile is retired')
live, args, _, _ = caddy_scenario('failed host-built move restores the local image', NEW_IMAGE, previous='host', verify_fails=True, expect_success=False)
assert ['image', 'tag', 'caddy-hooklook:rollback-' + STAMP, 'caddy-hooklook:2.11.4-ratelimit'] in args
assert not (live / 'compose.override.yaml').exists() and (live / 'Dockerfile').exists()
print('PASS: failed move from a host-built image restores it')
_, args, _, _ = caddy_scenario('workstation keeps the deployed image', previous=OLD_IMAGE, present=True)
assert not any(call[:1] == ['pull'] for call in args)
print('PASS: workstation deployment keeps the deployed digest without pulling')
live, _, _, _ = caddy_scenario('failed update restores the previous digest', NEW_IMAGE, previous=OLD_IMAGE, verify_fails=True, expect_success=False)
assert (live / 'compose.override.yaml').read_text().endswith(f'    image: {OLD_IMAGE}\n')
print('PASS: failed update restores the previous digest')
for name, kwargs in [('no image deployed', {'previous': 'host'}),
                     ('mutable tag', {'image': 'ghcr.io/example/caddy:latest'}),
                     ('missing token', {'image': NEW_IMAGE, 'token': ''}),
                     ('failed pull', {'image': NEW_IMAGE, 'fault': 'pull'}),
                     ('invalid Caddyfile', {'image': NEW_IMAGE, 'fault': 'validation'})]:
    caddy_scenario(name, **{'previous': OLD_IMAGE, **kwargs}, expect_success=False)
print('PASS: missing images, tags, tokens, failed pulls, and invalid Caddyfiles change nothing')

# verify.sh checks the running container, public routes, and Caddy's error log.
VERIFY_DOCKER = """#!/usr/bin/env bash
case "$1 $2" in
'compose ps') [[ $VERIFY_RUNNING == 1 ]] && echo container-1 ;;
'inspect --format') echo 2026-09-28T19:48:41.123Z ;;
'logs --since')
 [[ $3 == 2026-09-28T19:48:41.123Z && $4 == container-1 ]] || exit 1
 echo '{"level":"info","msg":"serving initial configuration"}' >&2
 echo '{"level":"warn","msg":"HTTP/2 skipped because it requires TLS"}' >&2
 [[ -z $VERIFY_LOG ]] || echo "$VERIFY_LOG" >&2 ;;
esac
"""
with tempfile.TemporaryDirectory(prefix='hetzner-verify-test-') as tmp:
    live = Path(tmp)
    bin_dir = live / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'docker').write_text(VERIFY_DOCKER)
    (bin_dir / 'curl').write_text('#!/usr/bin/env bash\necho "${@: -1}" >> "$CURL_LOG"\n[[ "${@: -1}" != "$CURL_FAIL" ]]\n')
    for tool in ('docker', 'curl'):
        (bin_dir / tool).chmod(0o755)
    script = live / 'verify.sh'
    script.write_text((ROOT / 'scripts/verify.sh').read_text().replace('/opt/caddy', str(live)))

    def verify(running='1', log='', curl_fail=''):
        env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'CURL_LOG': str(live / 'curl.log'),
               'VERIFY_RUNNING': running, 'VERIFY_LOG': log, 'CURL_FAIL': curl_fail}
        return subprocess.run(['bash', str(script)], env=env, capture_output=True, text=True)

    result = verify()
    assert result.returncode == 0, result.stderr
    assert 'No Caddy errors logged since 2026-09-28T19:48:41.123Z' in result.stdout
    assert len((live / 'curl.log').read_text().splitlines()) == 4
    print('PASS: verify accepts a running Caddy with healthy routes and no logged errors')
    for level in ('error', 'fatal', 'panic'):
        entry = '{"level":"%s","logger":"tls.obtain","msg":"could not get certificate"}' % level
        result = verify(log=entry)
        assert result.returncode != 0 and 'could not get certificate' in result.stderr, level
    print('PASS: verify fails on error, fatal, and panic entries since Caddy started')
    assert verify(running='0').returncode != 0
    assert verify(curl_fail='https://hooklook.app/health').returncode != 0
    print('PASS: verify fails when Caddy is not running or a public route fails')

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
    # CI passes a commit and uploads no password: reuse the live one, record
    # the manifest only on success, and keep the previous one on failure.
    commit, newer = 'a' * 40, 'b' * 40
    manifest = live / '.deploy' / 'manifest'
    (live / '.env').write_text('GRAFANA_ADMIN_PASSWORD=live\n')
    (stage / 'activate-observability.sh').write_text(f'cp "{stage.parent}/.env" "{live}/seen-env"\n')
    subprocess.run(['bash', str(runner), STAMP, 'observability', commit], check=True, capture_output=True)
    assert (live / 'seen-env').read_text() == (live / '.env').read_text()
    assert not staged_env.exists()
    assert manifest.read_text().startswith(f'commit={commit}\nmode=observability\n')
    assert manifest.stat().st_mode & 0o777 == 0o600
    print('PASS: CI deployment reuses the live password and records the manifest')
    (stage / 'activate.sh').write_text('exit 1\n')
    assert subprocess.run(['bash', str(runner), STAMP, 'caddy', newer], capture_output=True).returncode != 0
    assert manifest.read_text().startswith(f'commit={commit}\n')
    print('PASS: failed CI deployment keeps the previous manifest')
    (stage / 'activate.sh').write_text('exit 0\n')
    subprocess.run(['bash', str(runner), STAMP, 'caddy'], check=True)
    assert not manifest.exists()
    print('PASS: workstation deployment forgets the verified baseline')
    (live / '.env').unlink()
    (stage / 'activate-observability.sh').write_text(f'echo observability >> "{log}"\n')
    log.write_text('')
    assert subprocess.run(['bash', str(runner), STAMP, 'observability', commit], capture_output=True).returncode != 0
    assert log.read_text() == '' and not manifest.exists()
    print('PASS: CI observability deployment without a live password stops before activation')
    # The registry token reaches only the Caddy phase, on stdin.
    (stage / 'activate.sh').write_text(f'cat > "{live}/caddy-stdin"; echo "$2" > "{live}/caddy-image"\n')
    (stage / 'activate-observability.sh').write_text(f'cat > "{live}/observability-stdin"\n')
    (live / '.env').write_text('GRAFANA_ADMIN_PASSWORD=live\n')
    image = 'ghcr.io/example/hetzner-one-caddy@sha256:' + '2' * 64
    subprocess.run(['bash', str(runner), STAMP, 'full', commit, image], check=True, input='secret\n', text=True,
                   env={**os.environ, 'GHCR_USER': 'github-actions[bot]'})
    assert (live / 'caddy-stdin').read_text() == 'secret\n'
    assert (live / 'observability-stdin').read_text() == ''
    assert (live / 'caddy-image').read_text() == image + '\n'
    assert subprocess.run(['bash', str(runner), STAMP, 'observability', commit, image], capture_output=True).returncode != 0
    print('PASS: registry token and image reach only the Caddy phase')
    # A failed activation must also remove the staged password.
    (stage / 'activate-observability.sh').write_text('exit 1\n')
    staged_env.write_text('GRAFANA_ADMIN_PASSWORD=staged\n')
    assert subprocess.run(['bash', str(runner), STAMP, 'observability']).returncode != 0
    assert not staged_env.exists()
    print('PASS: failed activation removes staged password')
