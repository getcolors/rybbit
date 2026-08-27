from conftest import fixture
from package_rybbit_blue import workflow


async def test_build_and_dry_run_need_no_credentials():
    result = await workflow.start_step({**fixture(), "blue/event": "build"}, env={})
    assert result["blue/exit"] == 0
    result = await workflow.start_step(
        {**fixture(), "blue/event": "create", "blue/dry-run": True}, env={})
    assert result["blue/exit"] == 0


async def test_real_create_requires_credentials():
    result = await workflow.start_step({**fixture(), "blue/event": "create"}, env={})
    assert result["blue/exit"] == 2
    assert "COLORS_PAR_DO_TOKEN" in result["blue/err"]
    assert "COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY" in result["blue/err"]


async def test_delete_is_protected():
    result = await workflow.start_step({**fixture(), "blue/event": "delete"}, env={})
    assert result["blue/exit"] == 2
    assert "COMPUTE_PREVENT_DESTROY" in result["blue/err"]


def deletable_fixture(overrides: dict | None = None) -> dict:
    """A fixture that passes real-delete preflight: guard lifted, secrets
    present."""
    return fixture({"compute-prevent-destroy": False,
                    "do-token": "t", "cloudflare-api-token": "t",
                    "r2-access-key-id": "k", "r2-secret-access-key": "s",
                    "rybbit-backup-r2-access-key-id": "k",
                    "rybbit-backup-r2-secret-access-key": "s",
                    **(overrides or {})})


async def test_delete_fails_loudly_when_state_is_unreadable(monkeypatch):
    # Swallowing a failed state read is how a live teardown ended up pointing
    # the cleanup playbook at 192.0.2.10: stale backend credentials made
    # `tofu output` fail, nil was merged, and the inventory fell back to
    # TEST-NET. The failure must surface here, before any playbook runs.
    async def unauthorized(_opts):
        raise RuntimeError("Unauthorized")
    monkeypatch.setattr(workflow, "state_output", unauthorized)
    result = await workflow.start_step(
        {**deletable_fixture(), "blue/event": "delete"}, env={})
    assert result["blue/exit"] == 1
    assert "Unauthorized" in result["blue/err"]
    assert "COLORS_PAR_IP" in result["blue/err"]


async def test_delete_with_explicit_ip_skips_the_state_read(monkeypatch):
    # COLORS_PAR_IP is the operator's escape hatch when the state backend is
    # unreachable; it must not require the read it exists to replace.
    async def must_not_be_called(_opts):
        raise RuntimeError("must not be called")
    monkeypatch.setattr(workflow, "state_output", must_not_be_called)
    result = await workflow.start_step(
        {**deletable_fixture(), "blue/event": "delete", "ip": "203.0.113.7"}, env={})
    assert result["blue/exit"] == 0
    assert result["ip"] == "203.0.113.7"


async def test_delete_with_empty_state_proceeds_without_an_address(monkeypatch):
    # State readable, no compute recorded: the instance is already gone, the
    # cleanup step skips itself, and the rest of the teardown still runs.
    async def empty(_opts):
        return None
    monkeypatch.setattr(workflow, "state_output", empty)
    result = await workflow.start_step(
        {**deletable_fixture(), "blue/event": "delete"}, env={})
    assert result["blue/exit"] == 0
    assert result.get("ip") is None


def test_graph_orders_private_stack():
    assert workflow.wire_fn("rybbit/start", {"blue/event": "create"})[1:] == \
        ("rybbit/infrastructure",)
    assert workflow.wire_fn("rybbit/infrastructure", {"blue/event": "create"})[1:] == \
        ("rybbit/dns",)
    assert workflow.wire_fn("rybbit/start", {"blue/event": "delete"})[1:] == \
        ("rybbit/ansible",)
