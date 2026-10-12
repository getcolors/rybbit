import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from rybbit import remote


class RemoteTests(unittest.TestCase):
    def config(self):
        return {'rybbit-host': 'test.example.com', 'rybbit-disable-signup': True}

    def test_secrets_retained_and_settings_updated(self):
        first = remote.stack_settings(self.config())
        config = {**self.config(), 'rybbit-host': 'other.example.com', 'rybbit-disable-signup': False}
        second = remote.stack_settings(config, first)
        a, b = remote.read_env(first), remote.read_env(second)
        for key in remote.SECRET_KEYS:
            self.assertEqual(a[key], b[key])
        self.assertEqual(b['BASE_URL'], 'https://other.example.com')
        self.assertEqual(b['DISABLE_SIGNUP'], 'false')

    def test_invalid_retained_file_is_rejected(self):
        with self.assertRaises(ValueError):
            remote.stack_settings(self.config(), 'POSTGRES_PASSWORD=only-one\n')

    def test_prepare_never_starts_application(self):
        calls = []
        with patch.object(remote, 'configure', return_value=(True, False)), patch.object(remote, 'fence') as fence, patch.object(remote, 'mount_stale', return_value=False), patch.object(remote, 'compose', side_effect=lambda *args, **kw: calls.append(args)):
            result = remote.main({'action': 'prepare', 'config': self.config()})
        fence.assert_called_once()
        self.assertEqual(calls, [('up', '-d', '--wait', 'postgres', 'clickhouse', 'redis')])
        self.assertTrue(result['fenced'])

    def test_incomplete_restore_blocks_start(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, 'ROOT', Path(directory)), patch.object(remote, 'configure') as configure:
            (Path(directory) / 'restore.json').write_text('{"complete":false}')
            with self.assertRaises(RuntimeError):
                remote.main({'action': 'converge', 'config': self.config()})
            configure.assert_not_called()

    def test_status_allowlists_container_metadata(self):
        row = {'Service': 'postgres', 'State': 'running', 'Health': 'healthy', 'Labels': 'SECRET'}
        with patch.object(remote, 'compose', return_value=Mock(returncode=0, stdout=json.dumps(row).encode())), patch.object(remote, 'run', return_value=Mock(returncode=0)):
            result = remote.main({'action': 'status'})
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(result['services'][0]['Service'], 'postgres')

    def test_secret_write_permissions_and_symlink_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'secret'
            remote.write(path, 'sensitive', 0o600)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            link = Path(directory) / 'link'
            link.symlink_to(path)
            with self.assertRaises(RuntimeError):
                remote.write(link, 'other')

    def test_prepare_refuses_previously_started_target(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, 'ROOT', Path(directory)), patch.object(remote, 'configure') as configure:
            (Path(directory) / 'application-started').write_text('started')
            with self.assertRaises(RuntimeError):
                remote.main({'action': 'prepare', 'config': self.config()})
            configure.assert_not_called()

    def test_freeze_stops_ingress_before_writers_and_verifies_exit(self):
        calls = []
        module = Mock()
        module.ch.return_value = b'0'
        module.read_env.return_value = {'CLICKHOUSE_DB': 'rybbit'}
        module.db_name.return_value = 'rybbit'
        def compose(*args, **kwargs):
            calls.append(args)
            return Mock(returncode=0, stdout=(b'a' * 64 if args[:3] == ('ps', '--all', '--quiet') else b''), stderr=b'')
        def run(*args, **kwargs):
            if args[:2] == ('docker', 'inspect'):
                return Mock(returncode=0, stdout=b'false 0 false')
            return Mock(returncode=1, stdout=b'')
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, 'ROOT', Path(directory)), patch.object(remote, 'compose', side_effect=compose), patch.object(remote, 'run', side_effect=run), patch.object(remote, 'counts', return_value={'events': 5}), patch.object(remote, 'backup_module', return_value=module), patch.object(remote.time, 'sleep'):
            (Path(directory) / 'stack.env').write_text('CLICKHOUSE_DB=rybbit')
            result = remote.freeze(self.config())
        self.assertEqual(calls[0], ('stop', '--timeout', '45', 'caddy'))
        self.assertIn(('stop', '--timeout', '45', 'backend', 'client'), calls)
        self.assertFalse(result['queue_depth_verified'])

    def test_tracking_probe_has_browser_headers_and_unique_marker(self):
        request = remote.tracking_request('test.example.com', '123', '/colors-acceptance-unique')
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertIn('Firefox/', headers['user-agent'])
        self.assertIn('gzip', headers['accept-encoding'])
        self.assertEqual(headers['accept-language'], 'en-US,en;q=0.9')
        self.assertEqual(headers['sec-fetch-mode'], 'cors')
        self.assertEqual(headers['accept'], '*/*')
        payload = json.loads(request.data)
        self.assertEqual(payload['pathname'], '/colors-acceptance-unique')
        self.assertEqual(payload['hostname'], 'colors-acceptance.invalid')
        self.assertEqual(payload['site_id'], '123')
        self.assertEqual(payload['_bs'], 0)
        self.assertGreater(payload['screenWidth'], payload['screenHeight'])
