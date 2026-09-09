from conftest import fixture, keygen, keygen_vultr, vultr_fixture
from package_rybbit_blue import validate


def test_fixture_is_valid():
    assert validate.state_errors(fixture()) == []


def test_vultr_fixture_is_valid():
    assert validate.state_errors(vultr_fixture()) == []


def test_keygen_fixtures_are_valid():
    assert validate.state_errors(keygen()) == []
    assert validate.state_errors(keygen_vultr()) == []


# --- the spec handed to ONCE




# --- the compute-provider registry








def test_absent_machine_key_selects_keygen():
    assert validate.keygen(keygen())
    assert validate.keygen(keygen_vultr())
    assert not validate.keygen(fixture())
    assert not validate.keygen(vultr_fixture())
    # Absence, not a flag, is the switch.
    assert validate.keygen(vultr_fixture({"vultr-ssh-keys": None}))






# --- the network contract, wired through state_errors with ONCE's messages






# --- provider checks run only for the selected provider




def test_reports_all_errors():
    errors = validate.state_errors(
        fixture({"rybbit-host": "bad", "postgres-image": "floating",
                 "rybbit-backup-retention-days": -1,
                 "provider-dns": "other", "digitalocean-vpc-uuid": "forbidden"}))
    assert len(errors) >= 4
    for part in ["host", "image", "retention", "provider-dns"]:
        assert any(part in e for e in errors)




def test_profile_overlay_is_refused():
    assert validate.env_errors({"COLORS_PAR_PROFILE": "other"})
    assert validate.env_errors({}) == []




def test_validation_accepts_a_digest_pin():
    assert validate.state_errors(fixture()) == []
    assert validate.state_errors(fixture({"rybbit-backend-image": "no-tag-at-all"}))
