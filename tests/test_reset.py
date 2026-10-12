import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid
from unittest.mock import Mock

import pytest
from rybbit import reset


@pytest.fixture
def target(tmp_path, monkeypatch):
    root, data = tmp_path / 'opt', tmp_path / 'data'
    root.mkdir()
    data.mkdir()
    machine = tmp_path / 'machine-id'
    machine.write_text('a' * 32)
    mounts = tmp_path / 'mountinfo'
    mounts.write_text('')
    for key, value in {'ROOT': root, 'DATA_ROOT': data, 'MACHINE_ID': machine, 'MOUNTINFO': mounts}.items():
        monkeypatch.setattr(reset, key, value)
    config = {'profile': 'rybbit-hcloud'}
    for name in ('postgres', 'clickhouse', 'redis'):
        directory = data / name
        directory.mkdir()
        (directory / 'data').write_bytes(b'unchanged synthetic bytes')
        config[name + '-data-dir'] = str(directory)
    for name in ('stack.env', 'application-started', 'restore.json'):
        (root / name).write_text('synthetic')
    (data / 'caddy').mkdir()
    (data / 'caddy' / 'keep').write_text('certificate placeholder')
    request = {'reset_id': str(uuid.uuid4()), 'expected_machine_id': 'a' * 32}
    fence = Mock()
    rows = [{'Service': name, 'State': 'exited'} for name in reset.SERVICES]
    state = {'down': False}
    def result(*args, **kwargs):
        if args == ('down', '--remove-orphans'): state['down'] = True
        return SimpleNamespace(returncode=0, stdout=json.dumps([] if state['down'] else rows).encode())
    compose = Mock(side_effect=result)
    return root, data, config, request, fence, compose


def invoke(target):
    root, data, config, request, fence, compose = target
    return reset.reset_target(config, request, fence=fence, compose=compose)


def test_reset_preserves_data_tls_receipts_and_is_idempotent(target):
    root, data, config, request, fence, compose = target
    result = invoke(target)
    assert result == {'ok': True, 'reset': True, 'reset_id': request['reset_id'], 'quarantined': True}
    quarantine = data / 'reset-quarantine' / request['reset_id']
    for name in ('postgres', 'clickhouse', 'redis'):
        assert not (data / name).exists()
        assert (quarantine / name / 'data').read_bytes() == b'unchanged synthetic bytes'
    assert (data / 'caddy' / 'keep').read_text() == 'certificate placeholder'
    assert (quarantine / 'stack.env').read_text() == 'synthetic'
    assert quarantine.stat().st_mode & 0o777 == 0o700
    assert (root / 'reset.json').stat().st_mode & 0o777 == 0o600
    assert json.loads((root / 'reset.json').read_text())['complete'] is True
    (data / 'postgres').mkdir()  # A retry must never touch newly prepared data.
    assert invoke(target) == result
    assert (data / 'postgres').exists()
    assert fence.call_count == 1


@pytest.mark.parametrize('change', ['profile', 'machine', 'path', 'uuid', 'symlink', 'mount', 'missing'])
def test_identity_and_inventory_rejections_precede_fence(target, change):
    root, data, config, request, fence, compose = target
    if change == 'profile': config['profile'] = 'production'
    if change == 'machine': request['expected_machine_id'] = 'b' * 32
    if change == 'path': config['postgres-data-dir'] = '/production'
    if change == 'uuid': request['reset_id'] = '../../x'
    if change == 'symlink':
        (root / 'stack.env').unlink()
        (root / 'stack.env').symlink_to(data / 'caddy' / 'keep')
    if change == 'mount':
        reset.MOUNTINFO.write_text(f'1 2 0:1 / {data}/postgres/nested rw - tmpfs tmpfs rw\n')
    if change == 'missing': (root / 'stack.env').unlink()
    with pytest.raises((ValueError, RuntimeError)):
        invoke(target)
    fence.assert_not_called()
    assert (data / 'postgres' / 'data').exists()


