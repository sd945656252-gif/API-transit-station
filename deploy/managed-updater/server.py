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
import shutil
import subprocess
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from release_protocol import (GitHub, GitHubError, MANIFEST_NAME, RELEASE_PATTERN, validate_manifest,
                              version_tuple, archive_configuration, validate_loaded_image)

STATE = Path('/state')
REPOSITORY = os.environ.get('CUSTOM_REPOSITORY', 'sd945656252-gif/API-transit-station')
APP = os.environ.get('APP_CONTAINER', 'sub2api')
IMAGE = os.environ.get('APP_IMAGE', 'sub2api-local:runninghub')
OFFICIAL = 'Wei-Shaw/sub2api'
LOCK = threading.RLock()
CHECK_LOCK = threading.Lock()
ACTIVE = {'queued', 'checking', 'downloading', 'verifying', 'loading', 'deploying'}
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
API = GitHub(GH_TOKEN)


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


def run(args, cwd=STATE, data=None, check=True, timeout=1800):
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


def github(path, method='GET', payload=None):
    return API.json(path, method, payload)


def latest(force=False):
    """One publication source for every installation; never merge or build here."""
    global CACHE, CACHE_TIME
    with CHECK_LOCK:
        if not force and CACHE is not None and time.monotonic() - CACHE_TIME < 60:
            return copy.deepcopy(CACHE)
        official = github(f'repos/{OFFICIAL}/releases/latest')
        official_version = official['tag_name'].lstrip('v')
        version_tuple(official_version)
        try:
            release = github(f'repos/{REPOSITORY}/releases/latest')
        except GitHubError as error:
            if error.status != 404:
                raise
            release = None
        if release and (release.get('draft') or release.get('prerelease') or
                        not re.fullmatch(RELEASE_PATTERN, release.get('tag_name', ''))):
            raise RuntimeError('The latest GitHub release is not a prepared managed release; check the publication workflow')
        ready = None
        if release:
            assets = {a['name']: a for a in release.get('assets', []) if a.get('state') == 'uploaded'}
            metadata = assets.get(MANIFEST_NAME)
            if not metadata:
                raise RuntimeError('Published release manifest is missing')
            manifest = validate_manifest(json.loads(API.asset(REPOSITORY, metadata['id'])), REPOSITORY, release['tag_name'])
            for artifact in manifest['artifacts'].values():
                remote = assets.get(artifact['name'])
                if not remote or remote['size'] != artifact['size']:
                    raise RuntimeError('Published release image is missing or incomplete')
            tag_ref = github(f'repos/{REPOSITORY}/git/ref/tags/{release["tag_name"]}')['object']
            if tag_ref.get('type') != 'commit' or tag_ref.get('sha') != manifest['commit']:
                raise RuntimeError('Published release tag does not match its source commit')
            ready = {'manifest': manifest, 'assets': assets, 'release': release}
        runs = github(f'repos/{REPOSITORY}/actions/workflows/sync-upstream.yml/runs?branch=main&per_page=1')['workflow_runs']
        run_info = runs[0] if runs else {}
        pending = not ready or version_tuple(official_version) > version_tuple(ready['manifest']['version'])
        preparation = {'state': 'ready' if ready else 'waiting', 'official_version': official_version,
                       'url': run_info.get('html_url', ''), 'repository': REPOSITORY}
        if run_info.get('status') in ('queued', 'in_progress', 'waiting', 'pending', 'requested'):
            preparation['state'] = 'preparing'
        elif run_info.get('conclusion') in ('failure', 'cancelled', 'timed_out', 'action_required'):
            preparation['state'] = 'failed'
        elif pending:
            preparation['state'] = 'waiting'
        CACHE = {'ready': ready, 'preparation': preparation}
        CACHE_TIME = time.monotonic()
        return copy.deepcopy(CACHE)


