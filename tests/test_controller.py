import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from pocketdeploy.common import DeployError
from pocketdeploy.state import State
from rybbit import controller


@pytest.fixture
def current(tmp_path):
    with State(tmp_path / '.state.sqlite', 'synthetic', {}, create=True) as state:
        host, cloud = Mock(), Mock()
        cloud.resource_kinds = frozenset({'hcloud-server', 'hcloud-firewall', 'hcloud-primary-ip'})
        pub = tmp_path / 'operator.pub'
        pub.write_text('ssh-ed25519 synthetic')
        host.pub = pub
        host.cloud_init.return_value = '#cloud-config\n{}'
        cloud.converge.return_value = cloud.connection.return_value = {'instance_id': '123', 'ip': '192.0.2.1', 'user': 'ubuntu'}
        yield SimpleNamespace(state=state, host=host, cloud=cloud, root=tmp_path)


def setup(monkeypatch):
    service = Mock()
    monkeypatch.setattr(controller, 'dns', Mock(return_value=service))
    monkeypatch.setattr(controller, 'initialize', Mock())
    monkeypatch.setattr(controller, 'aliases', Mock())
    monkeypatch.setattr(controller, 'remote', Mock(return_value={'ok': True, 'prepared': True, 'fenced': True}))
    monkeypatch.setenv('COLORS_PAR_RYBBIT_BACKUP_R2_ACCESS_KEY_ID', 'synthetic-access')
    monkeypatch.setenv('COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY', 'synthetic-secret')
    return service


def test_credentials_fail_before_provider_or_keys(current, monkeypatch):
    setup(monkeypatch)
    monkeypatch.delenv('COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY')
    with pytest.raises(DeployError):
        controller.converge({}, {}, current, 'operation')
    controller.dns.assert_not_called()
    controller.initialize.assert_not_called()
    current.cloud.plan.assert_not_called()


def test_dns_conflict_fails_before_compute_mutation(current, monkeypatch):
    service = setup(monkeypatch)
    service.preflight.side_effect = DeployError('collision')
    with pytest.raises(DeployError):
        controller.converge({}, {}, current, 'operation')
    controller.initialize.assert_not_called()
    current.cloud.converge.assert_not_called()


def test_prepare_fences_backups_does_not_publish_dns_or_journal_secrets(current, monkeypatch):
    service = setup(monkeypatch)
    operation = current.state.begin_operation('prepare', 'hash')
    deployment = {'_desired_hash': 'hash'}
    config = {'profile': 'synthetic', 'rybbit-host': 'test.example.com', 'rybbit-disable-signup': True}
    retained = controller.remote_program.stack_settings(config)
    controller.converge(config, deployment, current, operation, prepare=True, stack_env=retained)
    service.converge.assert_not_called()
    assert controller.remote.call_args.kwargs['enable_backups'] is False
    assert controller.remote.call_args.kwargs['stack_env'] == retained
    assert '_cloud_init' not in deployment
    assert 'BETTER_AUTH_SECRET' not in '\n'.join(current.state.db.iterdump())


def test_cloud_failure_clears_ephemeral_bootstrap(current, monkeypatch):
    setup(monkeypatch)
    current.cloud.converge.side_effect = DeployError('failed')
    deployment = {'_desired_hash': 'hash'}
    with pytest.raises(DeployError):
        controller.converge({}, deployment, current, 'operation')
    assert '_cloud_init' not in deployment
    controller.remote.assert_not_called()


def test_private_inputs_reject_public_symlink_and_hardlink(tmp_path):
    file = tmp_path / 'secret'
    file.write_text('synthetic')
    file.chmod(0o644)
    with pytest.raises(DeployError):
        controller.private_text(file)
    file.chmod(0o600)
    assert controller.private_text(file) == 'synthetic'
    link = tmp_path / 'link'
    link.symlink_to(file)
    with pytest.raises(DeployError):
        controller.private_text(link)
    link.unlink()
    link.hardlink_to(file)
    with pytest.raises(DeployError):
        controller.private_text(file)


def test_upload_checks_permissions_and_hash_before_ssh(tmp_path):
    host = Mock()
    archive = tmp_path / 'snapshot.tar.gz'
    archive.write_bytes(b'synthetic archive')
    archive.chmod(0o644)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(DeployError):
        controller.upload(host, {}, archive, checksum)
    archive.chmod(0o600)
    with pytest.raises(DeployError):
        controller.upload(host, {}, archive, '0' * 64)
    host.prepare_transport.assert_not_called()


