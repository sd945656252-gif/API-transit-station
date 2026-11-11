import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import urllib.request

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from release_protocol import GitHub, SafeRedirect, validate_manifest

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


if __name__ == '__main__':
    unittest.main()
