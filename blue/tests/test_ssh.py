from conftest import keygen as fixture
from package_rybbit_blue import ssh, access

async def test_build_identity_is_public_and_deterministic():
    opts = await access.agent_step({**fixture(), 'blue/event': 'build'})
    assert opts['ssh-private-key-path'] == '/home/build-placeholder/compute/rybbit-keygen-fixture/ssh/machine-access/identity.pub'
    args = ssh.identity_args(opts)
    assert 'IdentityFile=none' in args and 'IdentitiesOnly=yes' in args
    assert 'IdentityAgent=/home/build-placeholder/agent.sock' in args
    assert 'ForwardAgent=no' in args and 'ControlMaster=no' in args

def test_live_identity_is_never_generated_by_the_application():
    opts = fixture()
    assert ssh.with_machine_key(opts) == opts
