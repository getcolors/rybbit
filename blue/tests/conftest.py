from pathlib import Path

from blue.cli import load_yaml

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, overrides: dict | None = None) -> dict:
    text = (ROOT / "test" / "fixtures" / name).read_text().replace("WORKDIR", ".colors")
    return {**load_yaml(text), **(overrides or {})}


def fixture(overrides: dict | None = None) -> dict:
    """DigitalOcean, opt-out mode: an explicit key id and a name equal to the
    profile."""
    return _load("colors.yml", overrides)


def vultr_fixture(overrides: dict | None = None) -> dict:
    """Vultr, opt-out mode -- the shape of the live rybbit-vultr deployment."""
    return _load("colors-vultr.yml", overrides)


def keygen(overrides: dict | None = None) -> dict:
    """DigitalOcean, keygen mode: no `digitalocean-ssh-keys`, no
    `digitalocean-name`."""
    return _load("keygen.yml", overrides)


def keygen_vultr(overrides: dict | None = None) -> dict:
    """Vultr, keygen mode: no `vultr-ssh-keys`, no `vultr-name`."""
    return _load("keygen-vultr.yml", overrides)
