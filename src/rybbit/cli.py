"""Rybbit CLI: explicit sequential operations over private deployment state."""
import argparse
import os
from pathlib import Path
import re
import sys
import time
from pocketdeploy.common import DeployError, local_path
from pocketdeploy.runtime import session, initialize, recorded_operation
from pocketdeploy.state import deployment_lock
from pocketdeploy.host import Host
from pocketdeploy.output import Reporter
from pocketdeploy import vault
from .config import load
from . import controller

COMMANDS = ('init', 'validate', 'plan', 'prepare', 'converge', 'status', 'acceptance', 'backup', 'counts',
            'fence', 'freeze', 'restore', 'ssh', 'ssh-install', 'ssh-uninstall', 'delete', 'vault-save', 'vault-restore')

class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise DeployError('Invalid arguments; run rybbit --help.', code='usage')

def parser():
    p = Parser(prog='rybbit', description='Rybbit on Hetzner using Python and SQLite.', allow_abbrev=False)
    p.add_argument('command', choices=COMMANDS)
    p.add_argument('-f', '--file', default='colors.yml')
    for flag in ('dry-run', 'json', 'quiet', 'verbose', 'overwrite', 'no-backups'):
        p.add_argument('--'+flag, action='store_true')
    for flag in ('ssh-command', 'document', 'version', 'destination', 'archive', 'sha256', 'stack-env-file'):
        p.add_argument('--'+flag)
    return p

def execute(args):
    if args.json and args.command == 'ssh':
        raise DeployError('--json does not support interactive SSH.')
    if args.quiet and args.verbose:
        raise DeployError('--quiet and --verbose conflict.')
    if args.dry_run and args.command not in ('converge', 'prepare', 'delete', 'plan'):
        raise DeployError('--dry-run supports prepare, converge, delete and plan.')
    if args.ssh_command and args.command != 'ssh':
        raise DeployError('--ssh-command requires ssh.')
    if (args.document or args.version or args.destination or args.overwrite) and args.command != 'vault-restore':
        raise DeployError('Vault restore options require vault-restore.')
    if (args.archive or args.sha256) and args.command != 'restore':
        raise DeployError('Archive options require restore.')
    if args.command == 'restore' and (not args.archive or not args.sha256 or not re.fullmatch(r'[a-f0-9]{64}', args.sha256)):
        raise DeployError('Restore requires --archive and its explicit --sha256.')
    if args.stack_env_file and args.command != 'prepare':
        raise DeployError('--stack-env-file requires prepare before database initialization.')
    if args.no_backups and args.command != 'converge':
        raise DeployError('--no-backups requires converge.')
    config, deployment = load(args.file)
    root = Path(deployment['_root'])
    if args.command == 'validate':
        return {'valid': True, 'profile': config['profile']}
    if args.command == 'vault-restore':
        destination = Path(args.destination).absolute() if args.destination else root
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        with deployment_lock(local_path(destination, deployment['state-file'])):
            return vault.restore(deployment, destination, args.document, args.version, args.overwrite)
    if args.command == 'ssh-uninstall':
        return {'removed': controller.aliases(config, Host(deployment, None, root), remove=True)}
    read_only = args.command in ('plan', 'status', 'counts') or args.dry_run
    with session(deployment, read_only=read_only, allow_create=args.command in ('init','plan','prepare','converge')) as current:
        cloud, host, state = current.cloud, current.host, current.state
        if args.command == 'init':
            return initialize(deployment, state, host, current.root)
        if args.command == 'vault-save':
            return vault.save(deployment, state, current.root)
        if args.command in ('status', 'counts'):
            observed = cloud.inspect()
            result = {'resources': observed, 'state': state.safe_status() if state else None}
            if observed.get('compute'):
                result['application'] = controller.remote(host, cloud.connection(), config, args.command)
            return result
        if read_only:
            services = controller.dns(config, state)
            return {'compute': cloud.plan_delete() if args.command == 'delete' else cloud.plan(),
                    'dns': services.plan_delete() if args.command == 'delete' else services.plan(),
                    'protected': deployment['compute-prevent-destroy']}
        if args.command == 'ssh':
            return {'ssh_exit': host.ssh(cloud.connection(), args.ssh_command)}
        if args.command == 'ssh-install':
            connection = cloud.connection()
            host.prepare_transport(connection)
            return {'changed': controller.aliases(config, host, connection)}
        with recorded_operation(state, args.command, deployment['_desired_hash']) as operation:
            if args.command in ('prepare', 'converge'):
                secret = controller.private_text(args.stack_env_file) if args.stack_env_file else None
                return controller.converge(config, deployment, current, operation,
                    prepare=args.command == 'prepare', stack_env=secret, enable_backups=not args.no_backups)
            if args.command == 'restore':
                return controller.restore(config, current, operation, args.archive, args.sha256)
            if args.command in ('fence', 'freeze', 'backup', 'acceptance'):
                return controller.remote(host, cloud.connection(), config, args.command)
            if args.command == 'delete':
                return controller.delete(config, deployment, current, operation)
            raise DeployError('Unsupported command.')

def main(argv=None):
    os.umask(0o077)
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        parser().print_help()
        return 0
    reporter = Reporter(json_mode='--json' in argv, quiet='--quiet' in argv)
    started = time.monotonic()
    try:
        args = parser().parse_args(argv)
        reporter.command, reporter.verbose = args.command, args.verbose
        with reporter.activate():
            result = execute(args)
        if args.command == 'ssh':
            return result['ssh_exit'] if result['ssh_exit'] >= 0 else 128-result['ssh_exit']
        reporter.success(result, elapsed_seconds=time.monotonic()-started)
        return 0
    except KeyboardInterrupt:
        reporter.failure('Interrupted; deployment retained for inspection.', code='interrupted')
        return 130
    except DeployError as error:
        reporter.failure(str(error), code=error.code, stage=error.stage or reporter.failed_stage,
                         elapsed_seconds=time.monotonic()-started, diagnostic=error.diagnostic)
        return 2 if error.code == 'usage' else 1
    except Exception:
        reporter.failure('Rybbit operation failed; private output suppressed. Inspect deployment state before retry.')
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
