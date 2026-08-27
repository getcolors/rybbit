from conftest import fixture, vultr_fixture
from package_rybbit_blue import validate


def test_fixture_is_valid():
    assert validate.state_errors(fixture()) == []


def test_vultr_fixture_is_valid():
    assert validate.state_errors(vultr_fixture()) == []


def test_compute_provider_must_be_one_the_package_has_a_template_for():
    # The registry is the only list; a provider accepted here with no template
    # directory would fail at render time instead of at validation.
    errors = validate.state_errors(fixture({"provider-compute": "hcloud"}))
    assert any("digitalocean, vultr" in e for e in errors)


def test_each_provider_requires_only_its_own_keys():
    # The DigitalOcean keys are not required of a Vultr deployment, and vice
    # versa -- a flat required list made every deployment carry both.
    assert any("vultr-plan" in e
               for e in validate.state_errors(vultr_fixture({"vultr-plan": None})))
    assert [e for e in validate.state_errors(vultr_fixture())
            if "digitalocean" in e] == []
    assert any("digitalocean-size" in e
               for e in validate.state_errors(fixture({"digitalocean-size": None})))
    assert [e for e in validate.state_errors(fixture()) if "vultr" in e] == []


def test_compute_credentials_follow_the_provider():
    assert validate.tofu_env(fixture(), "provider-compute") == \
        {"do-token": "DIGITALOCEAN_TOKEN"}
    assert validate.tofu_env(vultr_fixture(), "provider-compute") == \
        {"vultr-api-key": "VULTR_API_KEY"}
    # And the credential checked is the credential exported: a Vultr deployment
    # must not be asked for a DigitalOcean token it never uses.
    errors = "\n".join(validate.secret_errors(vultr_fixture()))
    assert "COLORS_PAR_VULTR_API_KEY" in errors
    assert "COLORS_PAR_DO_TOKEN" not in errors


def test_reports_all_errors():
    errors = validate.state_errors(
        fixture({"rybbit-host": "bad", "postgres-image": "floating",
                 "rybbit-backup-retention-days": -1,
                 "provider-dns": "other", "digitalocean-vpc-uuid": "forbidden"}))
    assert len(errors) >= 5
    for part in ["host", "image", "retention", "provider-dns", "vpc-uuid"]:
        assert any(part in e for e in errors)


def test_forbids_vpc_configuration():
    assert any("must be absent" in e for e in validate.state_errors(
        fixture({"digitalocean-vpc-cidr": "10.0.0.0/16"})))


def test_profile_overlay_is_refused():
    assert validate.env_errors({"COLORS_PAR_PROFILE": "other"})
    assert validate.env_errors({}) == []


def test_names_all_package_secrets():
    errors = "\n".join(validate.secret_errors(fixture()))
    for name in ["COLORS_PAR_DO_TOKEN", "COLORS_PAR_CLOUDFLARE_API_TOKEN",
                 "COLORS_PAR_R2_ACCESS_KEY_ID", "COLORS_PAR_R2_SECRET_ACCESS_KEY",
                 "COLORS_PAR_RYBBIT_BACKUP_R2_ACCESS_KEY_ID",
                 "COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY"]:
        assert name in errors


def test_validation_accepts_a_digest_pin():
    assert validate.state_errors(fixture()) == []
    assert validate.state_errors(fixture({"rybbit-backend-image": "no-tag-at-all"}))
