"""Shared release contract. Only complete, repository-owned releases are deployable."""
import hashlib
import json
from pathlib import Path
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request

MANIFEST_NAME = 'managed-release.json'
RELEASE_PATTERN = r'managed-v\d+\.\d+\.\d+-[0-9a-f]{12}'


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError('Invalid stable version')
    return tuple(map(int, value.lstrip('v').split('.')))


def validate_manifest(data, repository, release_tag=None):
    if not isinstance(data, dict) or data.get('schema_version') != 1 or data.get('repository') != repository:
        raise ValueError('Release manifest does not belong to the configured repository')
    commit = data.get('commit', '')
    if not re.fullmatch(r'[0-9a-f]{40}', commit) or not re.fullmatch(r'[0-9a-f]{40}', data.get('official_commit', '')):
        raise ValueError('Release commit is invalid')
    version_tuple(data.get('version'))
    expected_tag = f'managed-v{data["version"]}-{commit[:12]}'
    if data.get('tag') != expected_tag or (release_tag and release_tag != expected_tag):
        raise ValueError('Release tag and manifest do not match')
    artifacts = data.get('artifacts')
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError('Release has no Docker images')
    for platform, artifact in artifacts.items():
        if platform not in ('linux/amd64', 'linux/arm64') or not isinstance(artifact, dict):
            raise ValueError('Unsupported release platform')
        if artifact.get('name') != f'sub2api-{platform.replace("/", "-")}-{commit[:12]}.tar.gz':
            raise ValueError('Invalid image asset name')
        if not re.fullmatch(r'[0-9a-f]{64}', artifact.get('sha256', '')):
            raise ValueError('Image checksum is missing')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', artifact.get('image_id', '')):
            raise ValueError('Immutable Docker configuration digest is missing')
        if type(artifact.get('size')) is not int or not 0 < artifact['size'] <= 2_000_000_000:
            raise ValueError('Image asset size is invalid')
    return data


def archive_configuration(path, commit, expected_digest=None):
    """Docker classic and containerd stores expose different image IDs.

    The archived configuration digest and layer digests are portable identities.
    Read members directly; never extract archive paths onto the host filesystem.
    """
    def read_member(archive, name):
        members = [member for member in archive.getmembers() if member.name == name]
        if len(members) != 1 or not members[0].isfile() or members[0].size > 2_000_000:
            raise ValueError('Image archive metadata is missing, duplicated or too large')
        return archive.extractfile(members[0]).read()
    with tarfile.open(path, 'r:*') as archive:
        manifests = json.loads(read_member(archive, 'manifest.json'))
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise ValueError('Expected exactly one image in the Docker archive')
        entry = manifests[0]
        tag = 'sub2api-managed:' + commit
        tags = entry.get('RepoTags') or []
        if len(tags) != 1 or tags[0].removeprefix('docker.io/library/') != tag:
            raise ValueError('Image archive does not identify the published source tag')
        raw = read_member(archive, entry['Config'])
        digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
        if expected_digest and digest != expected_digest:
            raise ValueError('Archived image configuration digest does not match the release')
        config = json.loads(raw)
        if (config.get('config', {}).get('Labels') or {}).get('org.opencontainers.image.revision') != commit:
            raise ValueError('Archived image source commit does not match the release')
        return {'digest': digest, 'configuration': config, 'tag': tag}


def validate_loaded_image(image, archived, platform):
    config = archived['configuration']
    if image['Os'] + '/' + image['Architecture'] != platform or config['os'] + '/' + config['architecture'] != platform:
        raise ValueError('Loaded image platform does not match the release')
    if image.get('RootFS', {}).get('Layers') != config.get('rootfs', {}).get('diff_ids'):
        raise ValueError('Loaded image filesystem layers do not match the verified archive')
    if any(image['Config'].get(key) != value for key, value in config['config'].items()):
        raise ValueError('Loaded image runtime configuration does not match the verified archive')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image.get('Id', '')):
        raise ValueError('Docker did not return an immutable local image identity')
    return image['Id']


class GitHubError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'GitHub HTTP {status}; check repository access, Actions status and connectivity')


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != 'https' or not (target.hostname in ('github.com', 'api.github.com') or
                (target.hostname or '').endswith('.githubusercontent.com')):
            raise ValueError('Unexpected release asset redirect')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and target.hostname != urllib.parse.urlsplit(req.full_url).hostname:
            redirected.remove_header('Authorization')
        return redirected


class GitHub:
    def __init__(self, token):
        self.token = token
        self.opener = urllib.request.build_opener(SafeRedirect())

    def open(self, path, accept='application/vnd.github+json', method='GET', payload=None):
        segments = urllib.parse.unquote(path.split('?', 1)[0]).split('/')
        if not path.startswith('repos/') or any(part in ('.', '..') for part in segments):
            raise ValueError('Invalid GitHub API resource')
        headers = {'Accept': accept, 'User-Agent': 'Sub2API-Managed-Releases',
                   'X-GitHub-Api-Version': '2022-11-28', 'Cache-Control': 'no-cache'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        body = None if payload is None else json.dumps(payload).encode('utf-8')
        if body is not None:
            headers['Content-Type'] = 'application/json'
        try:
            return self.opener.open(urllib.request.Request('https://api.github.com/' + path,
                data=body, headers=headers, method=method), timeout=60)
        except urllib.error.HTTPError as error:
            raise GitHubError(error.code) from None

    def json(self, path, method='GET', payload=None):
        with self.open(path, method=method, payload=payload) as response:
            raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError('GitHub metadata exceeds the size limit')
        return json.loads(raw) if raw else None

    def asset(self, repository, asset_id, destination=None, expected=None):
        if type(asset_id) is not int or asset_id <= 0:
            raise ValueError('Invalid release asset ID')
        limit = expected['size'] if expected else 128_000
        checksum = hashlib.sha256()
        size = 0
        chunks = []
        output = Path(destination).open('wb') if destination else None
        try:
            with self.open(f'repos/{repository}/releases/assets/{asset_id}', 'application/octet-stream') as response:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise ValueError('Release asset exceeds the declared size')
                    checksum.update(chunk)
                    if output:
                        output.write(chunk)
                    else:
                        chunks.append(chunk)
            if expected and (size != expected['size'] or checksum.hexdigest() != expected['sha256']):
                raise ValueError('Release image checksum or size does not match; deployment was stopped')
            return None if output else b''.join(chunks)
        finally:
            if output:
                output.close()
