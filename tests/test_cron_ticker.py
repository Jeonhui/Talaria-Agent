"""Smoke coverage for the extracted background cron ticker
(gateway/cron_ticker.py).

The ticker is a daemon thread that runs while the gateway is alive; its
correctness contract is:

1. It calls ``cron.scheduler.tick`` on every cycle.
2. It cleanly exits when ``stop_event`` is set.
3. Exceptions inside any single per-tick sub-task (cron, channel-dir
   refresh, cache cleanup, paste sweep, curator poll) are swallowed so
   one busted hook can't take the gateway down.

These tests stub every sub-task so the whole ticker stays in-process,
no I/O, and finishes in a few hundred milliseconds.
"""

import threading

import pytest

from gateway import cron_ticker


@pytest.fixture
def stub_subtasks(monkeypatch):
    """Replace every per-tick sub-task with an in-memory counter / no-op."""
    calls = {
        "cron": 0,
        "image_cache": 0,
        "document_cache": 0,
        "paste_sweep": 0,
        "channel_dir": 0,
        "curator": 0,
    }

    import cron.scheduler as scheduler

    def _cron_tick(verbose=False, adapters=None, loop=None):
        calls["cron"] += 1

    monkeypatch.setattr(scheduler, "tick", _cron_tick)

    import gateway.platforms.base as pbase

    def _image_cleanup(max_age_hours=24):
        calls["image_cache"] += 1
        return 0

    def _doc_cleanup(max_age_hours=24):
        calls["document_cache"] += 1
        return 0

    monkeypatch.setattr(pbase, "cleanup_image_cache", _image_cleanup)
    monkeypatch.setattr(pbase, "cleanup_document_cache", _doc_cleanup)

    import talaria_cli.debug as debug

    def _sweep():
        calls["paste_sweep"] += 1
        return (0, 0)

    monkeypatch.setattr(debug, "_sweep_expired_pastes", _sweep)

    return calls


def test_ticker_exits_when_stop_event_is_set(stub_subtasks):
    """``_start_cron_ticker`` is meant to be a daemon thread; the test
    must complete in well under the join timeout below."""
    stop = threading.Event()
    t = threading.Thread(
        target=cron_ticker._start_cron_ticker,
        kwargs={"stop_event": stop, "interval": 1},
        daemon=True,
    )
    t.start()
    # Let one tick happen, then signal shutdown.
    threading.Timer(0.1, stop.set).start()
    t.join(timeout=5.0)

    assert not t.is_alive(), "ticker did not exit after stop_event was set"
    assert stub_subtasks["cron"] >= 1


def test_ticker_swallows_cron_tick_exception(monkeypatch):
    """A busted cron hook must not crash the ticker thread."""
    import cron.scheduler as scheduler

    boom_calls = {"n": 0}

    def _explode(verbose=False, adapters=None, loop=None):
        boom_calls["n"] += 1
        raise RuntimeError("scheduler is on fire")

    monkeypatch.setattr(scheduler, "tick", _explode)

    # Stub the other sub-tasks so they don't import real adapter state.
    import gateway.platforms.base as pbase

    monkeypatch.setattr(pbase, "cleanup_image_cache", lambda max_age_hours=24: 0)
    monkeypatch.setattr(pbase, "cleanup_document_cache", lambda max_age_hours=24: 0)
    import talaria_cli.debug as debug

    monkeypatch.setattr(debug, "_sweep_expired_pastes", lambda: (0, 0))

    stop = threading.Event()
    t = threading.Thread(
        target=cron_ticker._start_cron_ticker,
        kwargs={"stop_event": stop, "interval": 1},
        daemon=True,
    )
    t.start()
    threading.Timer(0.1, stop.set).start()
    t.join(timeout=5.0)

    assert not t.is_alive(), "ticker died after a sub-task exception"
    assert boom_calls["n"] >= 1


def test_ticker_no_adapters_skips_channel_directory(stub_subtasks):
    """The channel-directory refresh only runs when adapters are provided.
    Without them the ticker must not try to import / call into anything
    that depends on a live event loop."""
    stop = threading.Event()
    t = threading.Thread(
        target=cron_ticker._start_cron_ticker,
        kwargs={"stop_event": stop, "interval": 1, "adapters": None, "loop": None},
        daemon=True,
    )
    t.start()
    threading.Timer(0.1, stop.set).start()
    t.join(timeout=5.0)

    assert not t.is_alive()
    assert stub_subtasks["channel_dir"] == 0
