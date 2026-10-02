import json
from types import SimpleNamespace
import pytest
from package_rybbit_blue import reauth, compute

EXPIRED = {'status': 'error', 'error': {'code': 'command_failed', 'stage': 'plan', 'command': ['tofu', 'plan'],
           'infrastructure_changes': 'none', 'auth_reason': 'google_reauth_required',
           'message': 'Required command failed.', 'stderr': '[structured output suppressed]'}}
OPTS = {'blue/event': 'ssh', 'provider-compute': 'google'}

@pytest.mark.parametrize('exit,second,count', [(0, {'status': 'ready'}, 2), (0, EXPIRED, 2), (130, EXPIRED, 1), (127, EXPIRED, 1)])
async def test_retry_and_cancel(monkeypatch, exit, second, count):
    calls, log = [], []
    monkeypatch.setattr(reauth, 'local_user_adc', lambda _: True)
    monkeypatch.setattr(reauth, 'interactive', lambda: True)
    monkeypatch.setattr(reauth, 'announce', log.append)
    def login(argv):
        assert argv == ['gcloud', 'auth', 'application-default', 'login', '--no-launch-browser']
        return SimpleNamespace(exit=exit)
    monkeypatch.setattr(reauth, 'run_inherit', login)
    async def resolve():
        calls.append(1)
        return EXPIRED if len(calls) == 1 else second
    result = await reauth.resolve_with_login({**OPTS, 'rybbit-ssh-login-browser': False}, {}, resolve)
    assert len(calls) == count and log
    if exit:
        assert compute.failure({}, result)['blue/exit'] == exit
    else:
        assert result['status'] == second['status']
    if second == EXPIRED:
        assert 'stderr' not in result['error']

@pytest.mark.parametrize('opts,result,local,tty', [
    (OPTS, EXPIRED, True, False), (OPTS, EXPIRED, False, True),
    *[({**OPTS, 'blue/event': event}, EXPIRED, True, True) for event in ('create', 'delete', 'build')],
    ({**OPTS, 'blue/dry-run': True}, EXPIRED, True, True),
    ({**OPTS, 'provider-compute': 'aws'}, EXPIRED, True, True),
    (OPTS, {**EXPIRED, 'error': {**EXPIRED['error'], 'infrastructure_changes': 'possible'}}, True, True),
    (OPTS, {**EXPIRED, 'error': {**EXPIRED['error'], 'auth_reason': None}}, True, True),
    (OPTS, {'status': 'ready'}, True, True),
])
async def test_no_unexpected_login(monkeypatch, opts, result, local, tty):
    monkeypatch.setattr(reauth, 'local_user_adc', lambda _: local)
    monkeypatch.setattr(reauth, 'interactive', lambda: tty)
    monkeypatch.setattr(reauth, 'run_inherit', lambda _: pytest.fail('unexpected login'))
    calls = []
    async def resolve():
        calls.append(1)
        return result
    await reauth.resolve_with_login(opts, {}, resolve)
    assert len(calls) == 1


def test_only_local_user_adc(tmp_path):
    env = {'CLOUDSDK_CONFIG': str(tmp_path)}
    file = tmp_path / 'application_default_credentials.json'
    assert not reauth.local_user_adc(env)
    for kind in ('authorized_user', 'service_account', 'external_account', 'impersonated_service_account'):
        file.write_text(json.dumps({'type': kind, 'refresh_token': 'PRIVATE-CANARY'}))
        assert reauth.local_user_adc(env) == (kind == 'authorized_user')
        for key in reauth.CREDENTIAL_OVERRIDES:
            assert not reauth.local_user_adc({**env, key: 'override'})
    file.write_text('malformed')
    assert not reauth.local_user_adc(env)

async def test_login_uses_sdk_process(monkeypatch, tmp_path):
    import os
    shim, marker = tmp_path / 'gcloud', tmp_path / 'login-args'
    shim.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$LOGIN_MARKER"\nexit 0\n')
    shim.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('LOGIN_MARKER', str(marker))
    monkeypatch.setattr(reauth, 'local_user_adc', lambda _: True)
    monkeypatch.setattr(reauth, 'interactive', lambda: True)
    calls = []
    async def resolve():
        calls.append(1)
        return EXPIRED if len(calls) == 1 else {'status': 'ready'}
    result = await reauth.resolve_with_login(OPTS, {}, resolve)
    assert result['status'] == 'ready' and len(calls) == 2
    assert marker.read_text() == 'auth\napplication-default\nlogin\n'

@pytest.mark.parametrize('value,expected', [('false', False), ('true', True), ('FALSE', False), ('invalid', 'invalid')])
def test_login_browser_env_after_cli_overlay(value, expected):
    assert reauth.read_login_pars({'rybbit-ssh-login-browser': value}, {'COLORS_PAR_RYBBIT_SSH_LOGIN_BROWSER': value})['rybbit-ssh-login-browser'] == expected
