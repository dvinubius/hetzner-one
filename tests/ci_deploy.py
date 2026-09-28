#!/usr/bin/env python3
"""Exercise CI change classification and upload with a fake SSH; no VPS or Docker."""
import os
from pathlib import Path
import subprocess
import tarfile
import io
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SSH = '''#!/usr/bin/env python3
import os,sys
from pathlib import Path
command=sys.argv[-1]
log=Path(os.environ['SSH_LOG'])
with log.open('a') as f: f.write('ARGS '+' '.join(sys.argv[1:])+'\\n')
if os.environ.get('SSH_FAIL'): sys.exit(255)
if 'tar -x' in command:
 Path(os.environ['SSH_BUNDLE']).write_bytes(sys.stdin.buffer.read())
elif 'activate-mode.sh' in command:
 with log.open('a') as f: f.write('STDIN '+sys.stdin.read())
elif 'manifest' in command:
 manifest=Path(os.environ['SSH_MANIFEST'])
 if manifest.exists(): sys.stdout.write(manifest.read_text())
'''


def git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=repo, text=True).strip()


def commit(repo, paths, message):
    for path in paths:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(target.read_text() + message + '\n' if target.exists() else message + '\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', message)
    return git(repo, 'rev-parse', 'HEAD')


with tempfile.TemporaryDirectory(prefix='hetzner-ci-test-') as tmp:
    tmp = Path(tmp)
    repo = tmp / 'repo'
    (repo / 'scripts').mkdir(parents=True)
    for script in ('ci-deploy.sh', 'classify-deploy.sh', 'deploy.sh', 'activate-mode.sh', 'activate.sh', 'verify.sh'):
        (repo / 'scripts' / script).write_text((ROOT / 'scripts' / script).read_text())
    for path in ('Caddyfile', 'Dockerfile', 'compose.yaml', 'compose.observability.yaml',
                 'observability/prometheus/prometheus.yml', 'README.md', '.env.production.example'):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text('base\n')
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
    for key, value in (('user.email', 'test@example.invalid'), ('user.name', 'test'), ('commit.gpgsign', 'false')):
        git(repo, 'config', key, value)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'base')
    base = git(repo, 'rev-parse', 'HEAD')

    def classify(deployed, target):
        result = subprocess.run(['bash', 'scripts/classify-deploy.sh', deployed, target], cwd=repo, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    cases = [
        (['docs/x.md', 'README.md', 'tests/t.py', '.env.production.example', 'scripts/deploy.sh'], 'none'),
        (['scripts/activate-mode.sh', 'scripts/activate.sh', '.github/workflows/deploy.yml', 'new-file'], 'none'),
        (['Caddyfile'], 'caddy'),
        (['Dockerfile', 'scripts/verify.sh', 'docs/x.md'], 'caddy'),
        (['compose.yaml'], 'caddy'),
        (['observability/grafana/dashboards/x.json'], 'observability'),
        (['compose.observability.yaml', 'scripts/verify-observability.py'], 'observability'),
        (['Caddyfile', 'observability/prometheus/prometheus.yml'], 'full'),
    ]
    for paths, expected in cases:
        git(repo, 'checkout', '-q', '-B', 'case', base)
        target = commit(repo, paths, 'change ' + ' '.join(paths))
        assert classify(base, target) == expected, (paths, expected)
    print('PASS: path classification')
    # The workflow's push filter must start a run for exactly these paths.
    workflow = (ROOT / '.github/workflows/deploy.yml').read_text().split('    paths:\n', 1)[1]
    paths = []
    for line in workflow.splitlines():
        if not line.startswith('      - '):
            break
        paths.append(line[len('      - '):])
    assert paths == ['Caddyfile', 'Dockerfile', 'compose.yaml', 'compose.observability.yaml', 'observability/**'], paths
    print('PASS: workflow push paths match the classifier')
    git(repo, 'checkout', '-q', 'main')
    unrelated = git(repo, 'commit-tree', '-m', 'unrelated', git(repo, 'rev-parse', 'HEAD^{tree}'))
    assert classify('', base) == 'full'
    assert classify('0' * 40, base) == 'full'
    assert classify(unrelated, base) == 'full'
    assert classify(base, base) == 'none'
    assert subprocess.run(['bash', 'scripts/classify-deploy.sh', base, 'HEAD'], cwd=repo, capture_output=True).returncode != 0
    print('PASS: missing or diverged baselines deploy in full; bad targets fail')

    # ci-deploy.sh with a fake ssh and a local origin.
    origin = tmp / 'origin.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(origin)], check=True)
    git(repo, 'remote', 'add', 'origin', str(origin))
    head = commit(repo, ['Caddyfile'], 'caddy change')
    git(repo, 'push', '-q', 'origin', 'main')
    bin_dir = tmp / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'ssh').write_text(SSH)
    (bin_dir / 'ssh').chmod(0o755)
    (tmp / 'key').write_text('key\n')
    (tmp / 'known_hosts').write_text('host key\n')
    ssh_log, bundle, manifest = tmp / 'ssh.log', tmp / 'bundle.tar', tmp / 'manifest'
    env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'SSH_LOG': str(ssh_log),
           'SSH_BUNDLE': str(bundle), 'SSH_MANIFEST': str(manifest), 'DEPLOY_HOST': 'vps.example',
           'DEPLOY_USER': 'caddy-deploy', 'DEPLOY_SSH_KEY_FILE': str(tmp / 'key'),
           'DEPLOY_KNOWN_HOSTS_FILE': str(tmp / 'known_hosts')}

    def ci(*args, **extra):
        ssh_log.write_text('')
        return subprocess.run(['bash', 'scripts/ci-deploy.sh', *args], cwd=repo, env={**env, **extra}, capture_output=True, text=True)

    assert ci('plan', head).stdout == 'full\n'
    manifest.write_text(f'commit={base}\nmode=full\n')
    assert ci('plan', head).stdout == 'caddy\n'
    assert ci('plan', head, 'full').stdout == 'full\n'
    ssh_args = ssh_log.read_text()
    for option in ('-i ' + str(tmp / 'key'), 'BatchMode=yes', 'IdentitiesOnly=yes', 'StrictHostKeyChecking=yes',
                   'UserKnownHostsFile=' + str(tmp / 'known_hosts'), 'caddy-deploy@vps.example'):
        assert option in ssh_args, option
    failed = ci('plan', head, SSH_FAIL='1')
    assert failed.returncode != 0 and failed.stdout == ''
    assert ci('plan', head, DEPLOY_KNOWN_HOSTS_FILE='').returncode != 0
    print('PASS: plan reads the manifest over strict SSH and fails closed')

    repository = 'ghcr.io/example/hetzner-one-caddy'
    image = repository + '@sha256:' + '3' * 64
    env['CADDY_IMAGE_REPOSITORY'] = repository
    result = ci('deploy', 'observability', head)
    assert result.returncode == 0, result.stderr
    with tarfile.open(fileobj=io.BytesIO(bundle.read_bytes())) as archive:
        names = set(archive.getnames())
    assert {'Caddyfile', 'compose.yaml', 'compose.observability.yaml',
            'observability/prometheus/prometheus.yml', 'scripts/activate-mode.sh', 'scripts/activate.sh'} <= names
    assert not names & {'Dockerfile', 'scripts/deploy.sh', 'scripts/ci-deploy.sh', 'scripts/classify-deploy.sh', 'README.md'}, names
    calls = ssh_log.read_text().splitlines()
    assert "test -r '/opt/caddy/.env'" in calls[0] and 'python3' in calls[0]
    assert f"flock -n '/opt/caddy/.deploy.lock' bash '/opt/caddy/.staging/" in calls[1]
    assert f"'observability' '{head}' ''" in calls[1] and 'rm -rf' in calls[1]
    assert ci('deploy', 'observability', head, GHCR_USER='bot', GHCR_PULL_TOKEN='secret').returncode == 0
    assert 'secret' not in ssh_log.read_text() and 'GHCR_USER' not in ssh_log.read_text()
    print('PASS: deploy uploads the allowlisted bundle and activates under the host lock')

    result = ci('deploy', 'caddy', head, image, GHCR_USER='github-actions[bot]', GHCR_PULL_TOKEN='secret')
    assert result.returncode == 0, result.stderr
    calls = ssh_log.read_text().splitlines()
    assert 'test -r' not in calls[0]
    assert f"GHCR_USER='github-actions[bot]' flock" in calls[1] and f"'caddy' '{head}' '{image}'" in calls[1]
    assert 'STDIN secret' in calls and not any('secret' in call for call in calls if call.startswith('ARGS'))
    print('PASS: Caddy deployment passes the digest in arguments and the registry token on stdin')
    for args in (('caddy', head), ('caddy', head, 'ghcr.io/other/caddy@sha256:' + '3' * 64),
                 ('caddy', head, repository + ':latest'), ('observability', head, image), ('none', head)):
        assert ci('deploy', *args).returncode != 0 and ssh_log.read_text() == '', args
    assert ci('deploy', 'caddy', head, GHCR_USER='x', GHCR_PULL_TOKEN='y', CADDY_IMAGE_REPOSITORY='').returncode != 0
    print('PASS: Caddy deployments require an image digest from the expected repository')

    stale = ci('deploy', 'caddy', base, image)
    assert stale.returncode != 0 and 'Stale run' in stale.stderr and ssh_log.read_text() == ''
    upload_failed = ci('deploy', 'caddy', head, image, SSH_FAIL='1')
    assert upload_failed.returncode != 0 and len(ssh_log.read_text().splitlines()) == 1
    print('PASS: stale runs, bad modes, and failed uploads stop before activation')
