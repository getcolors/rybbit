"""Sequential Rybbit lifecycle; shared infrastructure stays in PocketDeploy."""
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import subprocess
from pocketdeploy.common import DeployError
from pocketdeploy.runtime import initialize, delete_compute
from pocketdeploy.dns import CloudflareDNS
from pocketdeploy.transport import python_command
from pocketdeploy import ssh_config
from . import remote as remote_program


def private_text(filename):
    path = Path(filename).absolute()
    info = path.lstat()
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > 1024 * 1024):
        raise DeployError('Secret input must be a private owned regular file.')
    return path.read_text()


def dns(config, state):
    return CloudflareDNS(config, state, [config['rybbit-host']])


def remote(host, connection, config, action, **extra):
    c = {**config, 'compute-name': config['profile'] + '-server'}
    payload = {'action': action, 'config': c, **extra}
    if action in ('prepare', 'converge'):
        payload['files'] = remote_program.render(c)
        payload['backup_credentials'] = backup_credentials()
    result = host.run_python(connection, Path(remote_program.__file__).read_text(), payload, timeout=1800)
    if not isinstance(result, dict) or result.get('ok') is not True:
        stage = result.get('stage') if isinstance(result, dict) else None
        allowed = {'validation', 'packages', 'configuration', 'restore', 'backup', 'acceptance', 'converge', 'prepare', 'fence', 'status', 'counts', 'freeze-ingress', 'freeze-quiet', 'freeze-stop'}
        raise DeployError('Rybbit remote operation failed; private output suppressed.',
                          stage=stage if stage in allowed else action)
    return safe_result(result, action, config)


def safe_result(result, action, config):
    """Validate remote evidence before publishing any of it to the operator."""
    def require(condition):
        if not condition:
            raise DeployError('Invalid remote evidence; private output suppressed.')
    def exact(keys):
        require(set(result) == {'ok', *keys})
    def number(value):
        return type(value) is int and 0 <= value <= 2**63 - 1
    def digest(value):
        return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
    if action == 'status':
        exact({'services', 'backup_timer'})
        require(type(result['backup_timer']) is bool and isinstance(result['services'], list) and len(result['services']) <= 6)
        seen = set()
        for row in result['services']:
            require(isinstance(row, dict) and set(row) == {'Service', 'State', 'Health'})
            require(row['Service'] in ('postgres', 'clickhouse', 'redis', 'backend', 'client', 'caddy') and row['Service'] not in seen)
            seen.add(row['Service'])
            require(row['State'] in ('created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead'))
            require(row['Health'] in (None, '', 'starting', 'healthy', 'unhealthy'))
    elif action in ('counts', 'freeze'):
        if action == 'freeze':
            exact({'counts', 'fenced', 'quiet_seconds', 'queue_depth_verified'})
            require(result['fenced'] is True and result['quiet_seconds'] == 60
                    and type(result['quiet_seconds']) is int and result['queue_depth_verified'] is False)
        else:
            exact({'counts'})
        values = result['counts']
        require(isinstance(values, dict) and set(values) == {'user', 'organization', 'sites', 'events', 'replay_sessions', 'replay_events', 'replay_bytes', 'replay_rows'})
        require(all(number(value) for value in values.values()))
    elif action == 'backup':
        exact({'archive', 'sha256', 'uploaded'})
        require(type(result['uploaded']) is bool and digest(result['sha256']))
        name = result['archive']
        require(isinstance(name, str) and Path(name).parent == Path(config['rybbit-backup-dir'])
                and re.fullmatch(r'rybbit-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.tar\.gz', Path(name).name))
    elif action == 'restore':
        if result.get('already_complete') is True:
            exact({'restored', 'already_complete'})
        else:
            exact({'restored', 'sha256', 'complete', 'postgres_tables'})
            require(digest(result['sha256']) and result['complete'] is True and number(result['postgres_tables']) and result['postgres_tables'] > 0)
        require(result['restored'] is True)
    elif action == 'acceptance':
        require(result.get('health') is True)
        if result.get('ingestion') == 'not-configured':
            exact({'health', 'ingestion', 'reason'})
            require(result['reason'] == 'no organization')
        else:
            exact({'health', 'ingestion', 'marker', 'matched_events'})
            require(result['ingestion'] == 'verified' and isinstance(result['marker'], str)
                    and re.fullmatch(r'/colors-acceptance-[0-9a-f]{32}', result['marker'])
                    and number(result['matched_events']) and result['matched_events'] > 0)
    else:
        expected = {'prepare': {'prepared', 'fenced'}, 'converge': {'converged'}, 'fence': {'fenced'}}.get(action)
        require(expected is not None)
        exact(expected)
        require(all(result[key] is True for key in expected))
    return result


