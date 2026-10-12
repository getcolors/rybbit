import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from rybbit import backup


class BackupTests(unittest.TestCase):
    def test_commands_hide_database_output_on_error(self):
        with patch.object(backup.subprocess, 'run') as run:
            run.return_value.returncode = 1
            run.return_value.stderr = b'password secret'
            with self.assertRaisesRegex(RuntimeError, '^database or backup command failed$'):
                backup.run(['false'])

    def test_database_identifier_guard(self):
        with self.assertRaises(ValueError):
            backup.db_name({'DB': 'db; DROP DATABASE production'}, 'DB')

    def test_credentials_only_in_child_environment(self):
        env = backup.rclone_env({'rybbit-backup-r2-endpoint': 'https://example.com'},
                                {'access_key_id': 'key', 'secret_access_key': 'secret'})
        self.assertEqual(env['RCLONE_CONFIG_R2_SECRET_ACCESS_KEY'], 'secret')
        self.assertNotIn('RCLONE_CONFIG_R2_SECRET_ACCESS_KEY', backup.os.environ)

    def test_clickhouse_failure_prevents_upload_and_cleans_scratch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'stack.env').write_text('CLICKHOUSE_DB=rybbit\n')
            config = {'rybbit-backup-dir': str(root / 'backups'), 'clickhouse-data-dir': str(root / 'ch')}
            original_open = open
            with patch.object(backup, 'ROOT', root), patch.object(backup, 'compose'), patch.object(backup, 'ch', side_effect=RuntimeError('failed')), patch.object(backup, 'pg') as pg, patch.object(backup, 'run') as run, patch('builtins.open', wraps=open) as opened:
                # Use a temporary lock so the unit test does not require root.
                opened.side_effect = lambda name, *a, **kw: original_open(root / 'lock' if str(name) == '/run/lock/rybbit-backup.lock' else name, *a, **kw)
                with self.assertRaises(RuntimeError):
                    backup.backup(config, {})
                run.assert_not_called()
                self.assertIn('DROP DATABASE IF EXISTS', pg.call_args.args[0])
            self.assertEqual(list((root / 'backups').iterdir()), [])
