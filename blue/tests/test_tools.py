import re
from datetime import datetime
from pathlib import Path

from conftest import fixture, vultr_fixture
from package_rybbit_blue import tools

RESOURCES = Path(tools.__file__).parent / "resources"
SOURCE = (Path(tools.__file__)).read_text()


def resource(name: str) -> str:
    return (RESOURCES / name).read_text()


def test_infrastructure_discovers_default_vpc():
    data = tools.infrastructure_data(fixture())
    assert tools.cidrs(data, "digitalocean-http-sources") == ["0.0.0.0/0", "::/0"]


def test_compute_keys_follow_the_selected_provider():
    # Firewall sources are named after the provider, so a step reaching them
    # through a fixed digitalocean- prefix silently renders an empty list on
    # any other provider -- a firewall with no rules rather than an error.
    assert tools.cidrs(tools.infrastructure_data(vultr_fixture()),
                       "vultr-http-sources") == ["0.0.0.0/0", "::/0"]
    assert "0.0.0.0/0" in tools.infrastructure_data(vultr_fixture())["ssh-sources-hcl"]
    assert "0.0.0.0/0" in tools.infrastructure_data(fixture())["http-sources-hcl"]


def test_hostname_is_provider_neutral():
    # The playbook used digitalocean-name, which renders empty on Vultr.
    assert tools.compute_name(fixture()) == "rybbit-fixture"
    assert tools.compute_name(vultr_fixture()) == "rybbit-vultr-fixture"
    # Build and dry-run render without a provider name at all.
    assert tools.compute_name(fixture({"digitalocean-name": None})) == "rybbit-fixture"
    assert "<{ compute-name }>" in resource("tools/ansible/main.yml")


def test_vultr_cidrs_split_into_address_and_prefix():
    # Vultr takes subnet and subnet_size as separate fields, per address family.
    assert tools.cidr_parts("0.0.0.0/0") == \
        {"subnet": "0.0.0.0", "subnet-size": 0, "ip-type": "v4"}
    assert tools.cidr_parts("::/0") == \
        {"subnet": "::", "subnet-size": 0, "ip-type": "v6"}
    assert tools.cidr_parts("203.0.113.4") == \
        {"subnet": "203.0.113.4", "subnet-size": 32, "ip-type": "v4"}
    assert tools.cidr_parts("2001:db8::1") == \
        {"subnet": "2001:db8::1", "subnet-size": 128, "ip-type": "v6"}


def test_vultr_firewall_opens_ssh_http_and_http3():
    json_text = tools.vultr_firewall_json(vultr_fixture())
    # HTTP/3 rides UDP 443. Caddy advertises it through alt-svc whether or not
    # the port is reachable, so leaving it closed degrades every visitor to TCP
    # without erroring anywhere.
    assert '"protocol" : "udp"' in json_text
    assert '"subnet_size" : 0' in json_text
    assert '"subnet" : "::"' in json_text
    # Four services across two address families, and nothing else open.
    assert len(re.findall(r'"firewall_group_id"', json_text)) == 8
    assert set(re.findall(r'"port" : ("\d+")', json_text)) == \
        {'"22"', '"80"', '"443"'}


def test_dns_is_apex_and_proxied():
    json_text = tools.dns_json(tools.dns_data({**fixture(), "ip": "192.0.2.10"}))
    assert "rybbit.example.com" in json_text
    assert "192.0.2.10" in json_text
    # Assert the value, not the key: "proxied" appears in the rendered record
    # either way, so a bare containment check passes on an unproxied record and
    # would not have caught the default being false.
    assert '"proxied" : true' in json_text


def test_dns_proxying_defaults_on_and_can_be_declined():
    assert tools.dns_data(fixture())["cloudflare-proxied"] is True
    assert tools.dns_data(fixture({"cloudflare-proxied": False}))["cloudflare-proxied"] \
        is False
    assert '"proxied" : false' in tools.dns_json(
        tools.dns_data({**fixture(), "ip": "192.0.2.10", "cloudflare-proxied": False}))


def test_inventory_keeps_one_private_target():
    inventory = tools.inventory({**fixture(), "ip": "192.0.2.10"})
    assert "192.0.2.10" in inventory
    assert "rybbit-fixture" in inventory