def backup_credentials():
    keys = {'access_key_id': 'COLORS_PAR_RYBBIT_BACKUP_R2_ACCESS_KEY_ID',
            'secret_access_key': 'COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY'}
    result = {k: os.environ.get(v) for k, v in keys.items()}
    if not all(result.values()):
        raise DeployError('Rybbit R2 backup credentials are required.')
    return result


def aliases(config, host, connection=None, *, remove=False, check_only=False):
    try:
        return ssh_config.update({'host_alias': config['profile'], 'block_state': 'absent' if remove else 'present',
            'ssh_hosts': [] if remove else [{'name': config['profile'], 'ip': connection['ip'], 'user': connection['user']}],
            'identity_file': str(host.key), 'known_hosts_file': str(host.known), 'check_only': check_only}, owner='RYBBIT')
    except (ValueError, OSError):
        raise DeployError('SSH alias configuration needs an early Include config.d/* before applicable options; resolve alias collisions or explicitly migrate legacy owned blocks. Private details suppressed.',
                          code='ssh_alias_configuration', stage='ssh-alias-preflight' if check_only else 'ssh-alias') from None


def converge(config, deployment, current, operation, *, prepare=False, stack_env=None, enable_backups=True):
    state, cloud, host = current.state, current.cloud, current.host
    if stack_env is not None:
        if not prepare:
            raise DeployError('Retained secrets require prepare before database initialization.')
        try:
            remote_program.stack_settings(config, stack_env)
        except (ValueError, TypeError, KeyError):
            raise DeployError('Retained stack settings are invalid; private content suppressed.') from None
    backup_credentials()
    aliases(config, host, {'ip': '192.0.2.1', 'user': deployment.get('ssh-user', 'ubuntu')}, check_only=True)
    service = dns(config, state)
    service.preflight()
    cloud.plan()
    initialize(deployment, state, host, current.root)
    deployment['_cloud_init'] = host.cloud_init()
    try:
        connection = cloud.converge(host.pub.read_text().strip(), operation)
        host.rotate_key(connection, operation)
        evidence = {'instance_id': connection['instance_id'], 'desired_hash': deployment['_desired_hash'], 'prepare': prepare}
        step = state.intent(operation, 'rybbit-prepare' if prepare else 'rybbit-converge', evidence)
        kwargs = {'enable_backups': enable_backups and not prepare}
        if stack_env is not None:
            kwargs['stack_env'] = stack_env
        if prepare:
            result = remote(host, connection, config, 'prepare', **kwargs)
        else:
            service.converge(connection, operation)
            result = remote(host, connection, config, 'converge', **kwargs)
        state.complete(step, {'verified': True})
        # Resolve identical interrupted idempotent host operations after successful verification.
        for row in state.db.execute("SELECT id,payload FROM steps WHERE step IN ('rybbit-prepare','rybbit-converge') AND status='pending'").fetchall():
            if json.loads(row['payload']) == evidence:
                state.complete(row['id'], {'verified': True})
        aliases(config, host, connection)
        return {'connection': connection, 'application': result}
    finally:
        deployment.pop('_cloud_init', None)


