import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch, Mock
import urllib.request

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from release_protocol import GitHub, SafeRedirect, validate_manifest, archive_configuration, validate_loaded_image

REPOSITORY = 'example/gateway'
COMMIT = 'a' * 40


def manifest():
    return {'schema_version': 1, 'repository': REPOSITORY, 'version': '0.2.21',
        'commit': COMMIT, 'official_commit': 'b' * 40, 'tag': 'managed-v0.2.21-' + COMMIT[:12],
        'artifacts': {'linux/amd64': {'name': 'sub2api-linux-amd64-' + COMMIT[:12] + '.tar.gz',
            'sha256': hashlib.sha256(b'image').hexdigest(), 'size': 5, 'image_id': 'sha256:' + 'c' * 64}}}


def load_server():
    read = Path.read_text
    def content(path, *args, **kwargs):
        if str(path).replace('\\', '/').startswith('/run/secrets/'):
            return 'x' * 40
        return read(path, *args, **kwargs)
    spec = importlib.util.spec_from_file_location('managed_test_server', SOURCE / 'server.py')
    module = importlib.util.module_from_spec(spec)
    with patch.object(Path, 'read_text', content):
        spec.loader.exec_module(module)
    return module


class ReleaseContractTests(unittest.TestCase):
    def test_archive_identity_is_portable_across_docker_image_stores(self):
        config = {'os': 'linux', 'architecture': 'amd64', 'rootfs': {'diff_ids': ['sha256:layer']},
                  'config': {'Labels': {'org.opencontainers.image.revision': COMMIT}, 'Cmd': ['/app/sub2api']}}
        raw = json.dumps(config).encode()
        digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.tar.gz'
            with tarfile.open(path, 'w:gz') as archive:
                entries = {'manifest.json': json.dumps([{'Config': 'config.json',
                    'RepoTags': ['sub2api-managed:' + COMMIT]}]).encode(), 'config.json': raw}
                for name, content in entries.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
            archived = archive_configuration(path, COMMIT, digest)
            for local_id in (digest, 'sha256:' + 'f' * 64):
                image = {'Id': local_id, 'Os': 'linux', 'Architecture': 'amd64',
                         'RootFS': {'Layers': ['sha256:layer']}, 'Config': config['config']}
                self.assertEqual(validate_loaded_image(image, archived, 'linux/amd64'), local_id)
            image['RootFS']['Layers'] = ['sha256:wrong']
            with self.assertRaisesRegex(ValueError, 'layers'):
                validate_loaded_image(image, archived, 'linux/amd64')
            with self.assertRaisesRegex(ValueError, 'digest'):
                archive_configuration(path, COMMIT, 'sha256:' + '0' * 64)

    def test_commit_comparison_is_valid_but_directory_traversal_is_not(self):
        client = GitHub('test')
        client.opener.open = Mock(return_value=io.BytesIO(b'{}'))
        self.assertEqual(client.json('repos/example/gateway/compare/' + 'a' * 40 + '...' + 'b' * 40), {})
        with self.assertRaises(ValueError):
            client.open('repos/example/../other')

    def test_complete_repository_owned_release_is_accepted(self):
        self.assertEqual(validate_manifest(manifest(), REPOSITORY)['commit'], COMMIT)

    def test_wrong_repository_and_unbound_tags_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_manifest(manifest(), 'other/repository')
        with self.assertRaises(ValueError):
            validate_manifest(manifest(), REPOSITORY, 'managed-v0.2.22-' + COMMIT[:12])

    def test_invalid_checksums_sizes_and_paths_are_rejected(self):
        for field, value in [('size', True), ('size', 0), ('sha256', ''), ('name', '../../image.tar.gz')]:
            with self.subTest(field=field, value=value):
                data = manifest()
                data['artifacts']['linux/amd64'][field] = value
                with self.assertRaises(ValueError):
                    validate_manifest(data, REPOSITORY)

    def test_redirect_does_not_forward_private_credentials_to_asset_host(self):
        request = urllib.request.Request('https://api.github.com/repos/example/gateway/releases/assets/1',
                                         headers={'Authorization': 'Bearer private'})
        redirect = SafeRedirect().redirect_request(request, None, 302, 'Found', {},
                                                   'https://release-assets.githubusercontent.com/file')
        self.assertIsNone(redirect.get_header('Authorization'))
        with self.assertRaises(ValueError):
            SafeRedirect().redirect_request(request, None, 302, 'Found', {}, 'https://other.example/file')

    def test_corrupt_download_is_rejected_before_docker_load(self):
        client = GitHub('test')
        client.open = Mock(return_value=io.BytesIO(b'wrong'))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'checksum'):
                client.asset(REPOSITORY, 1, Path(directory) / 'image', manifest()['artifacts']['linux/amd64'])


class IndependentInstallationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server()

    def test_two_instances_discover_same_release_and_update_independently(self):
        server = self.server
        ready = {'manifest': manifest(), 'release': {}}
        catalog = {'ready': ready, 'preparation': {'state': 'ready'}}
        with patch.object(server, 'docker', return_value={'Config': {'Labels': {
                'org.opencontainers.image.revision': 'd' * 40}}}):
            computer_a = server.check_result('0.2.21', catalog)
            computer_b = server.check_result('0.2.21', catalog)
        self.assertTrue(computer_a['has_update'])
        self.assertTrue(computer_b['has_update'])
        with patch.object(server, 'docker', return_value={'Config': {'Labels': {
                'org.opencontainers.image.revision': COMMIT}}}):
            self.assertFalse(server.check_result('0.2.21', catalog)['has_update'])
        self.assertTrue(computer_b['has_update'])

    def test_catalog_uses_github_latest_channel_instead_of_release_list_order(self):
        server = self.server
        data = manifest()
        artifact = data['artifacts']['linux/amd64']
        release = {'tag_name': data['tag'], 'assets': [
            {'name': 'managed-release.json', 'id': 1, 'state': 'uploaded'},
            {'name': artifact['name'], 'id': 2, 'state': 'uploaded', 'size': artifact['size']}]}
        paths = []
        def github(path):
            paths.append(path)
            if path == 'repos/' + server.OFFICIAL + '/releases/latest':
                return {'tag_name': 'v0.2.21'}
            if path == 'repos/' + REPOSITORY + '/releases/latest':
                return release
            if '/git/ref/tags/' in path:
                return {'object': {'type': 'commit', 'sha': COMMIT}}
            if '/actions/workflows/' in path:
                return {'workflow_runs': []}
            raise AssertionError('Unexpected GitHub resource: ' + path)
        with patch.object(server, 'REPOSITORY', REPOSITORY), patch.object(server, 'github', side_effect=github), \
                patch.object(server.API, 'asset', return_value=json.dumps(data).encode()):
            result = server.latest(force=True)
        self.assertEqual(result['ready']['manifest']['commit'], COMMIT)
        self.assertFalse(any('/releases?' in path for path in paths))

    def test_no_prepared_image_does_not_offer_update(self):
        with patch.object(self.server, 'docker', return_value={'Config': {'Labels': {}}}):
            result = self.server.check_result('0.2.21', {'ready': None, 'preparation': {'state': 'preparing'}})
        self.assertFalse(result['has_update'])
        self.assertEqual(result['managed_release']['preparation']['state'], 'preparing')

    def test_older_official_version_is_not_offered(self):
        with patch.object(self.server, 'docker', return_value={'Config': {'Labels': {}}}):
            result = self.server.check_result('0.2.22', {'ready': {'manifest': manifest(), 'release': {}},
                                                        'preparation': {'state': 'ready'}})
        self.assertFalse(result['has_update'])

    def test_corrupt_image_never_replaces_application(self):
        server = self.server
        server.JOB = {'id': 'test-job'}
        ready = {'manifest': manifest(), 'assets': {manifest()['artifacts']['linux/amd64']['name']: {'id': 1}}}
        with tempfile.TemporaryDirectory() as directory, patch.object(server, 'STATE', Path(directory)), \
                patch.object(server, 'docker', return_value={'OSType': 'linux', 'Architecture': 'x86_64'}), \
                patch.object(server, 'progress') as progress, patch.object(server, 'run') as run, \
                patch.object(server, 'deploy') as deploy, \
                patch.object(server.API, 'asset', side_effect=ValueError('checksum mismatch')):
            server.worker(ready)
        deploy.assert_not_called()
        run.assert_not_called()
        self.assertEqual(progress.call_args.kwargs['state'], 'failed')


class DeploymentRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.server = load_server()
        self.server.JOB = {'id': 'deployment-test'}
        self.policy = {'Name': 'on-failure', 'MaximumRetryCount': 7}
        self.old = {'Id': 'original-container', 'Image': 'old-image',
                    'Config': {'Image': 'old-image', 'Env': ['CUSTOM=value'], 'Labels': {}},
                    'HostConfig': {'RestartPolicy': copy.deepcopy(self.policy)},
                    'NetworkSettings': {'Networks': {'application-network': {}}}}
        self.current = self.old
        self.previous = None
        self.events = []
        for name, options in (
                ('docker', {'side_effect': self.docker}),
                ('progress', {'side_effect': self.progress}),
                ('run', {'return_value': (0, '')}),
                ('health', {'return_value': True})):
            patcher = patch.object(self.server, name, **options)
            setattr(self, name + '_mock', patcher.start())
            self.addCleanup(patcher.stop)

    def progress(self, stage, **fields):
        self.events.append(('record', stage))
        self.server.JOB.update(stage=stage, **fields)

    def docker(self, method, path, payload=None, **kwargs):
        self.events.append((method, path, copy.deepcopy(payload)))
        if method == 'GET':
            if path == '/containers/sub2api/json':
                return self.current
            if path == '/containers/sub2api-previous-deployme/json':
                return self.previous
            if path == '/containers/sub2api-updater/json':
                return {'Mounts': [{'Destination': '/run/secrets/updater_token', 'Source': '/private/token'}]}
            if path == '/images/old-image/json':
                return {'Config': self.old['Config']}
            if path == '/images/new-image/json':
                return {'Id': 'new-image', 'Config': {'Labels': {'org.opencontainers.image.revision': COMMIT}}}
        if method == 'POST' and path == '/containers/sub2api/update':
            self.current['HostConfig']['RestartPolicy'] = copy.deepcopy(payload['RestartPolicy'])
        elif method == 'POST' and path == '/containers/sub2api/rename?name=sub2api-previous-deployme':
            self.previous, self.current = self.current, None
        elif method == 'POST' and path == '/containers/create?name=sub2api':
            self.current = {'Id': 'candidate-container', 'Config': payload,
                            'HostConfig': payload['HostConfig']}
        elif method == 'DELETE' and path == '/containers/sub2api?force=true':
            self.current = None
        elif method == 'POST' and path == '/containers/sub2api-previous-deployme/rename?name=sub2api':
            self.current, self.previous = self.previous, None
        elif method == 'POST' and path in ('/containers/sub2api/start', '/containers/sub2api/stop?t=60'):
            pass
        else:
            raise AssertionError('Unexpected Docker operation: ' + method + ' ' + path)

    def test_success_disarms_backup_before_stop_and_preserves_new_restart_policy(self):
        self.server.deploy('new-image', COMMIT)
        self.assertEqual(self.previous['HostConfig']['RestartPolicy']['Name'], 'no')
        self.assertEqual(self.current['HostConfig']['RestartPolicy'], self.policy)
        self.assertEqual(self.server.JOB['previous_restart_policy'], self.policy)
        actions = [event[:2] for event in self.events]
        self.assertLess(actions.index(('record', 'deploying')),
                        actions.index(('POST', '/containers/sub2api/update')))
        self.assertLess(actions.index(('POST', '/containers/sub2api/update')),
                        actions.index(('POST', '/containers/sub2api/stop?t=60')))

    def test_failed_candidate_restores_original_container_and_restart_policy(self):
        self.health_mock.side_effect = [False, True]
        with self.assertRaisesRegex(RuntimeError, 'previous application restored=True'):
            self.server.deploy('new-image', COMMIT)
        self.assertEqual(self.current['Id'], 'original-container')
        self.assertEqual(self.current['HostConfig']['RestartPolicy'], self.policy)
        self.assertIsNone(self.previous)
        self.server.run.assert_not_called()

    def test_restart_before_stop_recovers_policy_on_same_original_container(self):
        self.server.JOB.update(previous_id=self.old['Id'], previous_restart_policy=self.policy,
                               previous_container='sub2api-previous-deployme')
        self.old['HostConfig']['RestartPolicy'] = {'Name': 'no', 'MaximumRetryCount': 0}
        self.assertTrue(self.server.recover_deployment())
        self.assertEqual(self.current['HostConfig']['RestartPolicy'], self.policy)
        self.assertFalse(any(event[0] == 'DELETE' for event in self.events))

    def test_recovery_refuses_to_replace_unrelated_application(self):
        self.previous = self.old
        self.current = {'Id': 'external-container', 'Config': {'Labels': {}}}
        self.server.JOB.update(previous_id=self.old['Id'], previous_restart_policy=self.policy,
                               previous_container='sub2api-previous-deployme')
        with self.assertRaisesRegex(RuntimeError, 'outside this job'):
            self.server.recover_deployment()
        self.assertFalse(any(event[0] != 'GET' for event in self.events))


if __name__ == '__main__':
    unittest.main()
