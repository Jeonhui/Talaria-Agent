# Monolith Refactor Roadmap

Three files in this repo are over 7,000 lines and concentrate responsibilities that should live in smaller, single-purpose modules. This document captures the plan to split them incrementally without breaking the test suite or the import API that gateway/CLI/Plugin consumers depend on.

The work is intentionally **not** a single PR — each phase below is an independent unit of work, sized so it can be reviewed, reverted, and merged without entangling the rest of the file.

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

### A4. `gateway/lifecycle.py`
**Target:** `start_gateway`, shutdown signal handling, `_start_cron_ticker` (currently at L11463), runtime status writes.

**Approach:** classic facade pattern — `lifecycle.start(runner)` / `lifecycle.stop(runner)` wrap `GatewayRunner.run`. Easy to test in isolation.

### A5. Residual `gateway/run.py`
Should end up at ~1–2K lines: the `GatewayRunner` class proper, plus its config/runtime-resolution helpers (`_resolve_runtime_agent_kwargs`, `_try_resolve_fallback_provider`, `_resolve_gateway_model`).

---

## Phase B — `talaria_cli/main.py` (~7.5K lines)

The CLI surface area has 23 `cmd_*` handlers but most of the bulk is in **provider/auth flow scaffolding** — not the handlers themselves.

### B1. `talaria_cli/provider_flows.py`
**Target:** `_aux_*`, `_model_flow_*`, `_save_custom_provider`, `_run_anthropic_oauth_flow`, `_aux_select_for_task` — the L987–2628 block. ~1.6K lines.

**Approach:** these helpers are already well-scoped (`_aux_*` for auxiliary task picker, `_model_flow_*` for provider-specific model selection). Move as-is into a new module, keep names, update import in `main.py`.

**Why first:** zero behavior change, zero risk to handler logic, large LOC win.

### B2. `talaria_cli/update_runtime.py`
**Target:** L2790–3930 update infrastructure (`_run_npm_install_deterministic`, `_update_via_zip`, `_stash_local_changes_if_needed`, fork-detection helpers, `_install_python_dependencies_with_optional_fallback`, hangup protection). ~1.1K lines.

**Approach:** move helpers + leave `cmd_update` in `main.py` as a thin facade that calls into `update_runtime.run(args)`.

### B3. `talaria_cli/sessions.py`
**Target:** L348–651 session browse picker (curses), `_resolve_last_session`, `_resolve_session_by_name_or_id`, `_coalesce_session_name_args`.

**Approach:** straight module move.

### B4. `talaria_cli/cli_commands/` package
**Target:** the small handlers at L2629–2782 (`cmd_logout`, `cmd_auth`, `cmd_status`, `cmd_cron`, `cmd_webhook`, `cmd_slack`, `cmd_hooks`, `cmd_doctor`, `cmd_dump`, `cmd_debug`, `cmd_config`, `cmd_backup`, `cmd_import`, `cmd_version`, `cmd_uninstall`).

**Approach:** group by domain (`cli_commands/diagnostics.py`, `cli_commands/auth.py`, `cli_commands/integrations.py`). Each module exports `cmd_*` functions and an `add_subparser(subparsers)` helper. `main.py` calls each module's `add_subparser` during argparse build.

### B5. Residual `main.py`
Should end up at ~1.5–2K lines: argparse build, dispatch loop, `main()`, profile override, the entry handlers that delegate to extracted modules.

---

## Phase C — `run_agent.py` (~13.2K lines, the `AIAgent` monolith)

This is **the highest-risk refactor in the repo**. `AIAgent` carries deep mutable state across `run_conversation`, the agent loop, tool dispatch, fallback chain, and persistence. The Hermes upstream had this split into `agent_init.py` + `conversation_loop.py` etc., and that template is mostly still applicable — see `ref/hermes-agent/agent/` for the historical structure.

**Pre-requisite:** add at least one end-to-end conversation test that exercises the full agent loop with a stub provider. Without that, any split risks silent semantic regressions.

### C1. `agent/iteration_budget.py`
**Target:** `class IterationBudget` (L294) and its helpers. ~80 lines.

**Why first:** smallest, has no `AIAgent` coupling. Pure win.

### C2. `agent/tool_batching.py`
**Target:** `_is_destructive_command`, `_should_parallelize_tool_batch`, `_extract_parallel_scope_path`, `_paths_overlap` (L387–481).

**Approach:** module move. Tests exist at `tests/test_agent_loop_tool_batching.py`.

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

The current suite is 401 passing tests, weighted toward import smoke and unit functions. The monolith splits in Phases A3 and C5 / C6 need behavior tests we currently lack:

- End-to-end `_handle_message` test driving a fake adapter through authorization → command → session → agent.
- Multi-turn `run_conversation` test with a stub provider returning tool calls then a final answer.
- Curator background-review smoke (`_spawn_background_review`) so the C-phase teardown extraction is safe.

Add these tests **before** the matching refactor PR, not after.

## Sequence recommendation

1. **A1 done** — pilot validated. ✓
2. **B1, B3** — pure relocations, zero behavior risk.
3. **A4, A2** — lifecycle and slash commands are well-bounded.
4. **C1, C2** — small, low-risk agent extractions.
5. **B4** — `cli_commands/` package.
6. Add the missing integration tests.
7. **A3** — message pipeline (only after integration test lands).
8. **B2, B5** — finish CLI.
9. **C3, C4** — agent init / teardown.
10. **C5, C6** — conversation loop and finish handlers (highest risk; do last, with the most test coverage in place).

Each step targets a single reviewable PR. Total expected LOC reduction in the three monoliths: roughly **15–20K lines** redistributed (no net deletion — this is structural, not subtractive).
