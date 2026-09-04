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


def test_the_spec_carries_this_packages_registry_sources_and_default():
    # The operations are ONCE's; this is the data they run over. A colour
    # whose registry, sources or default drifts fails here, in that colour.
    assert set(validate.spec["registry"]) == {"digitalocean", "vultr"}
    assert validate.spec["registry"] is validate.compute_providers
    assert validate.spec["registry"]["digitalocean"] == {
        "required": ["digitalocean-region", "digitalocean-size", "digitalocean-image",
                     "digitalocean-ssh-sources", "digitalocean-http-sources"],
        "secrets": ["do-token"],
        "tofu-env": {"do-token": "DIGITALOCEAN_TOKEN"},
    }
    assert validate.spec["registry"]["vultr"] == {
        "required": ["vultr-region", "vultr-plan", "vultr-os-id",
                     "vultr-ssh-sources", "vultr-http-sources"],
        "secrets": ["vultr-api-key"],
        "tofu-env": {"vultr-api-key": "VULTR_API_KEY"},
    }
    assert validate.spec["sources"] == {"non_empty": ["ssh-sources"],
                                        "may_be_empty": ["http-sources"]}
    # Vultr, not DigitalOcean: the default is what a legacy state without
    # params.provider is, and the only legacy state is the live Vultr deployment.
    assert validate.spec["default"] == "vultr"
    assert validate.spec["default"] == validate.default_compute_provider
    assert "name_rules" not in validate.spec, "the name rules are ONCE's"


# --- the compute-provider registry


def test_compute_provider_must_be_one_the_package_has_a_template_for():
    # The registry is the only list; a provider accepted here with no template
    # directory would fail at render time instead of at validation.
    errors = validate.state_errors(fixture({"provider-compute": "hcloud"}))
    assert ":provider-compute must be one of digitalocean, vultr" in errors


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


def test_name_and_machine_key_are_never_required():
    # `<provider>-name` is an optional override of the profile and
    # `<provider>-ssh-keys` is meaningful by its absence, so neither may be in
    # the registry's required list -- a required machine key would make keygen
    # mode unreachable.
    for entry in validate.compute_providers.values():
        for key in entry["required"]:
            assert not key.endswith("-name"), key
            assert not key.endswith("-ssh-keys"), key
    assert validate.state_errors(
        fixture({"digitalocean-name": None, "digitalocean-ssh-keys": None})) == []
    assert validate.state_errors(
        vultr_fixture({"vultr-name": None, "vultr-ssh-keys": None})) == []


def test_absent_machine_key_selects_keygen():
    assert validate.keygen(keygen())
    assert validate.keygen(keygen_vultr())
    assert not validate.keygen(fixture())
    assert not validate.keygen(vultr_fixture())
    # Absence, not a flag, is the switch.
    assert validate.keygen(vultr_fixture({"vultr-ssh-keys": None}))


def test_compute_name_falls_back_to_the_profile():
    assert validate.compute_name(fixture()) == "rybbit-fixture"
    assert validate.compute_name(keygen()) == "rybbit-keygen-fixture"
    assert validate.compute_name(vultr_fixture({"vultr-name": "custom"})) == "custom"
    assert validate.compute_key(vultr_fixture(), "ssh-sources") == "vultr-ssh-sources"


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


# --- the network contract, wired through state_errors with ONCE's messages


def test_ssh_sources_must_not_be_empty():
    # A machine nobody can reach is not a deployment; an empty HTTP list is
    # simply no public HTTP.
    assert ":vultr-ssh-sources must list at least one CIDR" in \
        validate.state_errors(vultr_fixture({"vultr-ssh-sources": []}))
    assert ":digitalocean-ssh-sources must list at least one CIDR" in \
        validate.state_errors(fixture({"digitalocean-ssh-sources": []}))
    assert validate.state_errors(vultr_fixture({"vultr-http-sources": []})) == []
    assert validate.state_errors(fixture({"digitalocean-http-sources": []})) == []


def test_malformed_sources_are_refused_before_any_provider_call():
    assert ':vultr-http-sources entry "203.0.113.0" is not an IPv4 or IPv6 CIDR' in \
        validate.state_errors(vultr_fixture({"vultr-http-sources": ["203.0.113.0"]}))
    assert ':digitalocean-ssh-sources entry "nope" is not an IPv4 or IPv6 CIDR' in \
        validate.state_errors(fixture({"digitalocean-ssh-sources": ["0.0.0.0/0", "nope"]}))
    assert validate.state_errors(
        fixture({"digitalocean-ssh-sources": ["2001:db8::/32", "203.0.113.4/32"]})) == []


# --- provider checks run only for the selected provider


def test_provider_checks_are_scoped_to_the_selected_provider():
    # DigitalOcean's VPC keys are refused on DigitalOcean and ignored on
    # Vultr, like every other unselected provider's key; Vultr's os-id is
    # numeric, and only checked on Vultr.
    assert any("vpc-uuid" in e for e in
               validate.state_errors(fixture({"digitalocean-vpc-uuid": "forbidden"})))
    assert validate.state_errors(vultr_fixture({"digitalocean-vpc-uuid": "ignored",
                                                "digitalocean-vpc-cidr": "10.0.0.0/16"})) == []
    assert ":vultr-os-id must be Vultr's numeric operating-system id" in \
        validate.state_errors(vultr_fixture({"vultr-os-id": "ubuntu"}))
    assert validate.state_errors(fixture({"vultr-os-id": "ubuntu"})) == []


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
