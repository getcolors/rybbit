"""Quarantine an explicitly identified staging target's databases; never delete them."""
import json
import os
from pathlib import Path
import re
import uuid

ROOT = Path('/opt/rybbit')
DATA_ROOT = Path('/var/lib/rybbit')
MACHINE_ID = Path('/etc/machine-id')
MOUNTINFO = Path('/proc/self/mountinfo')
SERVICES = ('caddy', 'backend', 'client', 'postgres', 'clickhouse', 'redis')


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, value):
    temporary = path.with_name(path.name + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(value, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    _sync(path.parent)


def _safe_path(path):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise RuntimeError('reset refuses symlink paths')
    if path.exists() and not (path.is_dir() or path.is_file()):
        raise RuntimeError('reset refuses special files')


def _mounts():
    # Linux escapes whitespace and backslashes in mountinfo path fields.
    return [Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4]))
            for line in MOUNTINFO.read_text().splitlines()]


def _inventory(paths, device):
    mounted = _mounts()
    items = []
    for path in paths:
        _safe_path(path)
        if any(mount == path or path in mount.parents for mount in mounted):
            raise RuntimeError('reset refuses mounted data paths')
        exists = path.exists()
        if exists:
            info = path.stat()
            if info.st_dev != device or (path.is_file() and info.st_nlink != 1):
                raise RuntimeError('reset requires same-filesystem independent data')
        items.append({'source': str(path), 'name': path.name, 'present': exists,
                      'device': info.st_dev if exists else None,
                      'inode': info.st_ino if exists else None})
    return items


def _stopped(compose):
    compose('stop', '--timeout', '45', *SERVICES)
    result = compose('ps', '--all', '--format', 'json')
    if result.returncode:
        raise RuntimeError('reset cannot inspect stopped services')
    text = result.stdout.decode().strip()
    rows = json.loads(text) if text.startswith('[') else [json.loads(line) for line in text.splitlines() if line]
    seen = set()
    for row in rows:
        service = row.get('Service')
        if service not in SERVICES or service in seen or row.get('State') not in ('exited', 'created'):
            raise RuntimeError('reset requires all application containers stopped')
        seen.add(service)
    if seen and not {'postgres', 'clickhouse', 'redis'} <= seen:
        raise RuntimeError('reset requires exact application container inventory')
    # Remove restart policies and old bind mounts before renames. A reboot must
    # not initialize fresh directories underneath an interrupted quarantine.
    compose('down', '--remove-orphans')
    result = compose('ps', '--all', '--format', 'json')
    if result.returncode or result.stdout.decode().strip() not in ('', '[]'):
        raise RuntimeError('reset requires all application containers removed')


def _verify_removed(compose):
    # Explicit project identity and synthetic configuration avoid resolving the
    # stack.env which may already be quarantined during interrupted retries.
    configuration = json.dumps({'services': {name: {'image': 'scratch'} for name in SERVICES}}).encode()
    result = compose('-f', '-', '--project-name', ROOT.name, 'ps', '--all', '--format', 'json', data=configuration)
    if result.returncode or result.stdout.decode().strip() not in ('', '[]'):
        raise RuntimeError('reset found containers after removal')