async def test_delete_cleanup_skips_when_state_has_no_compute(monkeypatch):
    # With the instance already gone the inventory would render 192.0.2.10;
    # there is no host to reach, so the step must not run the playbook and the
    # teardown must continue past it.
    def boom(*_args, **_kwargs):
        raise AssertionError("playbook must not run")
    monkeypatch.setattr(tools, "ansible_with_spec", boom)
    result = await tools.ansible_step({**fixture(), "blue/event": "delete"})
    assert result["blue/exit"] == 0
    assert result["rybbit/cleanup"] == "skipped-no-compute"


async def test_delete_cleanup_targets_the_adopted_address(monkeypatch):
    # When the start step recovered the instance address from state, the
    # cleanup playbook runs against it, never the documentation fallback.
    async def fake(opts, _specs, **_kwargs):
        return {**opts, "blue/exit": 0, "ran-against": opts.get("ip")}
    monkeypatch.setattr(tools, "ansible_with_spec", fake)
    result = await tools.ansible_step(
        {**fixture(), "blue/event": "delete", "ip": "203.0.113.7"})
    assert result["ran-against"] == "203.0.113.7"


def test_ingestion_is_judged_by_the_stored_row_not_the_status():
    assert tools.ingestion_verdict("200", 4, 5) == "ingested"
    # The failure this gate exists for: the endpoint accepts and nothing lands.
    assert tools.ingestion_verdict("200", 4, 4) == "dropped"
    assert tools.ingestion_verdict("202", 4, None) == "dropped"
    assert tools.ingestion_verdict("400", 4, 4) == "rejected"
    assert tools.ingestion_verdict(None, 4, 4) == "unreachable"


def test_backup_must_be_fresh_and_non_empty():
    since = datetime.fromisoformat("2026-08-17T02:30:00+00:00")
    entry = lambda size, mod_time: {"Size": size, "ModTime": mod_time}  # noqa: E731
    assert tools.fresh_backup([entry(1024, "2026-08-17T02:30:05Z")], since)
    assert tools.fresh_backup([entry(1024, "2026-08-17T04:30:05+02:00")], since)
    assert not tools.fresh_backup([entry(1024, "2026-08-16T02:30:05Z")], since)
    assert not tools.fresh_backup([entry(0, "2026-08-17T02:30:05Z")], since)
    assert not tools.fresh_backup([], since)
    assert not tools.fresh_backup(None, since)


def test_clickhouse_backup_is_native_and_has_no_torn_fallback():
    # A hot tar of the data directory races running merges: parts vanish
    # mid-read, tar exits non-zero and set -e aborts before the upload, which is
    # how this deployment ran for hours with nothing reaching R2.
    backup = resource("tools/ansible/backup")
    assert "BACKUP DATABASE" in backup
    assert "/var/lib/clickhouse/backups/" in backup
    assert not re.search(r"\|\|\s*\{?\s*\n?\s*tar -czf", backup)


def test_backup_proves_it_restores_and_prunes_the_bucket():
    backup = resource("tools/ansible/backup")
    assert "CREATE DATABASE" in backup
    assert "information_schema.tables" in backup
    assert "rclone delete --min-age" in backup
    restore = backup.index("restore check restored no tables")
    upload = backup.index("rclone copyto")
    assert restore < upload


def test_signup_is_desired_state():
    # Open registration on a public analytics instance should be a decision in
    # colors.yml, not a constant in the playbook.
    playbook = resource("tools/ansible/main.yml")
    assert "DISABLE_SIGNUP=false" not in playbook
    assert "DISABLE_SIGNUP=<{ rybbit-disable-signup }>" in playbook


def test_images_are_pinned_not_floating():
    # A moving tag is how the PostHog arm ended up running an application and a
    # plugin server built from different commits, querying a column that did not
    # exist. Digests cannot move under a deployment.
    text = (Path(__file__).resolve().parents[2]
            / "test" / "fixtures" / "colors.yml").read_text()
    assert not re.search(r"image:\s*\S+:(latest|master)\s*$", text, re.M)
    assert re.search(r"rybbit-backend-image: \S+@sha256:[0-9a-f]{64}", text)
    assert re.search(r"rybbit-client-image: \S+@sha256:[0-9a-f]{64}", text)


