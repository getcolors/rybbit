import pytest
from conftest import keygen as fixture, fixture as google, keygen_vultr, vultr_fixture
from package_rybbit_blue import workflow, compute, access

@pytest.mark.parametrize('factory',[fixture,google,keygen_vultr,vultr_fixture])
async def test_offline_start_needs_no_credentials(factory, tmp_path):
    result = await access.scoped(lambda: workflow.start_step({**factory(), 'workdir':str(tmp_path),'blue/event':'build'},env={}))
    assert result['blue/exit'] == 0
    assert result['workdir'].endswith('/build')

async def test_deployment_failure_retains_library_diagnostic(monkeypatch):
    async def fail(*args):
        return {'status':'error','error':{'message':'owned state unavailable'}}
    monkeypatch.setattr(compute,'compute_node',fail)
    result = await compute.infrastructure_step({**fixture(),'blue/event':'create', 'rybbit/ssh-resource':compute.PLACEHOLDER})
    assert result['blue/exit'] == 1
    assert result['blue/err'] == 'owned state unavailable'

async def test_delete_inspection_preserves_owned_node(monkeypatch):
    async def read(*args):
        assert args[1]['state_filename'] == 'rybbit-node-0.tfstate'
        return {'status':'ready','params':{'node_id':'rybbit-compute','ip':'203.0.113.7','user':'ubuntu','provider':'google'}}
    monkeypatch.setattr(compute,'compute_node',read)
    result = await compute.load({**fixture(), 'rybbit/ssh-resource':compute.PLACEHOLDER})
    assert result['ip'] == '203.0.113.7' and result['user'] == 'ubuntu'

async def test_destroyed_deployment_skips_remote_cleanup(monkeypatch):
    async def read(*args): return {'status':'destroyed'}
    monkeypatch.setattr(compute,'compute_node',read)
    opts = {**fixture(), 'blue/event':'delete', 'rybbit/ssh-resource':compute.PLACEHOLDER}
    loaded = await compute.load(opts)
    assert loaded['colors-compute/already-destroyed']
    assert await workflow.tools.ansible_step(loaded) == loaded
    assert await compute.step(loaded) == loaded
    assert workflow.next_steps('rybbit/start', ['rybbit/ssh-config'], loaded)[0][0] == 'rybbit/ssh-config'


def test_graph_preserves_application_order():
    for event, edges in [('create',[('start','infrastructure'),('infrastructure','ssh-config'),('ssh-config','dns'),('dns','ansible'),('ansible','acceptance')]),
                         ('delete',[('start','ssh-config'),('ssh-config','ansible'),('ansible','dns'),('dns','infrastructure'),('infrastructure','registration-delete')])]:
        for source,target in edges:
            assert workflow.wire_fn('rybbit/'+source,{'blue/event':event})[1:] == ('rybbit/'+target,)


async def test_cleanup_ip_override_requires_successful_state_inspection(monkeypatch):
    monkeypatch.setattr(workflow.validate, 'secret_errors', lambda opts: [])
    monkeypatch.setattr(access, 'lock', lambda opts: None)
    async def identity(opts): return opts
    for method in ('resource_step','registration_step','agent_step'):
        monkeypatch.setattr(access, method, identity)
    calls = []
    async def loaded(opts, env):
        calls.append(True)
        return {**opts, 'blue/exit':0, 'ip':'203.0.113.7', 'user':'ubuntu'}
    monkeypatch.setattr(compute, 'load', loaded)
    opts = {**fixture(), 'blue/event':'delete', 'compute-prevent-destroy':False, 'ip':'203.0.113.99'}
    result = await workflow.start_step(opts, env={})
    assert calls and result['ip'] == '203.0.113.99' and result['user'] == 'ubuntu'
    async def refused(opts, env): return {**opts, 'blue/exit':1, 'blue/err':'state unreadable'}
    monkeypatch.setattr(compute, 'load', refused)
    assert (await workflow.start_step(opts, env={}))['blue/exit'] == 1


async def test_scope_cleanup_runs_after_body_failure():
    cleaned=[]
    async def body():
        access.register_cleanup(lambda: cleaned.append(True))
        raise RuntimeError('fail')
    with pytest.raises(RuntimeError):
        await access.scoped(body)
    assert cleaned == [True]


async def test_profile_lock_is_exclusive_and_released(tmp_path):
    opts = {**fixture(), 'workdir': str(tmp_path)}
    async def acquire():
        access.lock(opts)
    async def concurrent():
        access.lock(opts)
        with pytest.raises(ValueError, match='another Rybbit operation'):
            await access.scoped(acquire)
    await access.scoped(concurrent)
    await access.scoped(acquire)


async def test_live_delete_opens_agent_only_after_verified_inventory(monkeypatch):
    calls=[]
    monkeypatch.setattr(access, 'lock', lambda opts: None)
    monkeypatch.setattr(workflow.validate, 'secret_errors', lambda opts: [])
    async def resource(opts):
        calls.append('resource')
        return opts
    async def registration(opts):
        calls.append('registration')
        return opts
    async def inspect(opts, env):
        calls.append('inspect')
        return {**opts, 'blue/exit':0, 'ip':'203.0.113.7', 'user':'ubuntu'}
    async def agent(opts):
        calls.append('agent')
        assert opts['ip']=='203.0.113.7'
        return opts
    monkeypatch.setattr(access, 'resource_step', resource)
    monkeypatch.setattr(access, 'registration_step', registration)
    monkeypatch.setattr(compute, 'load', inspect)
    monkeypatch.setattr(access, 'agent_step', agent)
    opts={**fixture(), 'blue/event':'delete', 'compute-prevent-destroy':False}
    await workflow.start_step(opts, env={})
    assert calls == ['resource','registration','inspect','agent']
    calls.clear()
    async def destroyed(opts, env):
        calls.append('inspect')
        return {**opts,'blue/exit':0,'colors-compute/already-destroyed':True}
    monkeypatch.setattr(compute,'load',destroyed)
    await workflow.start_step(opts, env={})
    assert calls == ['resource','registration','inspect']


async def test_registered_provider_dry_run_never_creates_workdir(tmp_path):
    path=tmp_path/'absent'
    opts={**fixture(), 'workdir':str(path), 'blue/event':'create', 'blue/dry-run':True}
    result=await workflow.start_step(opts,env={})
    assert result['blue/exit']==0
    assert not path.exists()
    await access.registration_step(result)
    assert not path.exists()


async def test_lock_refuses_symlink_ancestor(tmp_path):
    target=tmp_path/'target'
    target.mkdir()
    link=tmp_path/'link'
    link.symlink_to(target, target_is_directory=True)
    async def body():
        access.lock({**fixture(),'workdir':str(link/'work')})
    with pytest.raises(ValueError, match='unsafe SDK directory'):
        await access.scoped(body)
    assert not (target/'work').exists()


async def test_preflight_conflict_prevents_resource_creation(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow.validate,'secret_errors',lambda opts: [])
    monkeypatch.setattr(workflow.ssh_config,'preflight', lambda opts: {**opts,'blue/exit':1,'blue/err':'conflict'})
    async def forbidden(opts):
        raise AssertionError('resource creation before preflight')
    monkeypatch.setattr(access,'resource_step',forbidden)
    result=await access.scoped(lambda: workflow.start_step({**fixture(),'blue/event':'create','workdir':str(tmp_path)},env={}))
    assert result['blue/err']=='conflict'