def reset_target(config, request, *, fence, compose):
    """Called under the remote operation lock, after controller ownership validation."""
    reset_id = request.get('reset_id', '')
    if not isinstance(reset_id, str) or str(uuid.UUID(reset_id)) != reset_id:
        raise ValueError('reset requires a canonical UUID')
    expected = request.get('expected_machine_id', '')
    if (config.get('profile') != 'rybbit-hcloud' or not isinstance(expected, str)
            or not re.fullmatch('[a-f0-9]{32}', expected)
            or MACHINE_ID.read_text().strip() != expected):
        raise RuntimeError('reset target identity mismatch')
    database_paths = [DATA_ROOT / name for name in ('postgres', 'clickhouse', 'redis')]
    if any(config.get(name + '-data-dir') != str(path)
           for name, path in zip(('postgres', 'clickhouse', 'redis'), database_paths)):
        raise RuntimeError('reset requires canonical database paths')
    paths = [*database_paths, *(ROOT / name for name in ('stack.env', 'application-started', 'restore.json'))]
    base = DATA_ROOT / 'reset-quarantine'
    quarantine = base / reset_id
    receipt_path = ROOT / 'reset.json'
    for path in (ROOT, DATA_ROOT, base, quarantine, receipt_path):
        _safe_path(path)
    if any(mount == base or base in mount.parents for mount in _mounts()):
        raise RuntimeError('reset refuses mounted quarantine paths')
    if ROOT.stat().st_dev != DATA_ROOT.stat().st_dev:
        raise RuntimeError('reset requires one filesystem')
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
    if receipt:
        if receipt.get('reset_id') == reset_id:
            if receipt.get('machine_id') != expected or receipt.get('profile') != config['profile']:
                raise RuntimeError('reset receipt identity mismatch')
            if receipt.get('complete') is True:
                return {'ok': True, 'reset': True, 'reset_id': reset_id, 'quarantined': True}
        elif receipt.get('complete') is not True:
            raise RuntimeError('another reset is incomplete; resume its reset ID')
        else:
            receipt = None
    if receipt is None:
        # Validate every candidate before stopping anything or moving any data.
        items = _inventory(paths, DATA_ROOT.stat().st_dev)
        if not all(item['present'] for item in items[:4]):
            raise RuntimeError('reset requires initialized databases and retained settings')
        if quarantine.exists() and any(quarantine.iterdir()):
            prior = quarantine / 'manifest.json'
            _safe_path(prior)
            recovered = json.loads(prior.read_text()) if prior.is_file() else {}
            if recovered != {'reset_id': reset_id, 'machine_id': expected, 'profile': config['profile'],
                             'complete': False, 'containers_removed': False, 'items': items}:
                raise RuntimeError('reset ID already has quarantine evidence')
        base.mkdir(mode=0o700, exist_ok=True)
        os.chmod(base, 0o700)
        _sync(DATA_ROOT)
        quarantine.mkdir(mode=0o700, exist_ok=True)
        os.chmod(quarantine, 0o700)
        _sync(base)
        receipt = {'reset_id': reset_id, 'machine_id': expected, 'profile': config['profile'],
                   'complete': False, 'containers_removed': False, 'items': items}
        # Preserve the manifest in quarantine independently of the active receipt.
        _write(quarantine / 'manifest.json', receipt)
        _write(receipt_path, receipt)
    if (set(receipt) != {'reset_id', 'machine_id', 'profile', 'complete', 'containers_removed', 'items'}
            or not isinstance(receipt['items'], list) or len(receipt['items']) != len(paths)):
        raise RuntimeError('invalid reset receipt')
    for item, path in zip(receipt['items'], paths):
        if (set(item) != {'source', 'name', 'present', 'device', 'inode'}
                or item['source'] != str(path) or item['name'] != path.name
                or type(item['present']) is not bool):
            raise RuntimeError('invalid reset manifest')
    if receipt.get('containers_removed') is not True:
        fence()
        _stopped(compose)
        receipt['containers_removed'] = True
        _write(quarantine / 'manifest.json', receipt)
        _write(receipt_path, receipt)
    _verify_removed(compose)
    # Renames preserve ownership, permissions and all bytes. Receipt plus inode
    # evidence reconciles a crash after rename without replaying on new data.
    for item, path in zip(receipt['items'], paths):
        destination = quarantine / item['name']
        _safe_path(path)
        _safe_path(destination)
        if not item['present']:
            if path.exists() or destination.exists():
                raise RuntimeError('unexpected data appeared during reset')
            continue
        if path.exists() == destination.exists():
            raise RuntimeError('ambiguous reset data; preserve evidence')
        current = path if path.exists() else destination
        info = current.stat()
        if (info.st_dev, info.st_ino) != (item['device'], item['inode']):
            raise RuntimeError('reset data identity changed')
        if path.exists():
            # Recheck mounts immediately before a move, including during retry.
            if any(mount == path or path in mount.parents for mount in _mounts()):
                raise RuntimeError('reset refuses mounted data paths')
            os.rename(path, destination)
            _sync(path.parent)
            _sync(quarantine)
    receipt['complete'] = True
    _write(quarantine / 'manifest.json', receipt)
    _write(receipt_path, receipt)
    return {'ok': True, 'reset': True, 'reset_id': reset_id, 'quarantined': True}