def test_failed_restore_keeps_pending_evidence(current, monkeypatch):
    monkeypatch.setattr(controller, 'upload', Mock(return_value='/var/backups/rybbit/import/synthetic.tar.gz'))
    monkeypatch.setattr(controller, 'remote', Mock(side_effect=DeployError('restore failed')))
    operation = current.state.begin_operation('restore', 'hash')
    with pytest.raises(DeployError):
        controller.restore({}, current, operation, 'archive', '0' * 64)
    row = current.state.db.execute("SELECT status,payload FROM steps WHERE step='rybbit-restore'").fetchone()
    assert row['status'] == 'pending'
    assert json.loads(row['payload']) == {'instance_id': '123', 'sha256': '0' * 64}


def test_remote_error_suppresses_untrusted_stage_and_content(monkeypatch):
    host = Mock()
    host.run_python.return_value = {'ok': False, 'stage': 'synthetic-secret', 'error': 'synthetic-secret'}
    with pytest.raises(DeployError) as error:
        controller.remote(host, {}, {'profile': 'synthetic'}, 'status')
    assert error.value.stage == 'status'
    assert 'synthetic-secret' not in str(error.value)


def test_remote_status_does_not_attach_backup_credentials(monkeypatch):
    host = Mock()
    host.run_python.return_value = {'ok': True, 'services': [], 'backup_timer': False}
    credentials = Mock(side_effect=AssertionError('not needed'))
    monkeypatch.setattr(controller, 'backup_credentials', credentials)
    controller.remote(host, {}, {'profile': 'synthetic'}, 'status')
    payload = host.run_python.call_args.args[2]
    assert 'backup_credentials' not in payload
    assert payload['config']['compute-name'] == 'synthetic-server'


def test_delete_protection_before_any_remote_work(current, monkeypatch):
    service = setup(monkeypatch)
    with pytest.raises(DeployError):
        controller.delete({}, {'compute-prevent-destroy': True}, current, 'operation')
    service.plan_delete.assert_not_called()
    controller.remote.assert_not_called()


def test_remote_success_cannot_leak_extra_provider_or_secret_fields():
    host = Mock()
    host.run_python.return_value = {'ok': True, 'services': [], 'backup_timer': False,
                                  'raw_environment': 'synthetic-secret'}
    try:
        result = controller.remote(host, {}, {'profile': 'synthetic'}, 'status')
    except DeployError as error:
        assert 'synthetic-secret' not in str(error)
    else:
        assert 'synthetic-secret' not in json.dumps(result)
        assert 'raw_environment' not in result


def test_delete_resumes_absent_compute_without_ssh(current, monkeypatch):
    service = setup(monkeypatch)
    current.state.put_resource('compute', 'hcloud-server', '123', {}, owned=True)
    current.cloud.delete_pending_key = 'hcloud-delete-compute'
    current.state.set_meta('hcloud-delete-compute', {'id': '123'})
    current.state.set_meta('rybbit-delete-fence', {'instance_id': '123', 'fenced': True})
    current.cloud.plan_delete.return_value = [{'resource': 'compute', 'action': 'absent', 'id': '123', 'state': None}]
    current.cloud.connection.side_effect = AssertionError('absent server cannot be contacted')
    cleanup = Mock(return_value={'deleted': True})
    monkeypatch.setattr(controller, 'delete_compute', cleanup)
    assert controller.delete({'rybbit-host': 'test.example.com'}, {'compute-prevent-destroy': False}, current, 'operation') == {'deleted': True}
    current.cloud.connection.assert_not_called()
    controller.remote.assert_not_called()
    service.delete.assert_called_once()


@pytest.mark.parametrize('evidence,pending', [(None, {'id': '123'}), ({'instance_id': 'wrong', 'fenced': True}, {'id': '123'}), ({'instance_id': '123', 'fenced': True}, None)])
def test_delete_absent_requires_exact_receipt_and_fence(current, monkeypatch, evidence, pending):
    service = setup(monkeypatch)
    current.state.put_resource('compute', 'hcloud-server', '123', {}, owned=True)
    current.cloud.delete_pending_key = 'hcloud-delete-compute'
    current.state.set_meta('hcloud-delete-compute', pending)
    current.state.set_meta('rybbit-delete-fence', evidence)
    current.cloud.plan_delete.return_value = [{'resource': 'compute', 'action': 'absent', 'id': '123', 'state': None}]
    with pytest.raises(DeployError):
        controller.delete({'rybbit-host': 'test.example.com'}, {'compute-prevent-destroy': False}, current, 'operation')
    service.delete.assert_not_called()


