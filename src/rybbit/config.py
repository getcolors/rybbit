"""Preserve the existing Colors vocabulary; normalize infrastructure internally."""
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from blue.cli import load_yaml, read_pars
from pocketdeploy.config import normalize
from pocketdeploy.common import DeployError

IMAGES = ('postgres-image', 'clickhouse-image', 'redis-image', 'rybbit-backend-image', 'rybbit-client-image', 'caddy-image')
PATHS = ('postgres-data-dir', 'clickhouse-data-dir', 'redis-data-dir', 'rybbit-backup-dir')
SHARED = {'profile', 'workdir', 'state-file', 'provider-compute', 'compute-prevent-destroy',
          'compute-require-existing-state', 'compute-network-mode', 'compute-ssh-sources',
          'compute-http-sources', 'hcloud-server-type', 'hcloud-location', 'hcloud-image',
          'ssh-user', 'ssh-private-key-file', 'ssh-public-key-file', 'ssh-host-private-key-file',
          'ssh-host-public-key-file', 'ssh-known-hosts-file', 'vault-id', 'vault-command', 'vault-state-document-id'}
APP = {'provider-dns', 'cloudflare-zone-id', 'rybbit-host', 'rybbit-redirect-host', 'rybbit-disable-signup',
       'rybbit-backup-r2-bucket', 'rybbit-backup-r2-endpoint', 'rybbit-backup-r2-region',
       'rybbit-backup-oncalendar', 'rybbit-backup-retention-days'} | set(IMAGES) | set(PATHS)
DEFAULTS = {'provider-compute': 'hcloud', 'provider-dns': 'cloudflare', 'workdir': '.colors',
            'compute-network-mode': 'none', 'compute-prevent-destroy': True,
            'compute-require-existing-state': False, 'compute-ssh-sources': [], 'compute-http-sources': [],
            'rybbit-disable-signup': True, 'rybbit-backup-r2-region': 'auto',
            'rybbit-backup-oncalendar': '*-*-* 02:30:00', 'rybbit-backup-retention-days': 7}
KEYS = SHARED | APP
HOST = re.compile(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+')
IMAGE = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._/-]*(?::[a-zA-Z0-9][a-zA-Z0-9._-]*)?(?:@sha256:[0-9a-f]{64})?')


def load(filename, env=None):
    env = dict(os.environ if env is None else env)
    if 'COLORS_PAR_PROFILE' in env:
        raise DeployError('Profile must come from colors.yml; COLORS_PAR_PROFILE is forbidden.')
    path = Path(filename).absolute()
    try:
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise ValueError()
        raw = load_yaml(path.read_text())
        if not isinstance(raw, dict) or set(raw) - KEYS:
            raise ValueError()
    except Exception:
        raise DeployError('Invalid Rybbit configuration; unknown or retired fields are refused.') from None
    # Destruction requires an intentional versioned configuration change.
    env.pop('COLORS_PAR_COMPUTE_PREVENT_DESTROY', None)
    c = {k: v for k, v in read_pars({**DEFAULTS, **raw}, env).items() if k in KEYS}
    if c['provider-compute'] != 'hcloud' or c['provider-dns'] != 'cloudflare':
        raise DeployError('Rybbit supports Hetzner compute and Cloudflare DNS.')
    for key in ('rybbit-host', 'rybbit-redirect-host'):
        value = c.get(key)
        if key == 'rybbit-redirect-host' and value is None:
            continue
        if not isinstance(value, str) or len(value) > 253 or not HOST.fullmatch(value):
            raise DeployError('Invalid Rybbit hostname.')
    if c.get('rybbit-redirect-host') == c['rybbit-host']:
        raise DeployError('Redirect hostname must differ from primary hostname.')
    if type(c['rybbit-disable-signup']) is not bool:
        raise DeployError('Signup setting must be boolean.')
    for key in IMAGES:
        value = c.get(key)
        if not isinstance(value, str) or not IMAGE.fullmatch(value) or (':' not in value):
            raise DeployError('Every image must carry an explicit tag or SHA256 digest.')
    for key in PATHS:
        value = c.get(key)
        if (not isinstance(value, str) or not re.fullmatch(r'/[a-zA-Z0-9_./-]+', value)
                or '..' in Path(value).parts or value in ('/', '/var', '/var/lib', '/var/backups')):
            raise DeployError('Data and backup paths must be safe dedicated absolute directories.')
    paths = [Path(c[k]) for k in PATHS]
    if any(a == b or a in b.parents or b in a.parents for i, a in enumerate(paths) for b in paths[i+1:]):
        raise DeployError('Data and backup directories must not overlap.')
    bucket = c.get('rybbit-backup-r2-bucket', '')
    if not isinstance(bucket, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', bucket):
        raise DeployError('Invalid backup bucket.')
    endpoint = urlsplit(str(c.get('rybbit-backup-r2-endpoint', '')))
    if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password
            or endpoint.query or endpoint.fragment or endpoint.path not in ('', '/')):
        raise DeployError('Backup endpoint must be a credential-free HTTPS origin.')
    if c['rybbit-backup-r2-region'] != 'auto':
        raise DeployError('R2 backup region must be auto.')
    if type(c['rybbit-backup-retention-days']) is not int or c['rybbit-backup-retention-days'] < 1:
        raise DeployError('Backup retention must be a positive integer.')
    calendar = c['rybbit-backup-oncalendar']
    if not isinstance(calendar, str) or not calendar or not re.fullmatch(r'[a-zA-Z0-9*,: ./+-]+', calendar):
        raise DeployError('Invalid backup calendar.')
    d = {k: c[k] for k in SHARED if k in c}
    d['compute-ingress'] = ([{'protocol': 'udp', 'ports': ['443'], 'sources': c['compute-http-sources']}]
                            if c['compute-http-sources'] else [])
    # DNS has its own public interface and resolves the zone independently.
    d = normalize(d, path=path, env={}, desired=c)
    return c, d