def upload(host, connection, filename, checksum):
    path = Path(filename).absolute()
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_mode & 0o077):
        raise DeployError('Archive must be a private owned regular file.')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != checksum:
        raise DeployError('Archive checksum differs from the selected recovery point.')
    target = '/var/backups/rybbit/import/' + digest + '.tar.gz'
    source = '''import hashlib,json,os,pathlib,sys,tempfile
os.umask(0o077)
p=pathlib.Path(TARGET)
p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
if p.is_symlink() or any(x.is_symlink() for x in p.parents): raise SystemExit(1)
fd,name=tempfile.mkstemp(dir=p.parent,prefix='.upload-')
try:
 h=hashlib.sha256()
 with os.fdopen(fd,'wb') as out:
  while True:
   chunk=sys.stdin.buffer.read(1048576)
   if not chunk: break
   out.write(chunk);h.update(chunk)
  out.flush();os.fsync(out.fileno())
 if h.hexdigest()!=CHECKSUM: raise SystemExit(1)
 os.replace(name,p)
 print(json.dumps({'ok':True}))
finally:
 if os.path.exists(name): os.unlink(name)
'''.replace('TARGET', repr(target)).replace('CHECKSUM', repr(digest))
    transport = host.prepare_transport(connection)
    with path.open('rb') as stream:
        try:
            response = subprocess.run([*transport.argv, python_command(source)], stdin=stream, capture_output=True, timeout=1800)
        except (OSError, subprocess.TimeoutExpired):
            raise DeployError('Archive transfer failed; output suppressed.') from None
    if response.returncode or response.stdout.strip() != b'{"ok": true}':
        raise DeployError('Archive transfer failed; output suppressed.')
    return target


def restore(config, current, operation, archive, checksum):
    connection = current.cloud.connection()
    step = current.state.intent(operation, 'rybbit-restore', {'sha256': checksum, 'instance_id': connection['instance_id']})
    destination = upload(current.host, connection, archive, checksum)
    result = remote(current.host, connection, config, 'restore', archive=destination, sha256=checksum)
    current.state.complete(step, {'verified': True, 'sha256': checksum})
    return result


def delete(config, deployment, current, operation):
    if deployment['compute-prevent-destroy']:
        raise DeployError('Deletion protection is enabled; change colors.yml explicitly.')
    aliases(config, current.host, remove=True, check_only=True)
    service = dns(config, current.state)
    service.plan_delete()
    actions = current.cloud.plan_delete()
    compute = next((item for item in actions if item.get('resource') == 'compute'), None)
    record = current.state.get_resource('compute')
    if record:
        if not compute or compute.get('id') != record['provider_id']:
            raise DeployError('Compute deletion identity is not verified.')
        evidence = current.state.get_meta('rybbit-delete-fence')
        fenced = evidence == {'instance_id': record['provider_id'], 'fenced': True}
        if compute.get('state') == 'RUNNING':
            connection = current.cloud.connection()
            if connection['instance_id'] != record['provider_id']:
                raise DeployError('Compute fencing identity differs.')
            step = current.state.intent(operation, 'rybbit-delete-fence', {'instance_id': record['provider_id']})
            remote(current.host, connection, config, 'fence')
            current.state.set_meta('rybbit-delete-fence', {'instance_id': record['provider_id'], 'fenced': True})
            current.state.complete(step, {'verified': True})
        else:
            pending = current.state.get_meta(current.cloud.delete_pending_key)
            if not (fenced and isinstance(pending, dict) and pending.get('id') == record['provider_id']):
                raise DeployError('Absent or stopped compute requires its recorded deletion and verified fence evidence.')
    service.delete(operation)
    return delete_compute(deployment, current.state, current.cloud, current.host, operation,
                          before_cleanup=lambda: aliases(config, current.host, remove=True))
