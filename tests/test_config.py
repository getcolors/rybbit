import json
import pytest
from pocketdeploy.common import DeployError
from rybbit.config import load, IMAGES


@pytest.fixture
def desired():
    return {'profile': 'synthetic-hcloud', 'hcloud-location': 'nbg1', 'hcloud-server-type': 'cx23',
            'hcloud-image': 'ubuntu-24.04', 'rybbit-host': 'analytics.example.com',
            'compute-ssh-sources': ['192.0.2.1/32'], 'compute-http-sources': ['0.0.0.0/0'],
            'postgres-data-dir': '/var/lib/rybbit/postgres', 'clickhouse-data-dir': '/var/lib/rybbit/clickhouse',
            'redis-data-dir': '/var/lib/rybbit/redis', 'rybbit-backup-dir': '/var/backups/rybbit',
            'rybbit-backup-r2-bucket': 'synthetic-backup', 'rybbit-backup-r2-endpoint': 'https://r2.example.com',
            **{key: 'example/image:1.0' for key in IMAGES}}


def parse(tmp_path, desired, env=None):
    path = tmp_path / 'colors.yml'
    path.write_text(json.dumps(desired))
    return load(path, env={} if env is None else env)


def test_existing_names_normalize_without_retaining_credentials(tmp_path, desired):
    app, deployment = parse(tmp_path, desired, {'COLORS_PAR_HCLOUD_TOKEN': 'synthetic-token',
        'COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY': 'synthetic-secret'})
    assert deployment['provider-compute'] == 'hcloud'
    assert deployment['compute-ingress'] == [{'protocol': 'udp', 'ports': ['443'], 'sources': ['0.0.0.0/0']}]
    assert 'synthetic-token' not in json.dumps((app, deployment))
    assert 'synthetic-secret' not in json.dumps((app, deployment))
    assert deployment['state-file'] == '.colors.sqlite'


def test_empty_http_also_closes_http3(tmp_path, desired):
    desired['compute-http-sources'] = []
    assert parse(tmp_path, desired)[1]['compute-ingress'] == []


@pytest.mark.parametrize('field', ['compute-api-version', 'provider-backend', 'r2-bucket', 'r2-endpoint', 'unknown'])
def test_retired_fields_rejected(tmp_path, desired, field):
    desired[field] = 'old'
    with pytest.raises(DeployError):
        parse(tmp_path, desired)


def test_profile_override_forbidden_and_destroy_override_ignored(tmp_path, desired):
    with pytest.raises(DeployError):
        parse(tmp_path, desired, {'COLORS_PAR_PROFILE': 'different'})
    assert parse(tmp_path, desired, {'COLORS_PAR_COMPUTE_PREVENT_DESTROY': 'false'})[1]['compute-prevent-destroy'] is True


@pytest.mark.parametrize('field,value', [
    ('postgres-data-dir', '/var'), ('redis-data-dir', '/var/lib/rybbit/postgres/child'),
    ('postgres-data-dir', '/var/lib/../etc'), ('rybbit-backup-r2-endpoint', 'https://user:pass@example.com'),
    ('rybbit-backup-r2-endpoint', 'http://r2.example.com'), ('rybbit-backup-oncalendar', 'daily\nExecStart=bad'),
    ('rybbit-disable-signup', 'true'), ('compute-network-mode', 'private'),
    ('postgres-image', 'image:tag;bad'), ('rybbit-redirect-host', 'analytics.example.com')])
def test_unsafe_config_rejected(tmp_path, desired, field, value):
    desired[field] = value
    with pytest.raises(DeployError):
        parse(tmp_path, desired)
