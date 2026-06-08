"""Tests for the ``talaria skin`` CLI (icon / branding management)."""

from __future__ import annotations

import types


def _args(**kw):
    return types.SimpleNamespace(**kw)


def test_skin_new_scaffolds_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("TALARIA_HOME", str(tmp_path))
    from talaria_cli import skin_cli

    skin_cli.cmd_new(_args(name="mytheme", from_skin=None, force=False))

    path = tmp_path / "skins" / "mytheme.yaml"
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "name: mytheme" in body
    assert "brand_emoji" in body
    assert "colors:" in body


def test_skin_new_rejects_bad_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TALARIA_HOME", str(tmp_path))
    from talaria_cli import skin_cli

    skin_cli.cmd_new(_args(name="Bad Name!", from_skin=None, force=False))
    assert not (tmp_path / "skins" / "Bad Name!.yaml").exists()


def test_skin_new_no_overwrite_without_force(tmp_path, monkeypatch):
    monkeypatch.setenv("TALARIA_HOME", str(tmp_path))
    from talaria_cli import skin_cli

    skin_cli.cmd_new(_args(name="dup", from_skin=None, force=False))
    path = tmp_path / "skins" / "dup.yaml"
    path.write_text("name: dup\ndescription: edited\n", encoding="utf-8")

    # Without --force, must not clobber the user's edits.
    skin_cli.cmd_new(_args(name="dup", from_skin=None, force=False))
    assert "edited" in path.read_text(encoding="utf-8")

    # With --force, it is regenerated.
    skin_cli.cmd_new(_args(name="dup", from_skin=None, force=True))
    assert "edited" not in path.read_text(encoding="utf-8")


def test_skin_set_persists_to_config(tmp_path, monkeypatch):
    monkeypatch.setenv("TALARIA_HOME", str(tmp_path))
    from talaria_cli import skin_cli
    from talaria_cli.config import cfg_get, load_config

    skin_cli.cmd_new(_args(name="mine", from_skin=None, force=False))
    skin_cli.cmd_set(_args(name="mine"))

    assert cfg_get(load_config(), "display", "skin") == "mine"


def test_skin_set_rejects_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("TALARIA_HOME", str(tmp_path))
    from talaria_cli import skin_cli
    from talaria_cli.config import cfg_get, load_config

    skin_cli.cmd_set(_args(name="nope"))
    # config.display.skin must NOT be set to an unknown skin
    assert cfg_get(load_config(), "display", "skin") in (None, "", "default")


def test_skin_scaffold_then_loads(tmp_path, monkeypatch):
    """A scaffolded skin round-trips through the engine without error."""
    monkeypatch.setenv("TALARIA_HOME", str(tmp_path))
    from talaria_cli import skin_cli
    from talaria_cli.skin_engine import load_skin

    skin_cli.cmd_new(_args(name="round", from_skin=None, force=False))
    skin = load_skin("round")
    assert skin.name == "round"
    assert skin.get_branding("brand_emoji", "")  # inherited/seeded emoji present
