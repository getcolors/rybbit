"""Rybbit owns one greenfield v2 node and its encrypted SSH authority."""
from . import reauth
import json
import os
import stat
import re
from pathlib import Path
from blue.cli import stage_dir
from colors_compute import node_plan, compute_node, registry, ssh_plan, resolve_connection
from colors_compute.node import _directory, _write

NODE_ID = 'rybbit-compute'
PLACEHOLDER = {'status': 'ready', 'reference': 'ssh-resource:build-placeholder',
    'public_key': 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    'fingerprint': 'SHA256:kmYcvdi2GkPeWxB6XLjrZB8JHsy2Hm8luHMFp9GMvqk'}
API_ERROR = 'compute-api-version must be 2; existing deployments must retain their pinned launchers'


def planning(opts):
    return opts.get('blue/event') == 'build' or opts.get('blue/dry-run', False)


def sdk_workdir(opts):
    return str(Path(os.path.abspath(stage_dir(opts, NODE_ID))).parent.parent)


def library_options(opts):
    return {k: v for k, v in opts.items() if k not in ('ssh-private-key-path', 'ssh-public-key-path', 'rybbit-ssh-passphrase') and '/' not in k}


def resource(opts):
    value = opts.get('rybbit/ssh-resource') or (PLACEHOLDER if planning(opts) else None)
    if value is None:
        raise ValueError('SSH resource unavailable')
    return value


def registration(opts):
    return bool(registry()['compute'].get(opts.get('provider-compute'), {}).get('registration'))


def ssh_request(opts):
    return {'name': 'machine-access', 'workdir': sdk_workdir(opts), 'passphrase_env': 'COLORS_PAR_RYBBIT_SSH_PASSPHRASE'}


def registration_request(opts):
    return {'name': 'machine-access', 'workdir': sdk_workdir(opts), 'state_filename': 'rybbit-ssh-registration.tfstate', 'ssh_resource': resource(opts)}


def source_cidrs(opts, suffix, neutral):
    value = next((opts[key] for key in ('compute-' + suffix, neutral, str(opts.get('provider-compute')) + '-' + suffix) if opts.get(key) is not None), None)
    return [v for v in re.split(r'[,\s]+', value) if v] if isinstance(value, str) else value


def requirements(opts):
    ssh = source_cidrs(opts, 'ssh-sources', 'rybbit-ssh-sources')
    http = source_cidrs(opts, 'http-sources', 'rybbit-http-sources')
    if not isinstance(ssh, list) or not ssh:
        raise ValueError('compute-ssh-sources is required')
    if not isinstance(http, list):
        raise ValueError('compute-http-sources is required')
    return {'ingress': [{'id': 'ssh', 'protocol': 'tcp', 'from_port': 22, 'to_port': 22, 'sources': ssh}]
            + [{'id': 'http-' + str(port), 'protocol': 'tcp', 'from_port': port, 'to_port': port, 'sources': http} for port in (80, 443) if http]
            + ([{'id': 'http3', 'protocol': 'udp', 'from_port': 443, 'to_port': 443, 'sources': http}] if http else []),
            'egress': 'all', 'private_filter': False}


def request(opts):
    result = {'node_id': NODE_ID, 'state_filename': 'rybbit-node-0.tfstate', 'workdir': sdk_workdir(opts), 'ssh_resource': resource(opts), 'security': requirements(opts)}
    provider = opts.get('provider-compute')
    if 'compute-network-mode' in opts:
        result['network'] = {'mode': opts['compute-network-mode']}
    if registration(opts):
        result['ssh_registration'] = opts.get('rybbit/ssh-registration') or ({'status': 'ready', 'reference': 'registration:build-placeholder', 'provider': provider, 'ssh_resource_reference': resource(opts)['reference'], 'fingerprint': resource(opts)['fingerprint'], 'id': '0'} if planning(opts) else None)
    return result


