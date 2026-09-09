import pytest
from conftest import keygen as fixture, fixture as optout, keygen_vultr as do_fixture, vultr_fixture as do_optout
from package_rybbit_blue import workflow, compute

@pytest.mark.parametrize('factory',[fixture,optout,do_fixture,do_optout])
async def test_offline_start_needs_no_credentials(factory):
    result = await workflow.start_step({**factory(),'blue/event':'build'},env={})
    assert result['blue/exit'] == 0

async def test_deployment_failure_retains_library_diagnostic(monkeypatch):
    async def fail(*args):
        return {'status':'error','errors':['legacy compute state requires migration']}
    monkeypatch.setattr(compute,'orchestrate',fail)
    result = await compute.infrastructure_step({**fixture(),'blue/event':'create'})
    assert result['blue/exit'] == 1
    assert result['blue/err'] == 'legacy compute state requires migration'

async def test_delete_inspection_preserves_owned_node(monkeypatch):
    async def read(*args):
        assert args[3]['legacy_state_keys'] == ['rybbit-keygen-fixture/rybbit-infrastructure.tfstate']
        return {'status':'present','cluster':{'nodes':[{'node_id':'node-0','ip':'203.0.113.7','user':'ubuntu','provider':'azure'}]},'key':{'private_key_path':'/tmp/explicit-key'}}
    monkeypatch.setattr(compute,'read_deployment',read)
    result = await compute.load(fixture())
    assert result['ip'] == '203.0.113.7' and result['user'] == 'ubuntu'
    assert result['ssh-private-key-path'] == '/tmp/explicit-key'

async def test_destroyed_deployment_stops_application_cleanup(monkeypatch):
    async def read(*args): return {'status':'destroyed'}
    monkeypatch.setattr(compute,'read_deployment',read)
    assert (await compute.load(fixture()))['rybbit/already-destroyed']

def test_graph_preserves_application_order():
    create={'blue/event':'create'}
    for source,target in [('start','infrastructure'),('infrastructure','ssh-config'),('ssh-config','dns'),('dns','ansible'),('ansible','acceptance')]:
        assert workflow.wire_fn('rybbit/'+source,create)[1:] == ('rybbit/'+target,)
    delete={'blue/event':'delete'}
    assert workflow.wire_fn('rybbit/start',delete)[1:] == ('rybbit/ansible',)
    assert workflow.wire_fn('rybbit/infrastructure',delete)[1:] == ()

async def test_cleanup_ip_override_requires_successful_state_inspection(monkeypatch):
    monkeypatch.setattr(workflow.validate, 'secret_errors', lambda opts: [])
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
