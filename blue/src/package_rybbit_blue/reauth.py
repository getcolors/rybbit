"""Interactive recovery for expired local Google user ADC during SSH."""
import json
from pathlib import Path
import sys
from blue.cli import read_pars
from blue.process import run_inherit

CREDENTIAL_OVERRIDES = (
    'GOOGLE_APPLICATION_CREDENTIALS', 'GOOGLE_CREDENTIALS', 'GOOGLE_CLOUD_KEYFILE_JSON',
    'GCLOUD_KEYFILE_JSON', 'GOOGLE_OAUTH_ACCESS_TOKEN', 'GOOGLE_IMPERSONATE_SERVICE_ACCOUNT',
    'CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE', 'CLOUDSDK_AUTH_ACCESS_TOKEN',
    'CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT',
)


def read_login_pars(opts, env):
    # The first CLI overlay lacks a type for this optional boolean.
    if 'COLORS_PAR_RYBBIT_SSH_LOGIN_BROWSER' in env:
        opts = {**opts, 'rybbit-ssh-login-browser': True}
    return read_pars(opts, env)


def local_user_adc(env):
    if any((env.get(key) or '').strip() for key in CREDENTIAL_OVERRIDES):
        return False
    try:
        directory = Path(env.get('CLOUDSDK_CONFIG') or Path(env.get('HOME') or Path.home()) / '.config' / 'gcloud')
        return json.loads((directory / 'application_default_credentials.json').read_text()).get('type') == 'authorized_user'
    except (OSError, ValueError, AttributeError):
        return False


def interactive():
    return sys.stdin.isatty() and sys.stderr.isatty()


def announce(message):
    print(message, file=sys.stderr, flush=True)


def recoverable(opts, result):
    error = result.get('error') or {}
    return (opts.get('blue/event') == 'ssh' and not opts.get('blue/dry-run')
            and opts.get('provider-compute') == 'google' and result.get('status') == 'error'
            and error.get('auth_reason') == 'google_reauth_required'
            and error.get('stage') == 'plan' and error.get('command') == ['tofu', 'plan']
            and error.get('infrastructure_changes') == 'none')


def explain(result, message):
    return {**result, 'error': {**{k: v for k, v in result['error'].items() if k != 'stderr'}, 'message': message}}


async def resolve_with_login(opts, env, resolve):
    result = await resolve()
    if not recoverable(opts, result):
        return result
    local = local_user_adc(env)
    command = ['gcloud', 'auth', 'application-default', 'login']
    if opts.get('rybbit-ssh-login-browser') is False:
        command.append('--no-launch-browser')
    hint = (f"Run `{' '.join(command)}`, then retry SSH." if local else
            'Renew the configured Google credentials, then retry SSH; automatic login requires local user ADC without credential overrides.')
    if not local or not interactive():
        return explain(result, 'Google authentication expired. ' + hint)
    announce('Google authentication expired. Starting Google sign-in…')
    announce('$ ' + ' '.join(command))
    login = run_inherit(command)
    if login.exit != 0:
        return {**explain(result, 'Google sign-in could not start. Check that `gcloud` is installed and available in this terminal, then retry SSH.' if login.exit in (126, 127) else 'Google sign-in did not complete. SSH was not started; retry SSH to try again.'), 'rybbit/login-exit': login.exit or 1}
    announce('Google authentication complete. Retrying address lookup…')
    retried = await resolve()
    return explain(retried, 'Google authentication is still expired after login. Check the configured credential source; SSH was not started.') if recoverable(opts, retried) else retried