def check_result(current, catalog):
    version_tuple(current)
    info = docker('GET', f'/containers/{APP}/json')
    revision = (info['Config'].get('Labels') or {}).get('org.opencontainers.image.revision', '')
    ready = catalog['ready']
    manifest = ready['manifest'] if ready else None
    result = {'current_version': current, 'latest_version': manifest['version'] if manifest else '',
              'has_update': bool(manifest and manifest['commit'] != revision and
                                 version_tuple(manifest['version']) >= version_tuple(current)),
              'managed_updates': True, 'build_type': 'release', 'cached': False,
              'managed_release': {'repository': REPOSITORY, 'current_revision': revision,
                  'revision': manifest['commit'] if manifest else '',
                  'preparation': catalog['preparation']}}
    if ready:
        release = ready['release']
        result['release_info'] = {'name': release.get('name', ''), 'body': release.get('body', ''),
            'published_at': release.get('published_at', ''), 'html_url': release.get('html_url', '')}
    return result


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


def worker(ready):
    archive = STATE / ('image-' + JOB['id'] + '.tar.gz')
    try:
        manifest = ready['manifest']
        revision = manifest['commit']
        progress('checking', state='checking', commit=revision, target_version=manifest['version'],
                 release_tag=manifest['tag'], github_published=True)
        engine = docker('GET', '/info')
        architecture = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(engine['Architecture'], engine['Architecture'])
        platform = engine['OSType'] + '/' + architecture
        artifact = manifest['artifacts'].get(platform)
        if not artifact:
            raise RuntimeError('This release has no Docker image for ' + platform)
        if shutil.disk_usage(STATE).free < artifact['size'] * 3 + 300_000_000:
            raise RuntimeError('Insufficient disk space to download and load the release image')
        progress('downloading', state='downloading')
        API.asset(REPOSITORY, ready['assets'][artifact['name']]['id'], archive, artifact)
        progress('verifying', state='verifying')
        # The download completed only after checking its exact size and SHA-256.
        archived = archive_configuration(archive, revision, artifact['image_id'])
        progress('loading', state='loading')
        run(['docker', 'load', '--input', str(archive)], cwd=STATE, timeout=1200)
        image = docker('GET', '/images/' + archived['tag'] + '/json')
        local_image_id = validate_loaded_image(image, archived, platform)
        progress('deploying', state='deploying')
        deploy(local_image_id, revision)
        progress('complete', state='succeeded', finished_at=now())
    except Exception as error:
        progress('failed', state='failed', error=redact(error)[-4000:], finished_at=now())
    finally:
        archive.unlink(missing_ok=True)


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
                catalog = latest(params.get('force') == ['true'])
                return self.send(200, check_result(params.get('current', [''])[0], catalog))
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
        try:
            ready = latest(force=True)['ready']
            if ready is None:
                return self.send(409, {'error': 'GitHub has not published a complete managed Docker release yet'})
            info = docker('GET', f'/containers/{APP}/json')
            labels = info['Config'].get('Labels') or {}
            if labels.get('org.opencontainers.image.revision') == ready['manifest']['commit']:
                return self.send(409, {'error': 'This exact release is already installed'})
            installed = labels.get('org.opencontainers.image.version')
            if installed and version_tuple(installed) > version_tuple(ready['manifest']['version']):
                return self.send(409, {'error': 'Refusing to downgrade the installed application'})
        except Exception as error:
            return self.send(502, {'error': redact(error)})
        with LOCK:
            if JOB['state'] in ACTIVE or JOB.get('operation_id') == key:
                return self.send(202, copy.deepcopy(JOB))
            JOB.clear()
            JOB.update(id=uuid.uuid4().hex, operation_id=key, state='queued', stage='queued', started_at=now())
            progress('queued')
            threading.Thread(target=worker, args=(ready,), daemon=True).start()
            return self.send(202, copy.deepcopy(JOB))


if __name__ == '__main__':
    STATE.mkdir(exist_ok=True)
    saved = STATE / 'job.json'
    if saved.exists():
        JOB = json.loads(saved.read_text(encoding='utf-8'))
        if JOB.get('state') in ACTIVE:
            # Never replay a download after restart. Reconcile a recorded
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
