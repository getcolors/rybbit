"""Trusted root-side Rybbit convergence program, transported over verified SSH."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path('/opt/rybbit')
STAGE = 'validation'
SECRET_KEYS = ('POSTGRES_PASSWORD', 'CLICKHOUSE_PASSWORD', 'REDIS_PASSWORD', 'BETTER_AUTH_SECRET')


def render(config):
    """Render reviewed package templates locally; contains no credentials."""
    directory = Path(__file__).with_name('templates')
    values = dict(config)
    redirect = values.get('rybbit-redirect-host')
    values['rybbit-redirect-block'] = (f'\n{redirect} {{\n    redir https://{values["rybbit-host"]}{{uri}} permanent\n}}\n' if redirect else '')
    rendered = {}
    for name in ('compose.yml', 'Caddyfile'):
        text = (directory / name).read_text()
        def replace(match):
            return str(values[match[1].strip()])
        rendered[name] = re.sub(r'<\{ ([^}]+) \}>', replace, text)
    rendered['backup.py'] = Path(__file__).with_name('backup.py').read_text()
    return rendered


def run(*args, check=True, data=None):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'},
                            cwd=ROOT if ROOT.exists() else '/', timeout=1800)
    if check and result.returncode:
        raise RuntimeError('remote command failed')
    return result


def compose(*args, **kwargs):
    return run('docker', 'compose', *args, **kwargs)


def write(path, text, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError('refusing symlink configuration')
    changed = not path.exists() or path.read_text() != text
    if changed:
        temporary = path.with_name(path.name + '.new')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
        with os.fdopen(fd, 'w') as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    os.chmod(path, mode)
    return changed


def backup_module():
    spec = importlib.util.spec_from_file_location('rybbit_backup', ROOT / 'backup.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_env(text):
    result = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or '\x00' in value:
            raise ValueError('invalid environment file')
        result[key] = value
    return result


def stack_settings(config, existing=None):
    if existing is None:
        env = {'POSTGRES_USER': 'rybbit', 'POSTGRES_DB': 'rybbit',
               'CLICKHOUSE_USER': 'default', 'CLICKHOUSE_DB': 'rybbit',
               **{key: secrets.token_hex(32) for key in SECRET_KEYS}}
    else:
        env = read_env(existing)
        if any(not env.get(key) for key in (*SECRET_KEYS, 'POSTGRES_USER', 'POSTGRES_DB', 'CLICKHOUSE_USER', 'CLICKHOUSE_DB')):
            raise ValueError('retained stack settings are incomplete')
        # Compose env_file interpolation must never reinterpret saved credentials.
        if any('$' in env[key] or '\n' in env[key] or '\r' in env[key] for key in SECRET_KEYS):
            raise ValueError('unsupported credential encoding')
    env.update(DOMAIN_NAME=config['rybbit-host'], BASE_URL='https://' + config['rybbit-host'],
               DISABLE_SIGNUP=str(config['rybbit-disable-signup']).lower(), DISABLE_TELEMETRY='true')
    return ''.join(key + '=' + value + '\n' for key, value in env.items())


USERS_XML = '''<clickhouse><profiles><default>
<enable_json_type>1</enable_json_type><async_insert>1</async_insert><wait_for_async_insert>1</wait_for_async_insert>
<log_queries>0</log_queries><log_query_threads>0</log_query_threads><log_processors_profiles>0</log_processors_profiles>
<max_memory_usage>1000000000</max_memory_usage><max_threads>2</max_threads>
</default></profiles></clickhouse>\n'''
CONFIG_XML = '''<clickhouse><listen_host>0.0.0.0</listen_host>
<max_server_memory_usage_to_ram_ratio>0.50</max_server_memory_usage_to_ram_ratio>
<concurrent_threads_soft_limit_ratio_to_cores>1</concurrent_threads_soft_limit_ratio_to_cores>
<merges_mutations_memory_usage_to_ram_ratio>0.125</merges_mutations_memory_usage_to_ram_ratio>
<backups><allowed_path>/var/lib/clickhouse/backups/</allowed_path></backups>
<logger><level>warning</level><console>true</console></logger>
''' + ''.join(f'<{name} remove="remove"/>\n' for name in (
    'query_thread_log', 'query_log', 'query_views_log', 'query_metric_log', 'error_log',
    'opentelemetry_span_log', 'text_log', 'trace_log', 'metric_log', 'asynchronous_metric_log',
    'session_log', 'part_log', 'latency_log', 'processors_profile_log')) + '</clickhouse>\n'


def stop_backups():
    run('systemctl', 'disable', '--now', 'rybbit-backup.timer', check=False)
    run('systemctl', 'stop', 'rybbit-backup.service', check=False)
    for unit in ('rybbit-backup.timer', 'rybbit-backup.service'):
        result = run('systemctl', 'show', unit,
                     '--property=LoadState,ActiveState,UnitFileState', check=False)
        fields = dict(line.split('=', 1) for line in result.stdout.decode().splitlines() if '=' in line)
        if (result.returncode or set(fields) != {'LoadState', 'ActiveState', 'UnitFileState'}
                or fields['LoadState'] not in ('loaded', 'masked', 'not-found')
                or fields['ActiveState'] not in ('inactive', 'failed')):
            raise RuntimeError('backup unit remains active or could not be verified')
        if (unit.endswith('.timer') and fields['LoadState'] != 'not-found'
                and fields['UnitFileState'] not in ('disabled', 'masked', 'masked-runtime')):
            raise RuntimeError('backup timer remains enabled or could not be verified')


def fence():
    stop_backups()
    compose('stop', 'caddy', 'backend', 'client')


def configure(request):
    global STAGE
    config = request['config']
    STAGE = 'packages'
    if run('dpkg-query', '-W', '-f=${db:Status-Status}\n', 'docker.io', 'docker-compose-v2', 'rclone', check=False).stdout.decode().splitlines() != ['installed'] * 3:
        run('apt-get', 'update', '-qq')
        run('apt-get', 'install', '-y', '-qq', 'docker.io', 'docker-compose-v2', 'rclone', 'ca-certificates', 'curl')
    run('systemctl', 'enable', '--now', 'docker')
    if config.get('compute-name'):
        run('hostnamectl', 'set-hostname', config['compute-name'])
    STAGE = 'configuration'
    # Avoid changing files/credentials beneath a scheduled backup.
    stop_backups()
    for directory, mode in [(str(ROOT), 0o755), (config['postgres-data-dir'], 0o700),
                            (config['clickhouse-data-dir'], 0o755), (config['redis-data-dir'], 0o700),
                            (config['rybbit-backup-dir'], 0o700),
                            ('/var/lib/rybbit/caddy/data', 0o755), ('/var/lib/rybbit/caddy/config', 0o755)]:
        Path(directory).mkdir(parents=True, exist_ok=True, mode=mode)
    retained = ROOT / 'stack.env'
    supplied = request.get('stack_env')
    if supplied is not None and retained.exists():
        current, incoming = read_env(retained.read_text()), read_env(stack_settings(config, supplied))
        immutable = (*SECRET_KEYS, 'POSTGRES_USER', 'POSTGRES_DB', 'CLICKHOUSE_USER', 'CLICKHOUSE_DB')
        if any(current.get(key) != incoming.get(key) for key in immutable):
            raise RuntimeError('cannot replace retained secrets on initialized target')
    if supplied is not None and not retained.exists():
        for key in ('postgres-data-dir', 'clickhouse-data-dir', 'redis-data-dir'):
            if any(Path(config[key]).iterdir()):
                raise RuntimeError('restore secrets require empty database directories')
    existing = retained.read_text() if retained.exists() else supplied
    app_changed = write(retained, stack_settings(config, existing), 0o600)
    files = request['files']
    for name in ('compose.yml', 'Caddyfile', 'backup.py'):
        write(ROOT / name, files[name], 0o600 if name == 'backup.py' else 0o644)
    clickhouse_changed = write('/var/lib/rybbit/clickhouse-users.d/allow-json.xml', USERS_XML)
    clickhouse_changed |= write('/var/lib/rybbit/clickhouse-config.d/rybbit.xml', CONFIG_XML)
    write(ROOT / 'backup-config.json', json.dumps(config), 0o600)
    credentials = request.get('backup_credentials')
    if credentials:
        write('/etc/rybbit-backup.json', json.dumps(credentials), 0o600)
    write('/etc/systemd/system/rybbit-backup.service', '''[Unit]
Description=Rybbit database backups
Requires=docker.service
After=docker.service
[Service]
Type=oneshot
UMask=0077
ExecStart=/usr/bin/python3 /opt/rybbit/backup.py
''')
    write('/etc/systemd/system/rybbit-backup.timer', '[Unit]\nDescription=Daily Rybbit backup\n[Timer]\nOnCalendar=' + config['rybbit-backup-oncalendar'] + '\nPersistent=true\nRandomizedDelaySec=300\n[Install]\nWantedBy=timers.target\n')
    run('systemctl', 'daemon-reload')
    compose('config', '--quiet')
    return app_changed, clickhouse_changed


def mount_stale(service, source, destination):
    current = compose('exec', '-T', service, 'sha256sum', destination, check=False)
    checksum = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    words = current.stdout.decode().split()
    return current.returncode != 0 or not words or words[0] != checksum


def wait_health():
    for _ in range(60):
        if compose('exec', '-T', 'backend', 'wget', '-qO-', 'http://127.0.0.1:3001/api/health', check=False).returncode == 0:
            return
        time.sleep(5)
    raise RuntimeError('backend health check failed')


def readiness(config):
    """Check origin TLS and application health without DNS or tracking writes."""
    host = config['rybbit-host']
    if not isinstance(host, str) or len(host) > 253 or not all(
        re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label)
        for label in host.split('.')
    ):
        raise ValueError('invalid readiness hostname')
    settings = read_env((ROOT / 'stack.env').read_text())
    if settings.get('DOMAIN_NAME') != host or settings.get('BASE_URL') != 'https://' + host:
        raise RuntimeError('configured application hostname does not match')
    # Check running processes, not only files that may have changed since startup.
    for service, keys in (('backend', ('DOMAIN_NAME', 'BASE_URL')),
                          ('client', ('DOMAIN_NAME', 'BASE_URL', 'NEXT_PUBLIC_BACKEND_URL'))):
        expected = {key: host if key == 'DOMAIN_NAME' else 'https://' + host for key in keys}
        script = 'const e=JSON.parse(process.argv[1]);process.exit(Object.entries(e).every(([k,v])=>process.env[k]===v)?0:1)'
        if compose('exec', '-T', service, 'node', '-e', script, json.dumps(expected), check=False).returncode:
            raise RuntimeError('running application hostname does not match')
    # --resolve preserves the real Host and TLS SNI while selecting this origin.
    # Disable curl configuration and proxies; do not follow redirects or weaken
    # certificate verification. Only the numeric HTTP status leaves curl.
    result = run('curl', '-q', '--silent', '--show-error', '--noproxy', '*',
                 '--resolve', host + ':443:127.0.0.1', '--proto', '=https',
                 '--connect-timeout', '10', '--max-time', '30',
                 '--output', '/dev/null', '--write-out', '%{http_code}',
                 '--url', 'https://' + host + '/api/health', check=False)
    if result.returncode != 0 or result.stdout.strip() != b'200':
        raise RuntimeError('origin TLS or health check failed')
    frontend = run('curl', '-q', '--silent', '--show-error', '--noproxy', '*',
                   '--resolve', host + ':443:127.0.0.1', '--proto', '=https',
                   '--connect-timeout', '10', '--max-time', '30',
                   '--output', '/dev/null', '--write-out', '%{http_code}',
                   '--url', 'https://' + host + '/', check=False)
    if frontend.returncode != 0 or frontend.stdout.strip() != b'200':
        raise RuntimeError('origin frontend health check failed')
    return {'ok': True, 'ready': True, 'tls': True, 'health': True}


def counts(config):
    module = backup_module()
    env = module.read_env((ROOT / 'stack.env').read_text())
    pgdb, chdb = module.db_name(env, 'POSTGRES_DB'), module.db_name(env, 'CLICKHOUSE_DB')
    result = {}
    # Only aggregate numbers leave the remote process.
    for table in ('user', 'organization', 'sites'):
        result[table] = int(module.pg(f'SELECT count(*) FROM "{table}"', pgdb))
    result['events'] = int(module.ch(f'SELECT count() FROM {chdb}.events'))
    replay = module.ch(f'SELECT count(), sum(event_count), sum(compressed_size_bytes) FROM {chdb}.session_replay_metadata_v2 FINAL').decode().strip().split('\t')
    if len(replay) != 3:
        raise RuntimeError('invalid replay aggregate result')
    result.update(zip(('replay_sessions', 'replay_events', 'replay_bytes'), map(int, replay)))
    result['replay_rows'] = int(module.ch(f'SELECT count() FROM {chdb}.session_replay_events'))
    return result


def freeze(config):
    """Fence ingress and observe quietness; upstream exposes no queue depth."""
    global STAGE
    STAGE = 'freeze-ingress'
    started = int(time.time())
    stop_backups()
    compose('stop', '--timeout', '45', 'caddy')
    module = backup_module()
    database = module.db_name(module.read_env((ROOT / 'stack.env').read_text()), 'CLICKHOUSE_DB')
    STAGE = 'freeze-quiet'
    previous, stable = None, 0
    for _ in range(60):
        observed = counts(config)
        writers = int(module.ch("SELECT count() FROM system.processes WHERE query ILIKE 'INSERT %' OR query ILIKE 'ALTER %'"))
        mutations = int(module.ch(f"SELECT count() FROM system.mutations WHERE database='{database}' AND is_done=0"))
        stable = stable + 1 if observed == previous and writers == 0 and mutations == 0 else 0
        previous = observed
        if stable >= 12:
            break
        time.sleep(5)
    else:
        raise RuntimeError('database writes did not become quiet')
    STAGE = 'freeze-stop'
    compose('stop', '--timeout', '45', 'backend', 'client')
    ids = compose('ps', '--all', '--quiet', 'backend').stdout.decode().split()
    if not ids or any(not re.fullmatch(r'[a-f0-9]{12,64}', item) for item in ids):
        raise RuntimeError('cannot verify stopped backend')
    for identity in ids:
        # Docker emits ONLY the selected safe state fields; never raw inspection.
        state = run('docker', 'inspect', '--format', '{{.State.Running}} {{.State.ExitCode}} {{.State.OOMKilled}}', identity).stdout.decode().strip()
        if state != 'false 0 false':
            raise RuntimeError('backend did not stop cleanly')
    logs = compose('logs', '--no-color', '--since', str(started), 'backend')
    text = logs.stdout + logs.stderr
    failures = (b'Error processing pageview queue', b'Error processing bot event queue',
                b'Error during shutdown', b'Shutdown timeout exceeded', b'forcing exit')
    if any(message in text for message in failures):
        raise RuntimeError('backend reported a queue or shutdown failure')
    for _ in range(60):
        pending = int(module.ch(f"SELECT count() FROM system.mutations WHERE database='{database}' AND is_done=0"))
        if pending == 0:
            break
        time.sleep(5)
    else:
        raise RuntimeError('shutdown mutations did not complete')
    return {'ok': True, 'fenced': True, 'quiet_seconds': 60,
            'queue_depth_verified': False, 'counts': counts(config)}


def tracking_request(host, site, marker):
    # Exercise the ordinary browser ingestion path, including bot filtering.
    # Missing browser headers otherwise cause a successful HTTP response but
    # route the event to bot_events instead of the analytics events table.
    payload = json.dumps({'type': 'pageview', 'site_id': site, 'pathname': marker,
                          'hostname': 'colors-acceptance.invalid', 'language': 'en-US',
                          'screenWidth': 1920, 'screenHeight': 1080,
                          '_bs': 0, '_bsm': 0}).encode()
    return urllib.request.Request('https://' + host + '/api/track', data=payload,
        headers={'Content-Type': 'application/json',
                 'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0',
                 'Accept': '*/*', 'Accept-Language': 'en-US,en;q=0.9',
                 'Accept-Encoding': 'gzip, deflate, br',
                 'Origin': 'https://colors-acceptance.invalid',
                 'Referer': 'https://colors-acceptance.invalid/',
                 'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty'})


def acceptance(config):
    module = backup_module()
    env = module.read_env((ROOT / 'stack.env').read_text())
    pgdb, chdb = module.db_name(env, 'POSTGRES_DB'), module.db_name(env, 'CLICKHOUSE_DB')
    wait_health()
    if int(module.pg('SELECT count(*) FROM organization', pgdb)) == 0:
        return {'ok': True, 'health': True, 'ingestion': 'not-configured', 'reason': 'no organization'}
    site = module.pg("INSERT INTO sites (name, domain, organization_id) SELECT 'colors-acceptance', 'colors-acceptance.invalid', (SELECT id FROM organization LIMIT 1) WHERE NOT EXISTS (SELECT 1 FROM sites WHERE domain='colors-acceptance.invalid'); SELECT site_id FROM sites WHERE domain='colors-acceptance.invalid' LIMIT 1", pgdb).decode().strip().splitlines()[-1]
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', site):
        raise RuntimeError('invalid acceptance site identifier')
    marker = '/colors-acceptance-' + uuid.uuid4().hex
    request = tracking_request(config['rybbit-host'], site, marker)
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError('tracking request failed')
    for _ in range(30):
        count = int(module.ch(f"SELECT count() FROM {chdb}.events WHERE pathname='{marker}'"))
        if count:
            return {'ok': True, 'health': True, 'ingestion': 'verified', 'marker': marker, 'matched_events': count}
        time.sleep(2)
    raise RuntimeError('unique acceptance event was not found')


def require_completed_reset():
    """Do not initialize or write application data across an interrupted reset."""
    path = ROOT / 'reset.json'
    if not path.exists() and not path.is_symlink():
        return
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError()
        receipt = json.loads(path.read_text())
        if (not isinstance(receipt, dict)
                or set(receipt) != {'reset_id', 'machine_id', 'profile', 'complete', 'containers_removed', 'items'}
                or type(receipt['complete']) is not bool
                or type(receipt['containers_removed']) is not bool
                or receipt['profile'] != 'rybbit-hcloud'
                or not isinstance(receipt['reset_id'], str)
                or str(uuid.UUID(receipt['reset_id'])) != receipt['reset_id']
                or not isinstance(receipt['machine_id'], str)
                or not re.fullmatch(r'[0-9a-f]{32}', receipt['machine_id'])
                or not isinstance(receipt['items'], list) or len(receipt['items']) != 6):
            raise ValueError()
        expected = [Path('/var/lib/rybbit') / name for name in ('postgres', 'clickhouse', 'redis')]
        expected += [ROOT / name for name in ('stack.env', 'application-started', 'restore.json')]
        for item, source in zip(receipt['items'], expected):
            if (not isinstance(item, dict)
                    or set(item) != {'source', 'name', 'present', 'device', 'inode'}
                    or item['source'] != str(source) or item['name'] != source.name
                    or type(item['present']) is not bool):
                raise ValueError()
            if item['present']:
                if any(type(item[key]) is not int or item[key] < 0 for key in ('device', 'inode')):
                    raise ValueError()
            elif item['device'] is not None or item['inode'] is not None:
                raise ValueError()
        if receipt['complete'] is True and receipt['containers_removed'] is not True:
            raise ValueError()
    except (ValueError, TypeError, KeyError, OSError):
        raise RuntimeError('invalid reset receipt blocks application mutation; preserve reset evidence') from None
    if receipt['complete'] is not True:
        raise RuntimeError('incomplete target reset blocks application mutation; resume reset-target with the original ID')


def main(request):
    global STAGE
    action = request['action']
    config = request.get('config', {})
    if action in ('prepare', 'converge', 'restore', 'backup', 'acceptance'):
        require_completed_reset()
    if action == 'status':
        result = compose('ps', '--format', 'json', check=False)
        services = []
        if result.returncode == 0:
            text = result.stdout.decode().strip()
            rows = json.loads(text) if text.startswith('[') else [json.loads(line) for line in text.splitlines() if line]
            services = [{key: row.get(key) for key in ('Service', 'State', 'Health')} for row in rows]
        return {'ok': result.returncode == 0, 'services': services,
                'backup_timer': run('systemctl', 'is-active', '--quiet', 'rybbit-backup.timer', check=False).returncode == 0}
    if action == 'reset-target':
        STAGE = 'reset-target'
        namespace = {'__name__': 'rybbit_reset'}
        exec(compile(request['reset_program'], '<trusted-reset-program>', 'exec'), namespace)
        return namespace['reset_target'](config, request, fence=fence, compose=compose)
    if action == 'readiness':
        STAGE = 'readiness'
        return readiness(config)
    if action == 'freeze':
        return freeze(config)
    if action in ('stop', 'fence'):
        STAGE = 'fence'
        fence()
        return {'ok': True, 'fenced': True}
    if action in ('prepare', 'converge'):
        if action == 'prepare' and ((ROOT / 'restore.json').exists() or (ROOT / 'application-started').exists()):
            raise RuntimeError('prepare requires an unstarted, unrestored target')
        if action == 'converge' and (ROOT / 'restore.json').exists() and not json.loads((ROOT / 'restore.json').read_text()).get('complete'):
            raise RuntimeError('incomplete restoration blocks convergence')
        app_changed, ch_changed = configure(request)
        STAGE = 'services'
        if action == 'prepare':
            fence()
        ch_changed = ch_changed or mount_stale('clickhouse', '/var/lib/rybbit/clickhouse-users.d/allow-json.xml', '/etc/clickhouse-server/users.d/allow-json.xml') or mount_stale('clickhouse', '/var/lib/rybbit/clickhouse-config.d/rybbit.xml', '/etc/clickhouse-server/config.d/rybbit.xml')
        if action == 'prepare':
            if ch_changed:
                compose('up', '-d', '--wait', '--force-recreate', 'clickhouse')
            compose('up', '-d', '--wait', 'postgres', 'clickhouse', 'redis')
            return {'ok': True, 'prepared': True, 'fenced': True}
        if ch_changed:
            compose('up', '-d', '--wait', '--force-recreate', 'clickhouse')
        write(ROOT / 'application-started', 'Application startup authorized.\n', 0o600)
        compose('up', '-d', '--remove-orphans')
        if app_changed:
            compose('up', '-d', '--force-recreate', 'backend', 'client')
        # Single-file bind mounts retain old inodes after atomic replacement.
        if mount_stale('caddy', ROOT / 'Caddyfile', '/etc/caddy/Caddyfile'):
            compose('up', '-d', '--force-recreate', 'caddy')
        wait_health()
        if request.get('enable_backups', True):
            if not Path('/etc/rybbit-backup.json').exists():
                raise RuntimeError('backup credentials are missing')
            run('systemctl', 'enable', '--now', 'rybbit-backup.timer')
        else:
            run('systemctl', 'disable', '--now', 'rybbit-backup.timer')
        return {'ok': True, 'converged': True}
    if action == 'restore':
        STAGE = 'restore'
        fence()
        return backup_module().restore(config, request['archive'], request['sha256'])
    if action == 'backup':
        STAGE = 'backup'
        credentials = request.get('backup_credentials') or (json.loads(Path('/etc/rybbit-backup.json').read_text()) if request.get('upload', True) else {})
        return backup_module().backup(config, credentials, upload=request.get('upload', True))
    if action == 'counts':
        return {'ok': True, 'counts': counts(config)}
    if action == 'acceptance':
        STAGE = 'acceptance'
        return acceptance(config)
    raise ValueError('unsupported action')


if __name__ == '__main__':
    try:
        request = json.load(sys.stdin)
        with open('/run/lock/rybbit-controller.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = main(request)
    except Exception:
        # Raw errors may contain credentials or database contents.
        result = {'ok': False, 'stage': STAGE}
    print(json.dumps(result))