@pytest.mark.parametrize('state', ['running', 'paused', 'restarting', 'dead'])
def test_still_running_service_never_moves_data(target, state):
    root, data, config, request, fence, compose = target
    compose.side_effect = None
    compose.return_value = SimpleNamespace(returncode=0, stdout=json.dumps([{'Service': name, 'State': state if name == 'postgres' else 'exited'} for name in reset.SERVICES]).encode())
    with pytest.raises(RuntimeError): invoke(target)
    assert (data / 'postgres' / 'data').exists()
    assert json.loads((root / 'reset.json').read_text())['complete'] is False


def test_interrupted_move_reconciles_from_inode_evidence(target, monkeypatch):
    root, data, config, request, fence, compose = target
    rename = os.rename
    calls = 0
    def interrupted(source, destination):
        nonlocal calls
        rename(source, destination)
        calls += 1
        if calls == 2: raise OSError('simulated interruption after rename')
    monkeypatch.setattr(reset.os, 'rename', interrupted)
    with pytest.raises(OSError): invoke(target)
    assert not (data / 'postgres').exists()
    assert not (data / 'clickhouse').exists()
    other = dict(request, reset_id=str(uuid.uuid4()))
    with pytest.raises(RuntimeError):
        reset.reset_target(config, other, fence=fence, compose=compose)
    monkeypatch.setattr(reset.os, 'rename', rename)
    assert invoke(target)['reset']
    assert json.loads((root / 'reset.json').read_text())['complete']


def test_absent_optional_receipt_is_recorded(target):
    root, data, config, request, fence, compose = target
    (root / 'restore.json').unlink()
    assert invoke(target)['reset']
    receipt = json.loads((root / 'reset.json').read_text())
    assert receipt['items'][-1]['present'] is False


def test_replacement_after_interrupted_move_is_not_touched(target, monkeypatch):
    root, data, config, request, fence, compose = target
    rename = os.rename
    def interrupted(source, destination):
        rename(source, destination)
        raise OSError('simulated interruption')
    monkeypatch.setattr(reset.os, 'rename', interrupted)
    with pytest.raises(OSError): invoke(target)
    (data / 'postgres').mkdir()
    monkeypatch.setattr(reset.os, 'rename', rename)
    with pytest.raises(RuntimeError): invoke(target)
    assert (data / 'postgres').exists()


def test_manifest_crash_before_active_receipt_is_resumable(target, monkeypatch):
    root, data, config, request, fence, compose = target
    write = reset._write
    def interrupted(path, value):
        if path == root / 'reset.json': raise OSError('simulated receipt interruption')
        write(path, value)
    monkeypatch.setattr(reset, '_write', interrupted)
    with pytest.raises(OSError): invoke(target)
    monkeypatch.setattr(reset, '_write', write)
    assert invoke(target)['reset']


def test_retry_after_settings_move_avoids_original_compose_config(target, monkeypatch):
    root, data, config, request, fence, compose = target
    rename = os.rename
    def interrupted(source, destination):
        rename(source, destination)
        if source.name == 'stack.env': raise OSError('interrupted after settings move')
    monkeypatch.setattr(reset.os, 'rename', interrupted)
    with pytest.raises(OSError): invoke(target)
    assert not (root / 'stack.env').exists()
    calls_before = compose.call_count
    original = compose.side_effect
    def no_missing_env(*args, **kwargs):
        assert args[:2] == ('-f', '-')
        assert kwargs.get('data')
        return original(*args, **kwargs)
    compose.side_effect = no_missing_env
    fence.side_effect = AssertionError('must not use original config after settings move')
    monkeypatch.setattr(reset.os, 'rename', rename)
    assert invoke(target)['reset']
    assert compose.call_count == calls_before + 1


def test_prepared_database_only_target_can_reset(target):
    root, data, config, request, fence, compose = target
    original = compose.side_effect
    def databases(*args, **kwargs):
        result = original(*args, **kwargs)
        rows = json.loads(result.stdout)
        result.stdout = json.dumps([row for row in rows if row['Service'] in ('postgres', 'clickhouse', 'redis')]).encode()
        return result
    compose.side_effect = databases
    assert invoke(target)['reset']
