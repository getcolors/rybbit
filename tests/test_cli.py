import json
from unittest.mock import Mock
import pytest
from pocketdeploy.common import DeployError
from rybbit import cli


@pytest.mark.parametrize('args', [
    ['restore'], ['restore', '--archive', 'snapshot', '--sha256', 'invalid'],
    ['status', '--stack-env-file', 'secret'], ['status', '--archive', 'snapshot'],
    ['status', '--no-backups'], ['ssh', '--json'], ['status', '--dry-run'],
    ['status', '--ssh-command', 'true'], ['plan', '--quiet', '--verbose'],
    ['converge', '--destination', 'restore-dir']])
def test_invalid_options_fail_before_config_or_provider(monkeypatch, args):
    load = Mock(side_effect=AssertionError('must validate options first'))
    monkeypatch.setattr(cli, 'load', load)
    with pytest.raises(DeployError):
        cli.execute(cli.parser().parse_args(args))
    load.assert_not_called()


def test_no_argument_help_is_offline(monkeypatch, capsys):
    monkeypatch.setattr(cli, 'execute', Mock(side_effect=AssertionError('offline help')))
    assert cli.main([]) == 0
    assert 'Rybbit on Hetzner' in capsys.readouterr().out


def test_json_error_suppresses_exception_content(monkeypatch, capsys):
    monkeypatch.setattr(cli, 'execute', Mock(side_effect=RuntimeError('synthetic-secret')))
    assert cli.main(['status', '--json']) == 1
    output = capsys.readouterr()
    assert 'synthetic-secret' not in output.out + output.err
    assert json.loads(output.out)['ok'] is False


def test_validate_does_not_open_state(monkeypatch):
    monkeypatch.setattr(cli, 'load', Mock(return_value=({'profile': 'synthetic'}, {'_root': '/tmp'})))
    session = Mock(side_effect=AssertionError('validation is offline'))
    monkeypatch.setattr(cli, 'session', session)
    assert cli.execute(cli.parser().parse_args(['validate'])) == {'valid': True, 'profile': 'synthetic'}
    session.assert_not_called()


def test_json_usage_error_has_nonzero_status(capsys):
    assert cli.main(['unknown', '--json']) == 2
    assert json.loads(capsys.readouterr().out)['ok'] is False


@pytest.mark.parametrize('args', [
    ['dns-adopt'], ['dns-adopt', '--evidence-file', 'evidence'],
    ['dns-adopt', '--previous-dns-manager-disabled'],
    ['status', '--evidence-file', 'evidence'], ['status', '--previous-dns-manager-disabled'],
    ['dns-retire'], ['dns-retire', '--hostname', 'old.example.com'],
    ['status', '--hostname', 'old.example.com'], ['status', '--record-id', 'record']])
def test_dns_arguments_checked_before_configuration(monkeypatch, args):
    load = Mock(side_effect=AssertionError('offline argument check'))
    monkeypatch.setattr(cli, 'load', load)
    with pytest.raises(DeployError):
        cli.execute(cli.parser().parse_args(args))
    load.assert_not_called()


@pytest.mark.parametrize('args', [['reset-target'], ['status', '--reset-id', 'id']])
def test_reset_requires_all_explicit_identities_before_configuration(monkeypatch, args):
    monkeypatch.setattr(cli, 'load', Mock(side_effect=AssertionError('offline validation')))
    with pytest.raises(DeployError):
        cli.execute(cli.parser().parse_args(args))
    cli.load.assert_not_called()