def test_invalid_retained_secrets_fail_before_provision(current, monkeypatch):
    setup(monkeypatch)
    with pytest.raises(DeployError, match='Retained stack'):
        controller.converge({}, {}, current, 'operation', prepare=True, stack_env='INCOMPLETE=synthetic')
    controller.dns.assert_not_called()
    controller.initialize.assert_not_called()
    current.cloud.converge.assert_not_called()


@pytest.mark.parametrize('action,result', [
    ('counts', {'ok': True, 'counts': {'user': 'synthetic-secret', 'organization': 1, 'sites': 1, 'events': 2}}),
    ('status', {'ok': True, 'services': [{'Service': 'postgres', 'State': 'running', 'Health': 'synthetic-secret'}], 'backup_timer': True}),
    ('acceptance', {'ok': True, 'health': True, 'ingestion': 'verified', 'marker': 'synthetic-secret', 'matched_events': 1}),
    ('restore', {'ok': True, 'restored': True, 'complete': True, 'sha256': 'a'*64, 'postgres_tables': 'synthetic-secret'}),
    ('backup', {'ok': True, 'archive': '/tmp/synthetic-secret', 'sha256': 'a'*64, 'uploaded': True}),
])
def test_remote_nested_and_freeform_evidence_rejected(action, result):
    host = Mock()
    host.run_python.return_value = result
    with pytest.raises(DeployError) as error:
        controller.remote(host, {}, {'profile': 'synthetic', 'rybbit-backup-dir': '/var/backups/rybbit'}, action)
    assert 'synthetic-secret' not in str(error.value)


@pytest.mark.parametrize('action', ['counts', 'freeze'])
def test_all_migration_counts_and_explicit_freeze_receipt(action):
    values = {key: 1 for key in ('user', 'organization', 'sites', 'events', 'replay_sessions', 'replay_events', 'replay_bytes', 'replay_rows')}
    result = {'ok': True, 'counts': values}
    if action == 'freeze':
        result.update(fenced=True, quiet_seconds=60, queue_depth_verified=False)
    host = Mock()
    host.run_python.return_value = result
    assert controller.remote(host, {}, {'profile': 'synthetic'}, action) == result
    values['replay_bytes'] = 'private'
    with pytest.raises(DeployError):
        controller.remote(host, {}, {'profile': 'synthetic'}, action)


def test_freeze_cannot_claim_unmeasured_queue_depth():
    values = {key: 1 for key in ('user', 'organization', 'sites', 'events', 'replay_sessions', 'replay_events', 'replay_bytes', 'replay_rows')}
    with pytest.raises(DeployError):
        controller.safe_result({'ok': True, 'counts': values, 'fenced': True, 'quiet_seconds': 60, 'queue_depth_verified': True}, 'freeze', {})


def test_alias_preflight_fails_before_cloud_mutation(current, monkeypatch):
    service = setup(monkeypatch)
    controller.aliases.side_effect = DeployError('SSH alias configuration')
    with pytest.raises(DeployError):
        controller.converge({'profile': 'synthetic'}, {}, current, 'operation')
    assert controller.aliases.call_args.kwargs['check_only'] is True
    service.preflight.assert_not_called()
    controller.initialize.assert_not_called()
    current.cloud.converge.assert_not_called()


@pytest.mark.parametrize('failure', [ValueError('synthetic-secret'), OSError('synthetic-secret')])
def test_alias_errors_have_safe_actionable_diagnostic(monkeypatch, failure):
    monkeypatch.setattr(controller.ssh_config, 'update', Mock(side_effect=failure))
    host = Mock(key=Path('/tmp/key'), known=Path('/tmp/known'))
    with pytest.raises(DeployError) as error:
        controller.aliases({'profile': 'synthetic'}, host, {'ip': '192.0.2.1', 'user': 'ubuntu'}, check_only=True)
    assert error.value.code == 'ssh_alias_configuration'
    assert 'Include config.d/*' in str(error.value)
    assert 'synthetic-secret' not in str(error.value)
    assert error.value.stage == 'ssh-alias-preflight'


