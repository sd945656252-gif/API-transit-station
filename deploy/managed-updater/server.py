"""Internal, authenticated release coordinator. No public port and no browser credentials.

The application only submits a fixed update job; repository, container and Docker
configuration are operator-owned. GitHub credentials never enter build contexts.
"""
import copy
import datetime
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = Path('/state')
REPO = STATE / 'repository'
REPOSITORY = os.environ.get('CUSTOM_REPOSITORY', 'sd945656252-gif/API-transit-station')
APP = os.environ.get('APP_CONTAINER', 'sub2api')
IMAGE = os.environ.get('APP_IMAGE', 'sub2api-local:runninghub')
OFFICIAL = 'Wei-Shaw/sub2api'
LOCK = threading.RLock()
CHECK_LOCK = threading.Lock()
ACTIVE = {'queued', 'checking', 'syncing', 'building', 'publishing', 'deploying'}
JOB = {'state': 'idle', 'stage': 'idle'}
CACHE = None
CACHE_TIME = 0
if not re.fullmatch(r'[\w.-]+/[\w.-]+', REPOSITORY):
    raise RuntimeError('Invalid configured repository')


def secret(name):
    value = Path('/run/secrets', name).read_text(encoding='utf-8').strip()
    if len(value) < 24:
        raise RuntimeError('Missing or invalid updater credential')
    return value


TOKEN = secret('updater_token')
GH_TOKEN = secret('github_token')


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def redact(value):
    text = str(value).replace(TOKEN, '[REDACTED]').replace(GH_TOKEN, '[REDACTED]')
    return re.sub(r'(?i)(?:gh[pousr]_[\w]+|github_pat_[\w]+)', '[REDACTED]', text)


def progress(stage, **fields):
    with LOCK:
        JOB.update(stage=stage, updated_at=now(), **fields)
        STATE.mkdir(exist_ok=True)
        tmp = STATE / 'job.tmp'
        tmp.write_text(json.dumps(JOB, ensure_ascii=False), encoding='utf-8')
        tmp.replace(STATE / 'job.json')


