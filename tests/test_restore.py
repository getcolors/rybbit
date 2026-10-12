import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from rybbit import backup


class RestoreTests(unittest.TestCase):
    def archive(self, path, names=('postgres.sql', 'clickhouse.zip')):
        with tarfile.open(path, 'w:gz') as archive:
            for name in names:
                info = tarfile.TarInfo(name)
                info.size = 4
                archive.addfile(info, io.BytesIO(b'test'))

    def test_legacy_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.archive(root / 'backup.tar.gz', ('./postgres.sql', './clickhouse.zip'))
            output = root / 'out'
            output.mkdir()
            backup.extract_archive(root / 'backup.tar.gz', output)
            self.assertEqual((output / 'postgres.sql').read_bytes(), b'test')

    def test_traversal_and_unexpected_members_rejected(self):
        for name in ('../postgres.sql', '/postgres.sql', 'stack.env', 'postgres.sql/child'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.archive(root / 'backup.tar.gz', (name,))
                with self.assertRaises(ValueError):
                    backup.extract_archive(root / 'backup.tar.gz', root)

    def test_checksum_fails_before_database_calls(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(backup, 'pg') as pg:
            path = Path(directory) / 'backup'
            path.write_bytes(b'bad')
            with self.assertRaises(ValueError):
                backup.restore({}, path, '0' * 64)
            pg.assert_not_called()

    def test_completed_restore_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(backup, 'ROOT', Path(directory)), patch.object(backup, 'pg') as pg:
            path = Path(directory) / 'backup'
            path.write_bytes(b'archive')
            checksum = backup.file_digest(path)
            (Path(directory) / 'restore.json').write_text(json.dumps({'sha256': checksum, 'complete': True}))
            result = backup.restore({}, path, checksum)
            self.assertTrue(result['already_complete'])
            pg.assert_not_called()

    def test_partial_restore_refuses_retry(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(backup, 'ROOT', Path(directory)), patch.object(backup, 'pg') as pg:
            path = Path(directory) / 'backup'
            path.write_bytes(b'archive')
            checksum = backup.file_digest(path)
            (Path(directory) / 'restore.json').write_text(json.dumps({'sha256': checksum, 'complete': False}))
            with self.assertRaises(RuntimeError):
                backup.restore({}, path, checksum)
            pg.assert_not_called()
