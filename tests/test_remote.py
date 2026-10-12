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

    def test_readiness_uses_verified_local_origin_without_proxy_or_redirects(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, 'ROOT', Path(directory)), patch.object(remote, 'run', return_value=Mock(returncode=0, stdout=b'200')) as run, patch.object(remote, 'compose', return_value=Mock(returncode=0)) as compose:
            (Path(directory) / 'stack.env').write_text(remote.stack_settings(self.config()))
            result = remote.main({'action': 'readiness', 'config': self.config()})
        self.assertEqual(result, {'ok': True, 'ready': True, 'tls': True, 'health': True})
        args = run.call_args_list[0].args
        self.assertEqual(args[:2], ('curl', '-q'))
        self.assertEqual(args[args.index('--resolve') + 1], 'test.example.com:443:127.0.0.1')
        self.assertEqual(args[args.index('--noproxy') + 1], '*')
        self.assertEqual(args[-1], 'https://test.example.com/api/health')
        self.assertNotIn('--insecure', args)
        self.assertNotIn('--location', args)
        self.assertEqual(compose.call_count, 2)
        self.assertTrue(all(call.args[:2] == ('exec', '-T') for call in compose.call_args_list))

    def test_readiness_fails_closed_for_tls_failure_redirect_and_unhealthy_origin(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, 'ROOT', Path(directory)), patch.object(remote, 'compose', return_value=Mock(returncode=0)):
            (Path(directory) / 'stack.env').write_text(remote.stack_settings(self.config()))
            for code, status in ((60, b'000'), (0, b'301'), (0, b'503')):
                with self.subTest(code=code, status=status), patch.object(remote, 'run', return_value=Mock(returncode=code, stdout=status)):
                    with self.assertRaisesRegex(RuntimeError, 'TLS or health'):
                        remote.readiness(self.config())

    def test_readiness_rejects_unconfigured_or_invalid_hostname_before_network(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, 'ROOT', Path(directory)), patch.object(remote, 'run') as run:
            (Path(directory) / 'stack.env').write_text(remote.stack_settings(self.config()))
            for host in ('other.example.com', 'test.example.com/path', 'bad:443', '', 'bad..example'):
                with self.subTest(host=host), self.assertRaises((ValueError, RuntimeError)):
                    remote.readiness({**self.config(), 'rybbit-host': host})
            run.assert_not_called()

    def test_readiness_rejects_stale_runtime_hostname(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, 'ROOT', Path(directory)), patch.object(remote, 'compose', return_value=Mock(returncode=1)), patch.object(remote, 'run') as run:
            (Path(directory) / 'stack.env').write_text(remote.stack_settings(self.config()))
            with self.assertRaisesRegex(RuntimeError, 'running application hostname'):
                remote.readiness(self.config())
            run.assert_not_called()

    def test_readiness_rejects_unhealthy_frontend(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, 'ROOT', Path(directory)), patch.object(remote, 'compose', return_value=Mock(returncode=0)), patch.object(remote, 'run', side_effect=[Mock(returncode=0, stdout=b'200'), Mock(returncode=0, stdout=b'502')]):
            (Path(directory) / 'stack.env').write_text(remote.stack_settings(self.config()))
            with self.assertRaisesRegex(RuntimeError, 'frontend health'):
                remote.readiness(self.config())

    def test_reset_target_dispatches_trusted_program_with_fencing_hooks(self):
        source = '''def reset_target(config, request, *, fence, compose):
    fence()
    compose('stop')
    return {'ok': True, 'reset': True, 'reset_id': request['reset_id'], 'quarantined': True}
'''
        with patch.object(remote, 'fence') as fence, patch.object(remote, 'compose') as compose:
            result = remote.main({'action': 'reset-target', 'config': self.config(), 'reset_id': 'synthetic', 'reset_program': source})
        fence.assert_called_once_with()
        compose.assert_called_once_with('stop')
        self.assertEqual(result['reset_id'], 'synthetic')

    def test_fence_blocks_if_backup_service_or_timer_remains_active(self):
        for active in ('rybbit-backup.timer', 'rybbit-backup.service'):
            def run(*args, **kwargs):
                return Mock(returncode=0 if args[:2] == ('systemctl', 'is-active') and args[-1] == active else 1)
            with self.subTest(active=active), patch.object(remote, 'run', side_effect=run), patch.object(remote, 'compose') as compose:
                with self.assertRaisesRegex(RuntimeError, 'backup unit remains active'):
                    remote.fence()
                compose.assert_not_called()

    def test_incomplete_reset_blocks_application_mutations(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, 'ROOT', Path(directory)), patch.object(remote, 'configure') as configure, patch.object(remote, 'compose') as compose, patch.object(remote, 'backup_module') as backup:
            (Path(directory) / 'reset.json').write_text('{"complete":false}')
            for action in ('prepare', 'converge', 'restore', 'backup', 'acceptance'):
                with self.subTest(action=action), self.assertRaisesRegex(RuntimeError, 'reset.*blocks application'):
                    remote.main({'action': action, 'config': self.config()})
            configure.assert_not_called()
            compose.assert_not_called()
            backup.assert_not_called()

    def reset_receipt(self, root, complete=False):
        paths = [Path('/var/lib/rybbit') / name for name in ('postgres', 'clickhouse', 'redis')]
        paths += [root / name for name in ('stack.env', 'application-started', 'restore.json')]
        return {'reset_id': '12345678-1234-1234-1234-123456789abc', 'machine_id': 'a'*32,
                'profile': 'rybbit-hcloud', 'complete': complete, 'containers_removed': complete,
                'items': [{'source': str(path), 'name': path.name, 'present': True,
                           'device': 1, 'inode': 42} for path in paths]}

    def test_pending_or_malformed_reset_receipts_block_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, 'ROOT', Path(directory)), patch.object(remote, 'configure') as configure, patch.object(remote, 'compose') as compose, patch.object(remote, 'fence') as fence, patch.object(remote, 'backup_module') as backup, patch.object(remote, 'acceptance') as acceptance:
            root = Path(directory)
            completed = self.reset_receipt(root, True)
            receipts = [self.reset_receipt(root), {}, [], None, {'complete': True},
                        {**completed, 'complete': 1}, {**completed, 'complete': 'true'},
                        {**completed, 'containers_removed': 1},
                        {**completed, 'containers_removed': False},
                        {**completed, 'items': []}, {**completed, 'reset_id': 'bad'},
                        {**completed, 'profile': 'rybbit-hetzner'}]
            for receipt in receipts:
                (root / 'reset.json').write_text(json.dumps(receipt))
                for action in ('prepare', 'converge', 'restore', 'backup', 'acceptance'):
                    with self.subTest(receipt=receipt, action=action), self.assertRaisesRegex(RuntimeError, 'reset.*blocks application'):
                        remote.main({'action': action})
            (root / 'reset.json').write_text('malformed json')
            with self.assertRaisesRegex(RuntimeError, 'invalid reset receipt'):
                remote.main({'action': 'prepare'})
            for mock in (configure, compose, fence, backup, acceptance):
                mock.assert_not_called()

    def test_completed_reset_allows_application_and_pending_reset_allows_retry(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, 'ROOT', Path(directory)), patch.object(remote, 'acceptance', return_value={'ok': True}) as acceptance:
            root = Path(directory)
            (root / 'reset.json').write_text(json.dumps(self.reset_receipt(root, True)))
            self.assertEqual(remote.main({'action': 'acceptance'}), {'ok': True})
            acceptance.assert_called_once()
            (root / 'reset.json').write_text(json.dumps(self.reset_receipt(root)))
            result = remote.main({'action': 'reset-target', 'reset_program': 'def reset_target(*args, **kwargs): return {"retry": True}'})
            self.assertEqual(result, {'retry': True})

    def test_reset_receipt_symlinks_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, 'ROOT', Path(directory)):
            (Path(directory) / 'reset.json').symlink_to(Path(directory) / 'absent')
            with self.assertRaisesRegex(RuntimeError, 'invalid reset receipt'):
                remote.require_completed_reset()
