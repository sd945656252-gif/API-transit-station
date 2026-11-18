"""CI owns source synchronization and publication; deployments only consume releases."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/managed-updater'))
from release_protocol import (GitHub, GitHubError, MANIFEST_NAME, validate_manifest,
                              version_tuple, archive_configuration, validate_loaded_image)

OFFICIAL = 'Wei-Shaw/sub2api'
REPOSITORY = os.environ.get('GITHUB_REPOSITORY', '')
API = GitHub(os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN', ''))
REVIEWED_NEW_SQL = {
    # v0.2.23: adds nullable/defaulted columns and replaces rollup functions.
    # UPDATE/DELETE are inside function bodies; no existing rows are rewritten at migration time.
    'backend/migrations/251_group_usage_rollup_nonblocking_build.sql':
        'da504646104cec14aa7bcf5a0af6f06f5cd40883459379ae915db27c1d4cec8b',
}


def git(*args, check=True, data=None):
    result = subprocess.run(['git', *args], cwd=ROOT, input=data, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = result.stdout.decode('utf-8', errors='replace').strip()
    if check and result.returncode:
        raise RuntimeError('Git operation failed: ' + output[-2500:])
    return result.returncode, output


def contracts():
    required = json.loads((ROOT / 'deploy/managed-updater/contracts.json').read_text(encoding='utf-8'))
    for path, markers in required.items():
        file = ROOT / path
        value = file.read_text(encoding='utf-8') if file.is_file() else ''
        if any(marker not in value for marker in markers):
            raise RuntimeError('Custom integration requires repair: ' + path)


def check_migrations(baseline, target):
    changes = git('diff', '--no-renames', '--name-status', baseline, target, '--',
                  ':(glob)backend/migrations/*.sql')[1]
    for line in changes.splitlines():
        change, path = line.split('\t', 1)
        if change != 'A':
            raise RuntimeError('Existing database migration changed; data compatibility review is required: ' + path)
        raw = subprocess.check_output(['git', 'show', f'{target}:{path}'], cwd=ROOT)
        if hashlib.sha256(raw).hexdigest() == REVIEWED_NEW_SQL.get(path):
            continue
        sql = re.sub(r'/\*.*?\*/|--[^\n]*', '', raw.decode('utf-8'), flags=re.S)
        if re.search(r'\b(DROP|TRUNCATE|DELETE|UPDATE|RENAME)\b|\bALTER\s+COLUMN\b', sql, re.I):
            raise RuntimeError('Data-changing migration requires review: ' + path)


def sync_official(release):
    tag = release['tag_name']
    version_tuple(tag)
    if release.get('prerelease') or release.get('draft'):
        raise RuntimeError('Only official stable releases may be synchronized')
    baseline = (ROOT / '.upstream-ref').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'[0-9a-f]{40}', baseline):
        raise RuntimeError('Invalid official baseline')
    ref = API.json(f'repos/{OFFICIAL}/git/ref/tags/{tag}')['object']
    for _ in range(4):
        if ref.get('type') != 'tag':
            break
        ref = API.json(f'repos/{OFFICIAL}/git/tags/{ref["sha"]}')['object']
    target = ref.get('sha', '')
    if ref.get('type') != 'commit' or not re.fullmatch(r'[0-9a-f]{40}', target):
        raise RuntimeError('Official tag does not identify a commit')
    if baseline == target:
        return baseline
    status = API.json(f'repos/{OFFICIAL}/compare/{baseline}...{target}').get('status')
    if status == 'behind':
        return baseline
    if status != 'ahead':
        raise RuntimeError('Official baseline diverged; integration review is required')
    for ref_sha in (baseline, target):
        git('fetch', '--depth=1', '--no-tags', f'https://github.com/{OFFICIAL}.git', ref_sha)
    check_migrations(baseline, target)
    patch = subprocess.check_output(['git', 'diff', '--binary', baseline, target, '--', '.',
                                     ':(exclude).github/workflows/**'], cwd=ROOT)
    code, output = git('apply', '--3way', '--index', '--whitespace=nowarn', '-', data=patch, check=False)
    conflicts = git('diff', '--name-only', '--diff-filter=U')[1].splitlines()
    if code and not conflicts:
        raise RuntimeError('Official changes could not be applied: ' + output[-1200:])
    with tempfile.TemporaryDirectory(prefix='sub2api-merge-') as temporary:
        for path in conflicts:
            stages = git('ls-files', '-u', '--', path)[1]
            if not all(f' {stage}\t' in stages for stage in (1, 2, 3)):
                raise RuntimeError('Add/delete conflict requires repair: ' + path)
            files = []
            for stage in (2, 1, 3):
                file = Path(temporary) / str(stage)
                file.write_bytes(subprocess.check_output(['git', 'show', f':{stage}:{path}'], cwd=ROOT))
                files.append(str(file))
            merged = subprocess.run(['git', 'merge-file', '--theirs', '-p', *files], stdout=subprocess.PIPE)
            if merged.returncode or b'\0' in merged.stdout:
                raise RuntimeError('Binary or unresolved conflict requires repair: ' + path)
            (ROOT / path).write_bytes(merged.stdout)
            git('add', '--', path)
    contracts()
    (ROOT / '.upstream-ref').write_text(target + '\n', encoding='utf-8')
    git('add', '-A')
    git('commit', '-m', f'chore: sync official {tag}, preserve custom integrations')
    return target


def output(**values):
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
        for key, value in values.items():
            stream.write(f'{key}={str(value).lower() if isinstance(value, bool) else value}\n')


def prepare():
    base = git('rev-parse', 'HEAD')[1]
    git('config', 'user.name', 'Sub2API Release Bot')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    official = API.json(f'repos/{OFFICIAL}/releases/latest')
    official_sha = sync_official(official)
    contracts()
    revision = git('rev-parse', 'HEAD')[1]
    version = official['tag_name'].lstrip('v')
    tag = f'managed-v{version}-{revision[:12]}'
    try:
        existing = API.json(f'repos/{REPOSITORY}/releases/tags/{tag}')
    except GitHubError as error:
        if error.status != 404:
            raise
        existing = None
    if existing and not existing.get('draft'):
        asset = next((a for a in existing.get('assets', []) if a['name'] == MANIFEST_NAME), None)
        if not asset:
            raise RuntimeError('Published release is incomplete; repair it before publishing again')
        manifest = validate_manifest(json.loads(API.asset(REPOSITORY, asset['id'])), REPOSITORY, tag)
        names = {a['name'] for a in existing['assets'] if a.get('state') == 'uploaded'}
        if any(a['name'] not in names for a in manifest['artifacts'].values()):
            raise RuntimeError('Published Docker image is missing')
        channel = API.json(f'repos/{REPOSITORY}/releases/latest')
        if channel['id'] != existing['id']:
            API.json(f'repos/{REPOSITORY}/releases/{existing["id"]}', 'PATCH', {'make_latest': 'true'})
        output(publish=False)
        return
    git('push', 'origin', f'HEAD:refs/heads/managed/candidate-{revision[:12]}')
    path = Path(os.environ['RUNNER_TEMP']) / 'managed-source.json'
    path.write_text(json.dumps({'base': base, 'commit': revision, 'version': version, 'tag': tag,
        'official_commit': official_sha, 'official_tag': official['tag_name'],
        'notes': official.get('body', '')}, ensure_ascii=False), encoding='utf-8')
    output(publish=True, revision=revision, version=version, tag=tag,
           image=f'sub2api-managed:{revision}', metadata=str(path))


def publish(args):
    metadata = json.loads(Path(args.metadata).read_text(encoding='utf-8'))
    archive = Path(args.archive)
    image = f'sub2api-managed:{metadata["commit"]}'
    details = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image]))[0]
    if (details['Config'].get('Labels') or {}).get('org.opencontainers.image.revision') != metadata['commit']:
        raise RuntimeError('Built image does not identify the prepared source commit')
    platform = details['Os'] + '/' + details['Architecture']
    name = f'sub2api-{platform.replace("/", "-")}-{metadata["commit"][:12]}.tar.gz'
    artifact = archive.with_name(name)
    archive.rename(artifact)
    archived = archive_configuration(artifact, metadata['commit'])
    validate_loaded_image(details, archived, platform)
    checksum = hashlib.sha256()
    with artifact.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
    manifest = {'schema_version': 1, 'repository': REPOSITORY, 'tag': metadata['tag'],
        'version': metadata['version'], 'commit': metadata['commit'], 'official_commit': metadata['official_commit'],
        'built_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'artifacts': {platform: {'name': name, 'sha256': checksum.hexdigest(),
                                'size': artifact.stat().st_size, 'image_id': archived['digest']}}}
    validate_manifest(manifest, REPOSITORY)
    manifest_path = artifact.with_name(MANIFEST_NAME)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    try:
        release = API.json(f'repos/{REPOSITORY}/releases/tags/{metadata["tag"]}')
    except GitHubError as error:
        if error.status != 404:
            raise
        release = API.json(f'repos/{REPOSITORY}/releases', 'POST', {'tag_name': metadata['tag'],
            'target_commitish': metadata['commit'], 'name': f'Sub2API {metadata["version"]} · {metadata["commit"][:12]}',
            'body': f'Official core: {metadata["official_tag"]}\nSource: `{metadata["commit"]}`\n\n'
                'Verified custom Docker image. Each deployment updates independently.\n\n' + metadata['notes'],
            'draft': True, 'prerelease': False})
    if not release.get('draft'):
        raise RuntimeError('Refusing to replace an already published release')
    subprocess.run(['gh', 'release', 'upload', metadata['tag'], str(artifact), str(manifest_path),
                    '--clobber', '--repo', REPOSITORY], check=True)
    git('fetch', '--no-tags', 'origin', 'main')
    if git('rev-parse', 'origin/main')[1] != metadata['base']:
        raise RuntimeError('GitHub main changed during the build; draft remains unpublished. Retry the workflow.')
    git('push', 'origin', 'HEAD:refs/heads/main')
    uploaded = API.json(f'repos/{REPOSITORY}/releases/{release["id"]}')
    assets = {a['name']: a for a in uploaded['assets'] if a.get('state') == 'uploaded'}
    if assets.get(name, {}).get('size') != artifact.stat().st_size or MANIFEST_NAME not in assets:
        raise RuntimeError('Release assets were not completely uploaded')
    API.json(f'repos/{REPOSITORY}/releases/{release["id"]}', 'PATCH', {'draft': False, 'make_latest': 'true'})
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
        stream.write(f'Published `{metadata["tag"]}` with SHA-256 verified Docker image. All configured deployments can discover it.\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare')
    publisher = commands.add_parser('publish')
    publisher.add_argument('--metadata', required=True)
    publisher.add_argument('--archive', required=True)
    arguments = parser.parse_args()
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', REPOSITORY):
        raise RuntimeError('GITHUB_REPOSITORY is required')
    prepare() if arguments.command == 'prepare' else publish(arguments)
