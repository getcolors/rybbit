"""Root-side backup and restore helpers. Never print database command output."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import uuid
from xml.sax.saxutils import escape

ROOT = Path('/opt/rybbit')


def run(args, *, data=None, env=None, output=None, stdin=None):
    result = subprocess.run(args, input=data, stdin=stdin, stdout=output or subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, cwd=ROOT, timeout=3600)
    if result.returncode:
        raise RuntimeError('database or backup command failed')
    return result.stdout or b''


def compose(*args, **kwargs):
    return run(['docker', 'compose', *args], **kwargs)


def read_env(text):
    result = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or '\x00' in value:
            raise ValueError('invalid environment file')
        result[key] = value
    return result


def pg(query, database='postgres', data=None, stdin=None):
    # Container environment holds credentials; no password appears in argv.
    return compose('exec', '-T', 'postgres', 'sh', '-c',
                   'exec psql -U "$POSTGRES_USER" -d "$1" -qAt -v ON_ERROR_STOP=1', 'sh', database,
                   data=(data if data is not None else query.encode()) if stdin is None else None, stdin=stdin)


def ch(query):
    # Keep passwords out of process arguments, including inside the container.
    env = read_env((ROOT / 'stack.env').read_text())
    configuration = ('<config><user>' + escape(env['CLICKHOUSE_USER']) + '</user><password>'
                     + escape(env['CLICKHOUSE_PASSWORD']) + '</password></config>').encode()
    path = '/tmp/rybbit-client-' + uuid.uuid4().hex + '.xml'
    try:
        compose('exec', '-T', '-u', 'root', 'clickhouse', 'sh', '-c',
                'umask 077; cat > "$1"', 'sh', path, data=configuration)
        return compose('exec', '-T', '-u', 'root', 'clickhouse', 'clickhouse-client',
                       '--config-file', path, data=query.encode())
    finally:
        compose('exec', '-T', '-u', 'root', 'clickhouse', 'rm', '-f', path)


def db_name(env, key):
    name = env[key]
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', name):
        raise ValueError('invalid database identifier')
    return name


def rclone_env(config, credentials):
    return {**os.environ, 'RCLONE_CONFIG_R2_TYPE': 's3', 'RCLONE_CONFIG_R2_PROVIDER': 'Cloudflare',
            'RCLONE_CONFIG_R2_REGION': 'auto', 'RCLONE_CONFIG_R2_NO_CHECK_BUCKET': 'true',
            'RCLONE_CONFIG_R2_ENDPOINT': config['rybbit-backup-r2-endpoint'],
            'RCLONE_CONFIG_R2_ACCESS_KEY_ID': credentials['access_key_id'],
            'RCLONE_CONFIG_R2_SECRET_ACCESS_KEY': credentials['secret_access_key']}


def backup(config, credentials, upload=True):
    env = read_env((ROOT / 'stack.env').read_text())
    database = db_name(env, 'CLICKHOUSE_DB')
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
    directory = Path(config['rybbit-backup-dir'])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    archive = directory / ('rybbit-' + stamp + '.tar.gz')
    native = Path(config['clickhouse-data-dir']) / 'backups' / ('clickhouse-' + stamp + '.zip')
    scratch = 'rybbit_restore_check_' + uuid.uuid4().hex
    with open('/run/lock/rybbit-backup.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='rybbit-backup-') as temporary:
            temporary = Path(temporary)
            try:
                with (temporary / 'postgres.sql').open('wb') as output:
                    compose('exec', '-T', 'postgres', 'sh', '-c',
                            'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"', output=output)
                compose('exec', '-T', '-u', 'root', 'clickhouse', 'sh', '-c',
                        'mkdir -p /var/lib/clickhouse/backups && chown clickhouse:clickhouse /var/lib/clickhouse/backups')
                ch(f"BACKUP DATABASE {database} TO File('/var/lib/clickhouse/backups/{native.name}')")
                shutil.move(native, temporary / 'clickhouse.zip')
                pg(f'CREATE DATABASE "{scratch}"')
                with (temporary / 'postgres.sql').open('rb') as incoming:
                    pg('', scratch, stdin=incoming)
                if int(pg("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'", scratch)) < 1:
                    raise RuntimeError('backup scratch restore contains no tables')
                partial = archive.with_suffix('.partial')
                with tarfile.open(partial, 'w:gz') as output:
                    for name in ('postgres.sql', 'clickhouse.zip'):
                        output.add(temporary / name, arcname=name)
                os.chmod(partial, 0o600)
                os.replace(partial, archive)
                if upload:
                    child_env = rclone_env(config, credentials)
                    destination = 'r2:' + config['rybbit-backup-r2-bucket'] + '/' + config['profile']
                    run(['rclone', 'copyto', str(archive), destination + '/' + archive.name], env=child_env)
                    # Verify the uploaded bytes before any retention operation.
                    run(['rclone', 'check', '--download', '--one-way', '--include', archive.name,
                         str(directory), destination], env=child_env)
                    run(['rclone', 'delete', '--min-age', str(config['rybbit-backup-retention-days']) + 'd',
                         '--include', 'rybbit-*.tar.gz', destination], env=child_env)
                    cutoff = time.time() - int(config['rybbit-backup-retention-days']) * 86400
                    for old in directory.glob('rybbit-*.tar.gz'):
                        if old.is_file() and old.stat().st_mtime < cutoff:
                            old.unlink()
            finally:
                try:
                    pg(f'DROP DATABASE IF EXISTS "{scratch}"')
                finally:
                    native.unlink(missing_ok=True)
                    archive.with_suffix('.partial').unlink(missing_ok=True)
    return {'ok': True, 'archive': str(archive), 'sha256': file_digest(archive), 'uploaded': upload}


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def extract_archive(archive, destination):
    """Accept the two legacy regular files only; never extract archive paths."""
    found = set()
    with tarfile.open(archive, 'r:gz') as source:
        for member in source:
            name = member.name.removeprefix('./')
            if name in ('', '.') and member.isdir():
                continue
            if name not in ('postgres.sql', 'clickhouse.zip') or not member.isfile() or name in found:
                raise ValueError('unexpected backup archive member')
            found.add(name)
            with source.extractfile(member) as incoming, (Path(destination) / name).open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
    if found != {'postgres.sql', 'clickhouse.zip'}:
        raise ValueError('backup is missing database files')


def restore(config, archive, expected_sha256):
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha256) or file_digest(archive) != expected_sha256:
        raise ValueError('backup checksum mismatch')
    marker = ROOT / 'restore.json'
    if marker.exists():
        receipt = json.loads(marker.read_text())
        if receipt.get('sha256') == expected_sha256 and receipt.get('complete'):
            return {'ok': True, 'restored': True, 'already_complete': True}
        raise RuntimeError('restore already started; use a fresh target after failed restore')
    env = read_env((ROOT / 'stack.env').read_text())
    pgdb, chdb = db_name(env, 'POSTGRES_DB'), db_name(env, 'CLICKHOUSE_DB')
    if int(pg("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'", pgdb)):
        raise RuntimeError('PostgreSQL destination is not empty')
    if int(ch(f"SELECT count() FROM system.tables WHERE database='{chdb}'")):
        raise RuntimeError('ClickHouse destination is not empty')
    with tempfile.TemporaryDirectory(prefix='rybbit-restore-') as temporary:
        extract_archive(archive, temporary)
        marker.write_text(json.dumps({'sha256': expected_sha256, 'complete': False}))
        os.chmod(marker, 0o600)
        with (Path(temporary) / 'postgres.sql').open('rb') as incoming:
            pg('', pgdb, stdin=incoming)
        native = Path(config['clickhouse-data-dir']) / 'backups' / ('restore-' + uuid.uuid4().hex + '.zip')
        native.parent.mkdir(exist_ok=True)
        shutil.copyfile(Path(temporary) / 'clickhouse.zip', native)
        os.chmod(native, 0o644)
        try:
            ch(f"RESTORE DATABASE {chdb} FROM File('/var/lib/clickhouse/backups/{native.name}')")
        finally:
            native.unlink(missing_ok=True)
        tables = int(pg("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'", pgdb))
        if tables < 1:
            raise RuntimeError('restored PostgreSQL has no tables')
        receipt = {'sha256': expected_sha256, 'complete': True, 'postgres_tables': tables}
        marker.write_text(json.dumps(receipt))
    return {'ok': True, 'restored': True, **receipt}


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise RuntimeError('backup interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        config = json.loads((ROOT / 'backup-config.json').read_text())
        credentials = json.loads(Path('/etc/rybbit-backup.json').read_text())
        result = backup(config, credentials)
    except Exception:
        print(json.dumps({'ok': False, 'stage': 'backup'}))
        raise SystemExit(1)
    print(json.dumps(result))
