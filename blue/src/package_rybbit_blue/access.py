"""Scoped encrypted compute access; lock ownership is shared across colours."""
import contextvars
import fcntl
import os
import stat
from pathlib import Path
from blue.scope import with_scope
from blue.process import run_inherit
from colors_compute import ssh_resource, start_agent, compute_registration, registration_plan
from . import compute
from colors_compute.node import _directory

_register = contextvars.ContextVar('rybbit_access_scope', default=None)
_locked_paths = set()


def register_cleanup(cleanup):
    """Attach a temporary resource when called inside an Rybbit access scope."""
    register = _register.get()
    if register is not None:
        register('resource', cleanup)


async def scoped(body):
    async def run(register):
        token = _register.set(register)
        try:
            return await body()
        finally:
            _register.reset(token)
    return await with_scope(run)


def lock(opts):
    register = _register.get()
    if register is None:
        raise ValueError('Rybbit runtime requires an access scope')
    directory = Path(compute.sdk_workdir(opts)) / opts['profile']
    _directory(directory)
    for parent in (directory.parent, directory):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('unsafe Rybbit work directory')
        parent.chmod(0o700)
    path = str(directory / '.rybbit.lock')
    if path in _locked_paths:
        raise ValueError('another Rybbit operation owns this profile')
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('unsafe Rybbit lock')
        os.fchmod(fd, 0o600)
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another Rybbit operation owns this profile') from None
        _locked_paths.add(path)
        def close():
            try:
                os.close(fd)
            finally:
                _locked_paths.discard(path)
        register('resource', close)
    except BaseException:
        os.close(fd)
        raise


async def resource_step(opts):
    if compute.planning(opts):
        return {**opts, 'rybbit/ssh-resource': compute.PLACEHOLDER, 'blue/exit': 0}
    existing = (Path(compute.sdk_workdir(opts)) / opts['profile'] / compute.NODE_ID / 'compute.tf.json').exists()
    operation = 'inspect' if existing or opts.get('compute-require-existing-state') or opts.get('blue/event') != 'create' else 'create'
    result = await ssh_resource(compute.library_options(opts), compute.ssh_request(opts), operation, dict(os.environ))
    return {**opts, 'rybbit/ssh-resource': result, 'blue/exit': 0} if result['status'] == 'ready' else compute.failure(opts, result)


def placeholder_registration(opts):
    return {'status': 'ready', 'reference': 'registration:build-placeholder', 'provider': opts['provider-compute'], 'ssh_resource_reference': compute.resource(opts)['reference'], 'fingerprint': compute.resource(opts)['fingerprint'], 'id': '0'}


async def registration_step(opts):
    if opts.get('blue/dry-run') or not compute.registration(opts):
        return opts
    operation = 'create' if opts.get('blue/event') == 'create' and not opts.get('compute-require-existing-state') else 'inspect'
    result = compute.canonicalize(registration_plan(compute.library_options(opts), compute.registration_request(opts))) if compute.planning(opts) else await compute_registration(compute.library_options(opts), compute.registration_request(opts), operation)
    if result['status'] in ('ready', 'built'):
        return {**opts, 'rybbit/ssh-registration': result if result['status'] == 'ready' else placeholder_registration(opts), 'blue/exit': 0}
    if result['status'] == 'destroyed' and opts.get('blue/event') == 'delete':
        return {**opts, 'rybbit/registration-destroyed': True, 'rybbit/ssh-registration': placeholder_registration(opts)}
    return compute.failure(opts, result)


async def agent_step(opts):
    if compute.planning(opts):
        return {**opts, 'ssh-private-key-path': f'/home/build-placeholder/compute/{opts["profile"]}/ssh/machine-access/identity.pub', 'rybbit/agent-socket': '/home/build-placeholder/agent.sock'}
    register = _register.get()
    if register is None:
        raise ValueError('Rybbit runtime requires an access scope')
    agent = await start_agent([{'opts': compute.library_options(opts), 'request': compute.ssh_request(opts), 'resource': compute.resource(opts)}], dict(os.environ), register)
    return {**opts, 'rybbit/agent-socket': agent['socket'], 'ssh-private-key-path': agent['identities'][compute.resource(opts)['reference']]}


async def registration_delete(opts):
    if not compute.registration(opts) or opts.get('rybbit/registration-destroyed'):
        return opts
    result = await compute_registration(compute.library_options(opts), compute.registration_request(opts), 'delete')
    return {**opts, 'blue/exit': 0} if result['status'] == 'destroyed' else compute.failure(opts, result)


def identity_args(opts):
    path = opts.get('ssh-private-key-path')
    return ['-F', '/dev/null', '-o', 'IdentityFile=none', '-i', path, '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=' + (opts.get('rybbit/agent-socket') or 'none'), '-o', 'ForwardAgent=no', '-o', 'ControlMaster=no', '-o', 'ControlPersist=no', '-S', 'none'] if path else []


def ssh_args(opts):
    return ['ssh', '-p', '22', '-l', opts.get('user'), '-o', 'StrictHostKeyChecking=accept-new', *identity_args(opts), '--', opts.get('ip')]


async def ssh_step(opts):
    if compute.planning(opts):
        return opts
    if any(not isinstance(opts.get(key), str) or not opts[key].strip() for key in ('ip', 'user', 'ssh-private-key-path', 'rybbit/agent-socket')):
        return {**opts, 'blue/exit': 1, 'blue/err': 'SSH requires a resolved address, login and scoped identity'}
    result = run_inherit(ssh_args(opts))
    return {**opts, 'blue/exit': result.exit}
