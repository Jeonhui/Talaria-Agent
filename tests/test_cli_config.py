"""Shared config loader (cli_config) extracted from the removed cli.py REPL."""

import stat

import cli_config


def test_load_returns_defaults():
    cfg = cli_config.load_cli_config()
    assert isinstance(cfg, dict)
    assert "model" in cfg
    assert "agent" in cfg
    assert cfg["agent"]["max_turns"]  # default is present and truthy


def test_save_config_value_roundtrip_and_perms(tmp_path, monkeypatch):
    # Redirect the config home at tmp; pre-create config.yaml so the loader
    # picks the user path (not the repo-level project fallback).
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("model: ''\n")
    monkeypatch.setattr(cli_config, "_talaria_home", tmp_path)

    assert cli_config.save_config_value("agent.system_prompt", "hello-test") is True

    import yaml

    written = yaml.safe_load(cfg_file.read_text())
    assert written["agent"]["system_prompt"] == "hello-test"

    # Credential-bearing config must be owner-only (0600).
    mode = stat.S_IMODE(cfg_file.stat().st_mode)
    assert mode == 0o600
