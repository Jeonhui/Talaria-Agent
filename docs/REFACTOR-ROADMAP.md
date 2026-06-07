# Monolith Refactor Roadmap

Three files in this repo are over 7,000 lines and concentrate responsibilities that should live in smaller, single-purpose modules. This document captures the plan to split them incrementally without breaking the test suite or the import API that gateway/CLI/Plugin consumers depend on.

The work is intentionally **not** a single PR — each phase below is an independent unit of work, sized so it can be reviewed, reverted, and merged without entangling the rest of the file.

## Progress

Current state of the three monoliths (line numbers approximate, refreshed per merged PR):

| File | Original | Now | Reduction | Remaining work |
|---|---|---|---|---|
| `run_agent.py` | 13,237 | **13,106** | −131 | C3–C6 — gated on integration test |
| `gateway/run.py` | 11,914 | **11,650** | −264 | A2 (slash commands), A3 (pipeline), start_gateway split |
| `talaria_cli/main.py` | 7,498 | **3,143** | −4,355 | B5 residual polish |

Phases shipped: **A1, A4 (partial), B1, B2, B3, C1, C2.** Phase B4 was triaged out (each `cmd_*` is already a 1–5 line wrapper; packaging adds boilerplate without saving meaningful LOC). The remaining deep phases (A2, A3, start_gateway, C3–C6) are explicitly blocked on the integration tests called out in the "Test gaps" section near the end of this document.

## Guiding rules

