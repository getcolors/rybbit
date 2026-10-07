"""Only confirmed remote absence authorizes a fresh SSH authority."""
import pytest
from conftest import keygen
from package_rybbit_blue import access, compute

MISSING = {'status': 'error', 'error': {'code': 'ssh_authority_missing', 'message': 'authority missing'}}
VERIFIED = {'status': 'verified', 'verified_absent': True}


@pytest.mark.parametrize('provider,registrations', [('digitalocean', True), ('google', False)])
async def test_fresh_creation_verifies_all_consumers(monkeypatch, tmp_path, provider, registrations):
    opts = keygen({'workdir': str(tmp_path), 'blue/event': 'create', 'provider-compute': provider})
    # A rendered local plan neither proves nor disproves remote absence.
    plan = tmp_path / opts['profile'] / compute.NODE_ID / 'compute.tf.json'
    plan.parent.mkdir(parents=True)
    plan.write_text('{}')
    calls = []
    async def resource(options, request, operation, env):
        calls.append(operation)
        if operation == 'inspect':
            assert 'verified_absent' not in request
            return MISSING
        assert request['verified_absent'] is True
        return compute.PLACEHOLDER
    async def verify(options, request, env):
        calls.append('verify')
        assert request == {
            'workdir': compute.sdk_workdir(opts),
            'consumers': [{'node_id': 'rybbit-compute', 'state_filename': 'rybbit-node-0.tfstate'}],
            'registrations': [{'name': 'machine-access', 'state_filename': 'rybbit-ssh-registration.tfstate'}] if registrations else [],
        }
        return VERIFIED
    monkeypatch.setattr(access, 'ssh_resource', resource)
    monkeypatch.setattr(access, 'ssh_verify_absent', verify)
    assert (await access.resource_step(opts))['blue/exit'] == 0
    assert calls == ['inspect', 'verify', 'create']


@pytest.mark.parametrize('inspection,overrides,exit_code', [
    (compute.PLACEHOLDER, {}, 0),
    (MISSING, {'compute-require-existing-state': True}, 1),
    (MISSING, {'blue/event': 'delete'}, 1),
    (MISSING, {'blue/event': 'ssh'}, 1),
    ({'status': 'error', 'error': {'code': 'backend_unreadable', 'message': 'read failed'}}, {}, 1),
])
async def test_inspection_never_regenerates_existing_or_uncertain_authority(monkeypatch, tmp_path, inspection, overrides, exit_code):
    calls = []
    async def resource(options, request, operation, env):
        calls.append(operation)
        return inspection
    async def forbidden(*args):
        raise AssertionError('must not verify or create')
    monkeypatch.setattr(access, 'ssh_resource', resource)
    monkeypatch.setattr(access, 'ssh_verify_absent', forbidden)
    result = await access.resource_step(keygen({'workdir': str(tmp_path), 'blue/event': 'create', **overrides}))
    assert result['blue/exit'] == exit_code
    assert calls == ['inspect']


@pytest.mark.parametrize('verification', [
    {'status': 'error', 'error': {'message': 'consumer survives', 'stderr': 'provider detail'}},
    {'status': 'verified', 'verified_absent': False},
    {'status': 'verified', 'verified_absent': 'true'},
    {'status': 'unknown', 'verified_absent': True},
])
async def test_failed_or_incomplete_verification_prevents_creation(monkeypatch, tmp_path, verification):
    async def resource(options, request, operation, env):
        assert operation == 'inspect'
        return MISSING
    async def verify(*args):
        return verification
    monkeypatch.setattr(access, 'ssh_resource', resource)
    monkeypatch.setattr(access, 'ssh_verify_absent', verify)
    result = await access.resource_step(keygen({'workdir': str(tmp_path), 'blue/event': 'create'}))
    assert result['blue/exit'] == 1
    if verification.get('error'):
        assert result['blue/err'] == 'consumer survives\nprovider detail'


@pytest.mark.parametrize('overrides', [{'blue/event': 'build'}, {'blue/event': 'create', 'blue/dry-run': True}])
async def test_planning_does_not_inspect_or_verify(monkeypatch, tmp_path, overrides):
    async def forbidden(*args):
        raise AssertionError('planning must not query authority or consumers')
    monkeypatch.setattr(access, 'ssh_resource', forbidden)
    monkeypatch.setattr(access, 'ssh_verify_absent', forbidden)
    assert (await access.resource_step(keygen({'workdir': str(tmp_path), **overrides})))['blue/exit'] == 0


async def test_creation_failure_is_reported(monkeypatch, tmp_path):
    async def resource(options, request, operation, env):
        return MISSING if operation == 'inspect' else {'status': 'error', 'error': {'message': 'authority reservation lost'}}
    async def verify(*args):
        return VERIFIED
    monkeypatch.setattr(access, 'ssh_resource', resource)
    monkeypatch.setattr(access, 'ssh_verify_absent', verify)
    result = await access.resource_step(keygen({'workdir': str(tmp_path), 'blue/event': 'create'}))
    assert result['blue/exit'] == 1
    assert result['blue/err'] == 'authority reservation lost'