def run(args, cwd=REPO, data=None, check=True, timeout=1800):
    result = subprocess.run(args, cwd=cwd, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    text = redact(result.stdout.decode('utf-8', errors='replace'))
    # Retain bounded build diagnostics, without credentials or Docker config/env.
    log = STATE / 'build.log'
    if log.exists() and log.stat().st_size > 4_000_000:
        log.write_text('', encoding='utf-8')
    with log.open('a', encoding='utf-8') as stream:
        stream.write(text[-200_000:] + '\n')
    if check and result.returncode:
        raise RuntimeError(f'{args[0]} failed ({result.returncode}): {text[-3500:]}')
    return result.returncode, text.strip()


def git(*args, **kwargs):
    return run(['git', *args], **kwargs)


def github(path, method='GET', payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request('https://api.github.com/' + path, data=body, method=method,
        headers={'Authorization': 'Bearer ' + GH_TOKEN, 'Accept': 'application/vnd.github+json',
                 'User-Agent': 'Sub2API-Managed-Updater', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'GitHub API HTTP {error.code}; check token permissions, quota and connectivity') from None


def latest(force=False):
    global CACHE, CACHE_TIME
    with CHECK_LOCK:
        if not force and CACHE and time.monotonic() - CACHE_TIME < 300:
            return copy.deepcopy(CACHE)
        release = github(f'repos/{OFFICIAL}/releases/latest')
        tag = release.get('tag_name', '')
        if release.get('draft') or release.get('prerelease') or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
            raise RuntimeError('Official latest release is not a supported stable version')
        CACHE = release
        CACHE_TIME = time.monotonic()
        return copy.deepcopy(CACHE)


class DockerConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__('localhost', timeout=180)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')


def docker(method, path, payload=None, missing_ok=False):
    connection = DockerConnection()
    try:
        body = None if payload is None else json.dumps(payload).encode()
        connection.request(method, '/v1.47' + path, body, {'Content-Type': 'application/json'})
        response = connection.getresponse()
        data = response.read()
        if response.status == 404 and missing_ok:
            return None
        if response.status >= 400:
            # Do not echo container configuration or secrets returned by daemon errors.
            raise RuntimeError(f'Docker {method} {path.split("?")[0]} returned HTTP {response.status}')
        return json.loads(data) if data else None
    finally:
        connection.close()


def contract_errors():
    errors = []
    contracts = json.loads(Path('/app/contracts.json').read_text(encoding='utf-8'))
    for path, markers in contracts.items():
        file = REPO / path
        text = file.read_text(encoding='utf-8') if file.is_file() else ''
        if any(marker not in text for marker in markers):
            errors.append(path)
    return errors


def sync_release(tag):
    if not (REPO / '.git').is_dir():
        run(['git', 'clone', '--depth=1', '--no-tags', f'https://github.com/{REPOSITORY}.git', str(REPO)], cwd=STATE)
    git('fetch', '--depth=1', '--no-tags', 'origin', 'main')
    git('checkout', '-B', 'managed-update', 'origin/main')
    # Only this disposable coordinator-owned clone is reset. Never a user's workspace.
    if REPO.resolve() != Path('/state/repository'):
        raise RuntimeError('Unsafe checkout path')
    git('reset', '--hard', 'origin/main')
    git('clean', '-fdx')
    git('config', 'user.name', 'Sub2API Updater')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    git('remote', 'remove', 'upstream', check=False)
    git('remote', 'add', 'upstream', f'https://github.com/{OFFICIAL}.git')
    base = (REPO / '.upstream-ref').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'[0-9a-f]{40}', base):
        raise RuntimeError('Invalid official baseline; repair .upstream-ref before upgrading')
    git('fetch', '--depth=1', '--no-tags', 'upstream', base)
    git('fetch', '--depth=1', '--no-tags', 'upstream', f'refs/tags/{tag}:refs/managed-upstream/{tag}')
    target = git('rev-parse', f'refs/managed-upstream/{tag}^{{commit}}')[1]
    if base == target:
        return git('rev-parse', 'HEAD')[1], False
    # Avoid downgrading an installation already based on a newer official commit.
    comparison = github(f'repos/{OFFICIAL}/compare/{base}...{target}')
    if comparison.get('status') != 'ahead':
        raise RuntimeError('Official release is not newer than the installed official baseline')
    migration_changes = git('diff', '--name-status', base, target, '--', 'backend/migrations')[1]
    if any(line and not line.startswith('A\t') for line in migration_changes.splitlines()):
        raise RuntimeError('An existing database migration changed; manual data compatibility review is required')
    for line in migration_changes.splitlines():
        path = line.split('\t')[-1]
        sql = git('show', f'{target}:{path}')[1]
        sql = re.sub(r'/\*.*?\*/|--[^\n]*', '', sql, flags=re.S)
        if re.search(r'\b(DROP|TRUNCATE|DELETE|UPDATE|RENAME)\b|\bALTER\s+COLUMN\b', sql, re.I):
            raise RuntimeError('New data-changing migration requires compatibility review: ' + path)
    patch = subprocess.check_output(['git', 'diff', '--binary', base, target, '--', '.',
        ':(exclude).github/workflows/**'], cwd=REPO)
    code, output = run(['git', 'apply', '--3way', '--index', '--whitespace=nowarn', '-'], data=patch, check=False)
    conflicts = git('diff', '--name-only', '--diff-filter=U')[1].splitlines()
    if code and not conflicts:
        raise RuntimeError('Official changes could not be applied: ' + output[-1200:])
    for path in conflicts:
        # Preserve all non-conflicting custom hunks. Within conflict hunks prefer
        # the official side, then require the custom contracts and a real build.
        stages = git('ls-files', '-u', '--', path)[1]
        if all(f' {stage}\t' in stages for stage in (1, 2, 3)):
            files = []
            for stage in (2, 1, 3):
                file = STATE / f'merge-{stage}'
                file.write_bytes(subprocess.check_output(['git', 'show', f':{stage}:{path}'], cwd=REPO))
                files.append(file)
            merged = subprocess.run(['git', 'merge-file', '--theirs', '-p', *map(str, files)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if merged.returncode != 0:
                raise RuntimeError('Cannot resolve official conflict in ' + path)
            (REPO / path).write_bytes(merged.stdout)
            git('add', '--', path)
        else:
            # Add/delete or binary conflicts have ambiguous custom responsibilities.
            raise RuntimeError('Manual repair required for add/delete or binary conflict: ' + path)
    missing = contract_errors()
    if missing:
        raise RuntimeError('Official version needs custom integration repair: ' + ', '.join(missing))
    (REPO / '.upstream-ref').write_text(target + '\n', encoding='utf-8')
    git('add', '-A')
    git('commit', '-m', f'chore: sync official {tag}, preserve custom integrations')
    progress('syncing', conflicts_resolved=conflicts)
    return git('rev-parse', 'HEAD')[1], True


def health(name, expected=None, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        info = docker('GET', f'/containers/{name}/json')
        state = info['State']
        if state.get('Status') in ('exited', 'dead'):
            return False
        if state.get('Health', {}).get('Status') == 'healthy':
            labels = info['Config'].get('Labels') or {}
            return expected is None or labels.get('org.opencontainers.image.revision') == expected
        time.sleep(2)
    return False


def recover_deployment():
    """Reconcile only the exact containers recorded by this deployment."""
    current = docker('GET', f'/containers/{APP}/json', missing_ok=True)
    previous_name = JOB.get('previous_container')
    previous = docker('GET', f'/containers/{previous_name}/json', missing_ok=True) if previous_name else None
    if current and current['Id'] == JOB.get('previous_id'):
        docker('POST', f'/containers/{APP}/start')
        return health(APP)
    if not previous or previous['Id'] != JOB.get('previous_id'):
        raise RuntimeError('Previous container cannot be identified; operator review is required')
    if current:
        labels = current['Config'].get('Labels') or {}
        if labels.get('io.sub2api.update-job') != JOB.get('id'):
            raise RuntimeError('Application was changed outside this job; automatic recovery stopped')
        docker('DELETE', f'/containers/{APP}?force=true')
    docker('POST', f'/containers/{previous_name}/rename?name={APP}')
    docker('POST', f'/containers/{APP}/start')
    return health(APP)


def deploy(image, revision):
    old = docker('GET', f'/containers/{APP}/json')
    old_image = docker('GET', '/images/' + old['Image'] + '/json')
    image_info = docker('GET', '/images/' + urllib.parse.quote(image, safe='') + '/json')
    # Reuse the actual runtime config: ports, every data mount, secrets, security
    # options and network. Never compose up the database/cache dependencies.
    config = {key: copy.deepcopy(value) for key, value in old['Config'].items()
              if key not in ('Image', 'Hostname')}
    config['Image'] = image
    config['Hostname'] = APP
    for key in ('Entrypoint', 'Cmd', 'User', 'WorkingDir', 'StopSignal'):
        if old['Config'].get(key) == old_image['Config'].get(key):
            config[key] = image_info['Config'].get(key)
    old_env = dict(item.split('=', 1) for item in (old_image['Config'].get('Env') or []))
    runtime_env = dict(item.split('=', 1) for item in (config.get('Env') or []))
    new_env = dict(item.split('=', 1) for item in (image_info['Config'].get('Env') or []))
    new_env.update({key: value for key, value in runtime_env.items() if old_env.get(key) != value})
    config['Env'] = [f'{key}={value}' for key, value in new_env.items()]
    config['Labels'] = {**(config.get('Labels') or {}),
                        **(image_info['Config'].get('Labels') or {}),
                        'com.docker.compose.image': image_info['Id'],
                        'io.sub2api.update-job': JOB['id']}
    config['Healthcheck'] = image_info['Config'].get('Healthcheck') or config.get('Healthcheck')
    config['HostConfig'] = copy.deepcopy(old['HostConfig'])
    # Bootstrap the private control link without touching database containers.
    updater = docker('GET', '/containers/sub2api-updater/json')
    token_mount = next(m for m in updater['Mounts'] if m['Destination'] == '/run/secrets/updater_token')
    mounts = config['HostConfig'].get('Mounts') or []
    config['HostConfig']['Mounts'] = [m for m in mounts if m.get('Target') != '/run/secrets/updater_token']
    config['HostConfig']['Mounts'].append({'Type': 'bind', 'Source': token_mount['Source'],
                                         'Target': '/run/secrets/updater_token', 'ReadOnly': True})
    env = dict(item.split('=', 1) for item in config['Env'])
    env.update(MANAGED_UPDATER_URL='http://sub2api-updater:8099',
               MANAGED_UPDATER_TOKEN_FILE='/run/secrets/updater_token')
    config['Env'] = [f'{key}={value}' for key, value in env.items()]
    config['NetworkingConfig'] = {'EndpointsConfig': {
        name: {'Aliases': [APP]} for name in old['NetworkSettings']['Networks']}}
    backup = f'{APP}-previous-{JOB["id"][:8]}'
    progress('deploying', previous_container=backup, previous_id=old['Id'], previous_image=old['Image'], candidate_image=image,
             commit=revision, deployment_started=True)
    try:
        docker('POST', f'/containers/{APP}/stop?t=60')
        docker('POST', f'/containers/{APP}/rename?name={backup}')
        docker('POST', f'/containers/create?name={APP}', config)
        docker('POST', f'/containers/{APP}/start')
        if not health(APP, revision):
            raise RuntimeError('New application did not become healthy')
    except Exception as error:
        try:
            recovered = recover_deployment()
        except Exception as recovery_error:
            raise RuntimeError(f'Deployment failed: {redact(error)}. Recovery needs operator action: {redact(recovery_error)}') from None
        raise RuntimeError(f'Deployment failed; previous application restored={recovered}. '
                           f'Database was not restored or deleted. {redact(error)}') from None
    # Retain stopped previous container for recovery; no data volumes are removed.
    run(['docker', 'tag', image, IMAGE], cwd=STATE)


def worker():
    published = False
    try:
        progress('checking', state='checking')
        release = latest(force=True)
        tag = release['tag_name']
        progress('syncing', state='syncing', target_version=tag[1:])
        revision, changed = sync_release(tag)
        missing = contract_errors()
        if missing:
            raise RuntimeError('Custom integrations require repair: ' + ', '.join(missing))
        progress('building', state='building', commit=revision)
        image = 'sub2api-managed:' + revision[:12]
        dockerfile = (REPO / 'Dockerfile').read_text(encoding='utf-8')
        # Built-in BuildKit supports the same directives. Avoid an unnecessary
        # Dockerfile frontend registry lookup, a common failure on this host.
        dockerfile = '\n'.join(line for line in dockerfile.splitlines() if not line.startswith('# syntax=')) + '\n'
        run(['docker', 'build', '--file', '-', '--label', f'org.opencontainers.image.revision={revision}',
             '--build-arg', f'COMMIT={revision}', '--build-arg', f'VERSION={tag[1:]}', '-t', image, '.'],
            data=dockerfile.encode(), timeout=3600)
        progress('publishing', state='publishing')
        # Preserve a repair branch and never force-push over concurrent user work.
        branch = 'managed/update-' + JOB['id'][:12]
        git('push', 'origin', f'HEAD:refs/heads/{branch}')
        progress('publishing', repair_branch=branch)
        if changed:
            git('push', 'origin', 'HEAD:refs/heads/main')
        else:
            git('fetch', '--no-tags', 'origin', 'main')
            if git('rev-parse', 'origin/main')[1] != revision:
                raise RuntimeError('GitHub main changed during build; retry to include the latest custom work')
        published = True
        progress('deploying', state='deploying', github_published=True)
        deploy(image, revision)
        progress('complete', state='succeeded', finished_at=now())
    except Exception as error:
        progress('failed', state='failed', error=redact(error)[-4000:], github_published=published,
                 finished_at=now())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Access headers and bodies must never enter logs.

    def send(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        return hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + TOKEN)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path)
        if path.path == '/health':
            return self.send(200, {'status': 'ok'})
        if not self.authorized():
            return self.send(401, {'error': 'Unauthorized'})
        if path.path == '/status':
            with LOCK:
                return self.send(200, copy.deepcopy(JOB))
        if path.path == '/check':
            params = urllib.parse.parse_qs(path.query)
            try:
                release = latest(params.get('force') == ['true'])
                current = params.get('current', [''])[0]
                def version(value):
                    return tuple(map(int, value.lstrip('v').split('.')))
                return self.send(200, {'current_version': current, 'latest_version': release['tag_name'][1:],
                    'has_update': version(current) < version(release['tag_name']), 'managed_updates': True,
                    'build_type': 'release', 'cached': False, 'release_info': {
                        'name': release.get('name', ''), 'body': release.get('body', ''),
                        'published_at': release.get('published_at', ''), 'html_url': release.get('html_url', '')}})
            except Exception as error:
                return self.send(502, {'error': redact(error)})
        return self.send(404, {'error': 'Not found'})

    def do_POST(self):
        if not self.authorized():
            return self.send(401, {'error': 'Unauthorized'})
        if self.path != '/jobs':
            return self.send(404, {'error': 'Not found'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self.send(400, {'error': 'Invalid Content-Length'})
        if self.headers.get('Transfer-Encoding') or length > 4096 or length < 0:
            return self.send(413, {'error': 'Request too large'})
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except (ValueError, TypeError):
            return self.send(400, {'error': 'Invalid JSON'})
        key = body.get('operation_id') if isinstance(body, dict) else None
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,128}', key):
            return self.send(400, {'error': 'Invalid operation ID'})
        with LOCK:
            if JOB['state'] in ACTIVE or JOB.get('operation_id') == key:
                return self.send(202, copy.deepcopy(JOB))
            JOB.clear()
            JOB.update(id=uuid.uuid4().hex, operation_id=key, state='queued', stage='queued', started_at=now())
            progress('queued')
            threading.Thread(target=worker, daemon=True).start()
            return self.send(202, copy.deepcopy(JOB))


if __name__ == '__main__':
    STATE.mkdir(exist_ok=True)
    saved = STATE / 'job.json'
    if saved.exists():
        JOB = json.loads(saved.read_text(encoding='utf-8'))
        if JOB.get('state') in ACTIVE:
            # Never replay a build or push after restart. Reconcile a recorded
            # container switch before accepting another job.
            message = 'Updater restarted; the unfinished update was not replayed.'
            if JOB.get('deployment_started'):
                try:
                    current = docker('GET', f'/containers/{APP}/json', missing_ok=True)
                    if current and health(APP, JOB.get('commit'), seconds=35):
                        progress('complete', state='succeeded', finished_at=now())
                    else:
                        message += f' Previous application restored={recover_deployment()}.'
                        progress('interrupted', state='failed', error=message, finished_at=now())
                except Exception as error:
                    progress('interrupted', state='failed', error=message + ' ' + redact(error), finished_at=now())
            else:
                progress('interrupted', state='failed', error=message, finished_at=now())
    ThreadingHTTPServer(('0.0.0.0', 8099), Handler).serve_forever()