def test_track_payload_uses_the_api_discriminator():
    # Rybbit validates a `type` discriminator and rejects the request outright
    # without it; `name` produced a 400 that only surfaced once a site existed.
    assert '"type": "pageview"' in SOURCE
    assert "pathname" in SOURCE
    assert '"name": "pageview"' not in SOURCE


def test_acceptance_uses_a_throwaway_site():
    # Sending to whichever site was first wrote a synthetic pageview into the
    # operator's real analytics on every converge.
    assert "acceptance_site_id" in SOURCE
    assert "rybbit-acceptance-site-domain" in SOURCE
    # No falling back to an arbitrary existing site.
    assert "select site_id from sites limit 1" not in SOURCE
    # Created only when absent, so a converge is idempotent.
    assert "where not exists" in SOURCE


def test_site_id_is_taken_from_the_last_line():
    # psql prints "INSERT 0 1" before the selected id; the whole output is not
    # a site id.
    assert "splitlines()" in SOURCE
    assert 're.fullmatch(r"\\d+", last)' in SOURCE


def test_a_missing_compute_output_fails_loudly():
    # The documentation address belongs to build and dry-run. Merging it into a
    # real converge would point Ansible at TEST-NET instead of failing.
    assert tools.resolved_compute({}, {"ip": "192.0.2.10"}, {"ip": "1.2.3.4"})["ip"] \
        == "1.2.3.4"
    assert tools.resolved_compute({}, {"ip": "192.0.2.10"}, None)["blue/exit"] == 1
    assert tools.resolved_compute({}, {"ip": "192.0.2.10"}, {})["blue/exit"] == 1
    assert tools.resolved_compute(
        {}, {"ip": "192.0.2.10"}, {"ip": "5.6.7.8"}).get("blue/exit") is None


def test_signup_policy_is_reapplied_on_every_converge():
    # stack.env is written once to keep its generated secrets, which also froze
    # the signup policy: changing the key afterwards silently did nothing.
    playbook = resource("tools/ansible/main.yml")
    assert "lineinfile" in playbook
    assert "DISABLE_SIGNUP=<{ rybbit-disable-signup }>" in playbook
    # An env_file is read when a container is created, not while it runs.
    assert "--force-recreate backend client" in playbook


def test_caddy_access_logging_is_on_and_bounded():
    # Access logging is off by default in Caddy, so a successful request left no
    # trace and ingestion had no request-level evidence to debug from.
    caddyfile = resource("tools/ansible/Caddyfile")
    assert "log {" in caddyfile
    assert "output stdout" in caddyfile
    # On, but bounded: json-file never rotates on its own and this endpoint
    # writes a line per request.
    compose = resource("tools/ansible/compose.yml")
    assert "max-size" in compose
    assert "max-file" in compose


def test_caddy_reload_is_convergent_not_change_triggered():
    # The Caddyfile is a single-file bind mount, so copy-by-rename leaves the
    # container on the old inode and `up -d` will not recreate an unchanged
    # service: the host file looked right while Caddy served the old config.
    playbook = resource("tools/ansible/main.yml")
    assert "--force-recreate caddy" in playbook
    assert "sha256sum /etc/caddy/Caddyfile" in playbook
    # And it must run once the stack is up, or it recreates against a compose
    # file that has not been rendered yet.
    converge = playbook.index("Start Rybbit stack")
    reload = playbook.index("--force-recreate caddy")
    health = playbook.index("Wait for backend health endpoint")
    assert converge < reload < health


def test_access_log_records_the_visitor_not_the_proxy():
    # Behind the Cloudflare proxy every connection arrives from an edge address,
    # so without trusted_proxies Caddy attributes each request to Cloudflare and
    # the access log answers "who sent this?" with the proxy.
    caddyfile = resource("tools/ansible/Caddyfile")
    assert "trusted_proxies static" in caddyfile
    assert "162.158.0.0/15" in caddyfile
    assert "2400:cb00::/32" in caddyfile