1. **Behavior preservation > API preservation > code aesthetics.** Public callers (gateway, plugins, tests) must keep working. Module-level wrappers are fine if they make the refactor mergeable.
2. **One extraction per PR.** Mixing platform cleanup and structural splits in the same commit makes regressions hard to bisect (the PR #4 cleanup was an exception only because the platform code was already dead).
3. **Tests are the contract.** `pytest -o addopts=""` and the import smoke at `tests/test_imports_smoke.py` must stay green at every step.
4. **No new public API without a use case.** When extracting helpers, keep them `_private` until a second caller appears.
5. **Plugin platform `_missing_()` mechanism must keep working** — many extractions touch `Platform.<X>` references, but plugin platforms remain dynamic.

## Phase A — `gateway/run.py` (~11.7K lines)

The runtime is the highest-leverage split because each extraction targets a self-contained policy.

### A1. ✅ `gateway/auth.py` — *done*
Extracted `_is_user_authorized` and `_get_unauthorized_dm_behavior` from `GatewayRunner` as pure module-level functions. Methods on the class are now thin wrappers. ~180 lines moved out.

### A2. `gateway/slash_commands.py`
**Target:** the `/update`, `/deny`, `/cancel`, `/debug`, `/new`, `/reset`, `/status` handlers currently living as `_handle_*_command` methods on `GatewayRunner`.

**Approach:** module-level handler registry `{command_name → handler_fn(runner, event, args)}`. Runner stays the dispatch surface (so it can still hold per-command rate-limit state). Each handler becomes a function in this module — they're easier to read in isolation and unit-test.

**Why:** ~2K lines worth of grep-resistant `/cmd` dispatch logic that is otherwise interleaved with message routing.

**Watch out for:** `_UPDATE_ALLOWED_PLATFORMS` set lives near the slash dispatch; extract together.

### A3. `gateway/message_pipeline.py`
**Target:** `_handle_message` (the message processing pipeline) and its supporting helpers.

**Approach:** runner still owns adapters and sessions, but the pipeline becomes a function that takes `runner` + `event` and returns the dispatched response. Each pipeline step (authorization → command check → interrupt → session resolve → agent call) becomes a single named function.

**Risk:** medium-high. This is the hottest path in the gateway; any behavior drift surfaces in production.

**Pre-work:** add at least one integration test that drives `_handle_message` end-to-end before extracting.

### A4. `gateway/lifecycle.py` — *partial*
**Status:** ✅ `_start_cron_ticker` extracted to `gateway/cron_ticker.py`. `start_gateway` and signal-handling still pending — both wrap GatewayRunner state and are part of the gateway hot path, so they share A3's integration-test prerequisite.

**Approach:** classic facade pattern — `lifecycle.start(runner)` / `lifecycle.stop(runner)` wrap `GatewayRunner.run`. Easy to test in isolation.

### A5. Residual `gateway/run.py`
Should end up at ~1–2K lines: the `GatewayRunner` class proper, plus its config/runtime-resolution helpers (`_resolve_runtime_agent_kwargs`, `_try_resolve_fallback_provider`, `_resolve_gateway_model`).

---

## Phase B — `talaria_cli/main.py` (~7.5K lines)

The CLI surface area has 23 `cmd_*` handlers but most of the bulk is in **provider/auth flow scaffolding** — not the handlers themselves.

### B1. ✅ `talaria_cli/provider_flows.py` — *done*
`select_provider_and_model` + the `_aux_*` / `_model_flow_*` / `_save_custom_provider` / `_run_anthropic_oauth_flow` family extracted. ~1.9K lines moved out. `main.py` re-exports `select_provider_and_model` for callers in `talaria_cli/setup.py` and `talaria_cli/fallback_cmd.py`.

### B2. ✅ `talaria_cli/update_runtime.py` — *done*
`cmd_update`, `_cmd_update_impl`, `_cmd_update_check`, plus the npm-install / fork-detection / git-stash / hangup / pre-update-backup helpers. ~2.1K lines moved out. `main.py` re-imports `cmd_update` so the argparse handler registration is unchanged.

### B3. ✅ `talaria_cli/sessions.py` — *done*
`_session_browse_picker` (curses), `_coalesce_session_name_args`, and `_relative_time` extracted. ~320 lines. The previously dead `_resolve_last_session` / `_resolve_session_by_name_or_id` helpers were removed entirely (no callers in the repo) and a stale `"acp"` entry in `_SUBCOMMANDS` was scrubbed along the way.

### B4. ~~`talaria_cli/cli_commands/` package~~ — *triaged out*
After Phase B2 landed the small `cmd_*` handlers in `main.py` (logout / auth / status / cron / webhook / slack / hooks / doctor / dump / debug / config / backup / import / version / uninstall) are each 1–5 lines: they delegate immediately into a domain module that already lives outside `main.py`. Grouping them into a `cli_commands/` package adds `__init__.py` boilerplate and a layer of indirection without saving meaningful LOC, so this phase is dropped.

### B5. Residual `main.py`
`main.py` is at 3,143 lines after B1/B2/B3 — already close to the original B5 target. Remaining content is the argparse build + dispatch (which has nowhere obvious to go without splintering the top-level parser), the small `cmd_*` wrappers (per the B4 note above), and the profile-override boot sequence (which has to run before any other import). Further reduction has low ROI without a structural re-think of how subparsers register handlers.

---

## Phase C — `run_agent.py` (~13.2K lines, the `AIAgent` monolith)

This is **the highest-risk refactor in the repo**. `AIAgent` carries deep mutable state across `run_conversation`, the agent loop, tool dispatch, fallback chain, and persistence. The Hermes upstream had this split into `agent_init.py` + `conversation_loop.py` etc., and that template is mostly still applicable — see `ref/hermes-agent/agent/` for the historical structure.

**Pre-requisite:** add at least one end-to-end conversation test that exercises the full agent loop with a stub provider. Without that, any split risks silent semantic regressions.

### C1. ✅ `agent/iteration_budget.py` — *done*
`IterationBudget` (~56 lines) lives in its own module; `run_agent.py` re-exports it so `from run_agent import IterationBudget` (used by `tests/test_iteration_budget.py`) keeps working.

### C2. ✅ `agent/tool_batching.py` — *done*
`_is_destructive_command`, `_should_parallelize_tool_batch`, `_extract_parallel_scope_path`, `_paths_overlap`, plus the supporting frozen sets (`_NEVER_PARALLEL_TOOLS` / `_PARALLEL_SAFE_TOOLS` / `_PATH_SCOPED_TOOLS`), the destructive-command regex, and `_MAX_TOOL_WORKERS`. ~146 lines. Covered by `tests/test_agent_loop_tool_batching.py`.

### C3. `agent/agent_init.py`
**Target:** `AIAgent.__init__` (L565) and the L1786 `reset_session_state`, L1858 `switch_model` — anything that mutates baseline state.

**Approach:** keep `AIAgent` as the public class; pull `__init__` body into `agent_init.initialize(agent, ...)` helper. Reduces `run_agent.py` size and makes init testable in isolation.

### C4. `agent/turn_lifecycle.py`
**Target:** `_persist_session` (L3309), `_save_trajectory` (L3589), `_convert_to_trajectory_format` (L3424), `_cleanup_task_resources` (L2894), `_flush_messages_to_session_db` — all per-turn teardown.

### C5. `agent/conversation_loop.py`
**Target:** `run_conversation` (L9673) and the agent loop body at L10039.

**Approach:** function takes `agent` and `user_input`, returns the response. `AIAgent.run_conversation` becomes a one-liner delegator.

**Risk:** highest. This is the hot path. Required: at least one integration test that exercises a multi-turn conversation with tool calls before extracting.

### C6. `agent/finish_handlers.py`
**Target:** the `finish_reason` branch logic (length / stop / tool / nudge / prefill / retry / fallback).

### C7. Residual `run_agent.py`
Should end up at ~1.5–2K lines: the `AIAgent` class definition with method delegators, the OpenAI proxy, `_SafeWriter`, `main()`.

---

## Auxiliary

### `agent/auxiliary_client.py` (~3.5K lines)
Single-responsibility (auxiliary-task LLM router) but fallback chain is intricate. Consider splitting the per-provider strategy into `agent/auxiliary_providers/{openrouter,anthropic,codex,custom}.py` once Phase C is settled. **Not urgent** — the file's complexity matches its problem domain (multi-provider OAuth + credit-exhaustion fallback).

## Test gaps to address before deep splits

The current suite is 407 passing tests, weighted toward import smoke and unit functions. The monolith splits in Phases A2 / A3 and C3 / C5 / C6 need behavior tests we currently lack:

- End-to-end `_handle_message` test driving a fake adapter through authorization → command → session → agent. **Required by A2** (slash-command extraction touches the same dispatch surface) and **A3**.
- Multi-turn `run_conversation` test with a stub provider returning tool calls then a final answer. **Required by C5 / C6.**
- `AIAgent.__init__` snapshot test covering credential-pool / checkpoint-manager / fallback-chain setup. **Required by C3.**
- Curator background-review smoke (`_spawn_background_review`) so the C-phase teardown extraction is safe. **Required by C4.**

Add these tests **before** the matching refactor PR, not after.

## Sequence recommendation

1. **A1 done** — pilot validated. ✓
2. **B1, B3** — pure relocations, zero behavior risk.
3. **A4, A2** — lifecycle and slash commands are well-bounded.
4. **C1, C2** — small, low-risk agent extractions.
5. ~~**B4** — `cli_commands/` package~~ — triaged out (see B4 above).
6. **Add the missing integration tests** — the next merged work in this area should be the gateway message-pipeline test, then the multi-turn `run_conversation` test. Everything below blocks on these.
7. **A2** — slash-command extraction (depends on the gateway integration test).
8. **A3** — message pipeline (same dependency, biggest single payoff once the test lands).
9. **A4 remainder** — `start_gateway` + signal handling into `gateway/lifecycle.py`.
10. **C3, C4** — agent init / teardown.
11. **C5, C6** — conversation loop and finish handlers (highest risk; do last, with the most test coverage in place).

Each step targets a single reviewable PR. Total expected LOC reduction across the three monoliths once everything above ships: roughly **15–20K lines redistributed** (no net deletion — this is structural, not subtractive). The current cumulative redistribution sits at **~4.7K lines** moved out across PRs #5, #6, #9, #10, #11.