def errors(opts):
    if type(opts.get('compute-api-version')) is not int or opts['compute-api-version'] != 2:
        return [API_ERROR]
    if opts.get('provider-backend') not in ('s3', 'r2'):
        return ['compute state requires an s3 or r2 backend']
    legacy_keys = {'ssh-keygen', 'ssh-key-path', 'ssh-private-key-path', 'ssh-public-key-path', 'ssh-key-id'}
    for adapter in registry()['compute'].values():
        legacy_keys.add(adapter.get('ssh-setting'))
        legacy_keys.update((adapter.get('ssh-aliases') or {}).keys())
    if any(isinstance(key, str) and key in opts for key in legacy_keys):
        return ['external SSH keys are outside the single-node contract']
    try:
        planned = {**opts, 'blue/dry-run': True}
        ssh_plan(library_options(planned), ssh_request(planned))
        node_plan(library_options(planned), request(planned))
        return []
    except ValueError as exc:
        return [str(exc)]


def fallback_params(opts):
    if not planning(opts):
        raise ValueError('compute inventory unavailable')
    user = registry()['compute'].get(opts.get('provider-compute'), {}).get('user', 'root')
    return {'provider': opts.get('provider-compute'), 'node_id': NODE_ID, 'ip': '192.0.2.10', 'user': user, 'sudoer': user,
            'name': str(opts.get('profile')) + '-rybbit-compute', 'ssh-keygen': True, 'ssh-private-key-path': f'/home/build-placeholder/compute/{opts.get("profile")}/ssh/machine-access/identity.pub', 'rybbit/agent-socket': '/home/build-placeholder/agent.sock'}


def failure(opts, result):
    error = result.get('error') or {}
    return {**opts, 'blue/exit': result.get('rybbit/login-exit', 1), 'blue/err': (error.get('message') or 'compute lifecycle refused') + ('\n' + error['stderr'] if error.get('stderr') else '')}


def params(opts, result):
    values = result['params']
    return {**values, 'name': values.get('name') or str(opts.get('profile')) + '-rybbit-compute', 'sudoer': values.get('sudoer') or values.get('user'), 'ssh-keygen': True, 'ssh-private-key-path': opts.get('ssh-private-key-path') or (f'/home/build-placeholder/compute/{opts.get("profile")}/ssh/machine-access/identity.pub' if planning(opts) else None), 'rybbit/agent-socket': opts.get('rybbit/agent-socket')}


def adopt(opts, result):
    values = params(opts, result)
    return {**opts, **values, 'rybbit/compute-params': values, 'colors-compute/node': result['params'], 'blue/exit': 0}


async def step(opts):
    if opts.get('colors-compute/already-destroyed'):
        return opts
    if planning(opts):
        canonicalize(node_plan(library_options(opts), request(opts)))
        return {**opts, **fallback_params(opts), 'rybbit/compute-params': fallback_params(opts), 'blue/exit': 0}
    result = await compute_node(library_options(opts), request(opts), opts['blue/event'])
    if result['status'] == 'ready':
        return adopt(opts, result)
    if result['status'] == 'destroyed':
        return {**opts, 'blue/exit': 0}
    return failure(opts, result)


async def load(opts, env=None):
    result = await reauth.resolve_with_login(opts, env if env is not None else dict(os.environ), lambda: resolve_connection(library_options(opts), request(opts), env)) if opts.get('blue/event') in ('ssh', 'describe') else await compute_node(library_options(opts), request(opts), 'inspect', env)
    if result['status'] == 'destroyed' and opts.get('blue/event') == 'delete':
        return {**opts, 'blue/exit': 0, 'colors-compute/already-destroyed': True}
    if result['status'] == 'destroyed':
        return {**opts, 'blue/exit': 1, 'blue/err': 'compute node is destroyed'}
    return adopt(opts, result) if result['status'] == 'ready' else failure(opts, result)


async def connection(opts):
    result = await resolve_connection(library_options(opts), request(opts))
    return adopt(opts, result) if result['status'] == 'ready' else failure(opts, result)


def canonicalize(plan):
    directory = Path(plan['directory'])
    _directory(directory)
    for owned in (directory.parent.parent, directory.parent, directory):
        if owned.lstat().st_uid != os.getuid():
            raise ValueError('unsafe Rybbit work directory ownership')
        owned.chmod(0o700)
    for filename, document in plan['documents'].items():
        target = directory / filename
        if target.exists() and target.lstat().st_uid != os.getuid():
            raise ValueError('unsafe Rybbit working file ownership')
        _write(target, (json.dumps(document, sort_keys=True, indent=2) + '\n').encode())
    return {**plan, 'status': 'built'}


infrastructure_step = step
