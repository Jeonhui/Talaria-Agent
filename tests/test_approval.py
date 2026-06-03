"""Command-approval guards (tools/approval).

Covers the unconditional hardline blocklist and the Docker bind-mount fix:
an isolated container auto-approves, but once host directories are bind-mounted
the dangerous-command checks must run again.
"""

from tools import approval


def test_hardline_blocks_rm_rf_root_local():
    res = approval.check_all_command_guards("rm -rf /", "local")
    assert res.get("approved") is False


def test_docker_host_mount_detection(monkeypatch):
    monkeypatch.delenv("TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE", raising=False)
    monkeypatch.delenv("TERMINAL_DOCKER_VOLUMES", raising=False)
    assert approval._docker_has_host_mounts() is False

    monkeypatch.setenv("TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE", "true")
    assert approval._docker_has_host_mounts() is True


def test_isolated_docker_auto_approves():
    res = approval.check_all_command_guards("echo hi", "docker")
    assert res.get("approved") is True


def test_bindmounted_docker_still_blocks_hardline(monkeypatch):
    monkeypatch.setenv("TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE", "true")
    res = approval.check_all_command_guards("rm -rf /", "docker")
    # Host dir is mounted, so the docker fast-path must NOT auto-approve.
    assert res.get("approved") is False