def test_alias_readonly_preflight_does_not_create_files(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    host = Mock(key=tmp_path / 'key', known=tmp_path / 'known')
    with pytest.raises(DeployError, match='Include config.d'):
        controller.aliases({'profile': 'synthetic'}, host, {'ip': '192.0.2.1', 'user': 'ubuntu'}, check_only=True)
    assert not (home / '.ssh').exists()
    (home / '.ssh').mkdir()
    main = home / '.ssh/config'
    main.write_text('Include config.d/*\n')
    assert controller.aliases({'profile': 'synthetic'}, host, {'ip': '192.0.2.1', 'user': 'ubuntu'}, check_only=True) is False
    assert not (home / '.ssh/config.d').exists()
    assert main.read_text() == 'Include config.d/*\n'


@pytest.mark.parametrize('name,kind', [
    ('dns:A:old.example.com', 'cloudflare-dns'),
    ('dns:A:test.example.com', 'resend-domain'),
    ('other-service', 'unknown-service')])
def test_delete_rejects_unmanaged_resource_before_any_mutation(current, monkeypatch, name, kind):
    service = setup(monkeypatch)
    current.state.put_resource('compute', 'hcloud-server', '123', {}, owned=True)
    current.state.put_resource(name, kind, '456', {}, owned=True)
    cleanup = Mock()
    monkeypatch.setattr(controller, 'delete_compute', cleanup)
    with pytest.raises(DeployError, match='explicit retirement or retention'):
        controller.delete({'rybbit-host': 'test.example.com'}, {'compute-prevent-destroy': False}, current, 'operation')
    controller.aliases.assert_not_called()
    controller.dns.assert_not_called()
    controller.remote.assert_not_called()
    service.delete.assert_not_called()
    current.cloud.delete.assert_not_called()
    cleanup.assert_not_called()


def test_delete_current_dns_passes_resource_preflight(current, monkeypatch):
    service = setup(monkeypatch)
    current.state.put_resource('dns:A:test.example.com', 'cloudflare-dns', '456', {}, owned=True)
    current.cloud.plan_delete.return_value = []
    cleanup = Mock(return_value={'deleted': True})
    monkeypatch.setattr(controller, 'delete_compute', cleanup)
    assert controller.delete({'rybbit-host': 'test.example.com'}, {'compute-prevent-destroy': False}, current, 'operation')['deleted']
    service.delete.assert_called_once_with('operation')
    cleanup.assert_called_once()


def test_application_converge_never_mutates_dns_compute_and_disables_backups(current, monkeypatch):
    service = setup(monkeypatch)
    operation = current.state.begin_operation('application-converge', 'hash')
    controller.application_converge({'rybbit-host': 'prod.example.com'}, current, operation)
    controller.dns.assert_not_called()
    current.cloud.converge.assert_not_called()
    assert controller.remote.call_args.kwargs == {'enable_backups': False}


def adoption(current):
    host = 'prod.example.com'
    evidence = {'host': host, 'zone': 'zone', 'record_id': 'record', 'ipv4': '192.0.2.2',
                'proxied': False, 'ttl': 300, 'settings_sha256': 'a'*64}
    current.state.put_resource('dns:A:' + host, 'cloudflare-dns', 'record', {}, owned=True)
    current.state.set_meta('dns-adoption:dns:A:' + host,
                           {'completed': True, 'evidence': evidence, 'body': {'proxied': False, 'ttl': 300}})
    return {'rybbit-host': host}, evidence


def test_dns_uses_retained_policy_on_every_convergence(current):
    config, _ = adoption(current)
    service = controller.dns(config, current.state)
    assert service.proxied is False and service.ttl == 300


def test_dns_switch_requires_adoption_before_readiness(current, monkeypatch):
    setup(monkeypatch)
    with pytest.raises(DeployError, match='adoption'):
        controller.dns_switch({'rybbit-host': 'prod.example.com'}, current, 'op')
    controller.remote.assert_not_called()


def test_dns_switch_checks_fresh_readiness_before_every_retry(current, monkeypatch):
    config, _ = adoption(current)
    service = setup(monkeypatch)
    controller.remote.side_effect = DeployError('unready')
    with pytest.raises(DeployError, match='unready'):
        controller.dns_switch(config, current, 'op')
    service.converge.assert_not_called()
    controller.remote.side_effect = None
    service.converge.side_effect = [DeployError('lost response'), {'actions': []}]
    with pytest.raises(DeployError, match='lost response'):
        controller.dns_switch(config, current, 'op')
    assert controller.dns_switch(config, current, 'retry') == {'actions': []}
    assert controller.remote.call_count == 3
    assert controller.remote.call_args.args[-1] == 'readiness'


def test_ordinary_converge_cannot_bypass_explicit_switch(current, monkeypatch):
    config, _ = adoption(current)
    service = setup(monkeypatch)
    service.evidence.return_value = {'ipv4': '192.0.2.2'}
    with pytest.raises(DeployError, match='dns-switch'):
        controller.converge(config, {}, current, 'op')
    current.cloud.converge.assert_not_called()
    controller.remote.assert_not_called()


def test_adoption_passes_original_exact_evidence_without_routing(current, monkeypatch, tmp_path):
    _, evidence = adoption(current)
    service = setup(monkeypatch)
    path = tmp_path / 'evidence.json'
    path.write_text(json.dumps(evidence))
    path.chmod(0o600)
    config = {'rybbit-host': evidence['host']}
    with pytest.raises(DeployError, match='Disable'):
        controller.dns_adopt(config, current, 'op', path, False)
    service.adopt.assert_not_called()
    controller.dns_adopt(config, current, 'op', path, True)
    assert service.adopt.call_args.args[-1] == {**evidence, 'previous_dns_manager_disabled': True}
    service.converge.assert_not_called()


def test_retire_exact_identity_and_interrupted_final_receipt(current, monkeypatch):
    config = {'rybbit-host': 'prod.example.com'}
    service = Mock()
    monkeypatch.setattr(controller, 'CloudflareDNS', Mock(return_value=service))
    current.state.put_resource('dns:A:stage.example.com', 'cloudflare-dns', 'old-record', {}, owned=True)
    with pytest.raises(DeployError):
        controller.dns_retire(config, current, 'op', 'stage.example.com', 'wrong-record')
    service.delete.assert_not_called()
    controller.dns_retire(config, current, 'op', 'stage.example.com', 'old-record')
    # Simulate lost final response after library removed its resource.
    current.state.db.execute("DELETE FROM resources WHERE name='dns:A:stage.example.com'")
    current.state.db.commit()
    current.state.set_meta('rybbit-dns-retired:stage.example.com', {'record_id': 'old-record', 'retired': False})
    controller.dns_retire(config, current, 'retry', 'stage.example.com', 'old-record')
    assert controller.dns_retire(config, current, 'retry2', 'stage.example.com', 'old-record')['retired']
    assert service.delete.call_count == 2


def test_reset_identity_fails_before_remote_and_records_exact_receipt(current, monkeypatch):
    setup(monkeypatch)
    reset_id = '12345678-1234-1234-1234-123456789abc'
    machine_id = 'a' * 32
    config = {'profile': 'rybbit-hcloud', 'rybbit-host': 'rybbit-hcloud.getcolors.ai'}
    current.state.put_resource('compute', 'hcloud-server', '123', {}, owned=True)
    args = (config, current, 'op', reset_id, machine_id, '123')
    with pytest.raises(DeployError, match='deployment identity'):
        controller.reset_target(*args, 'wrong')
    controller.remote.assert_not_called()
    with pytest.raises(DeployError, match='rybbit-hcloud'):
        controller.reset_target({'profile': 'rybbit-hetzner'}, *args[1:], current.state.deployment_id)
    controller.remote.assert_not_called()
    operation = current.state.begin_operation('reset-target', 'hash')
    controller.remote.return_value = {'ok': True, 'reset': True, 'reset_id': reset_id, 'quarantined': True}
    result = controller.reset_target(config, current, operation, reset_id, machine_id, '123', current.state.deployment_id)
    assert result['reset_id'] == reset_id
    assert controller.remote.call_args.kwargs == {'reset_id': reset_id, 'expected_machine_id': machine_id}


@pytest.mark.parametrize('adopted,hostname,extra_dns', [
    (True, 'rybbit-hcloud.getcolors.ai', False),
    (False, 'rybbit.getcolors.ai', False),
    (False, 'rybbit-hcloud.getcolors.ai', True)])
def test_reset_refuses_production_transition(current, monkeypatch, adopted, hostname, extra_dns):
    setup(monkeypatch)
    if adopted:
        current.state.set_meta('dns-adoption:dns:A:rybbit.getcolors.ai', {'completed': False})
    if extra_dns:
        current.state.put_resource('dns:A:rybbit.getcolors.ai', 'cloudflare-dns', 'prod', {}, owned=True)
    with pytest.raises(DeployError):
        controller.reset_target({'profile': 'rybbit-hcloud', 'rybbit-host': hostname}, current, 'op',
            '12345678-1234-1234-1234-123456789abc', 'a'*32, '123', current.state.deployment_id)
    current.cloud.connection.assert_not_called()
    controller.remote.assert_not_called()
