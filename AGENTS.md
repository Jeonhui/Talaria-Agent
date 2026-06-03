# Talaria Agent - Development Guide

Instructions for AI coding assistants and developers working on the talaria-agent codebase.

## Development Environment

```bash
# Prefer .venv; fall back to venv if that's what your checkout has.
source .venv/bin/activate   # or: source venv/bin/activate
```

`scripts/run_tests.sh` probes `.venv` first, then `venv`, then
`$HOME/.talaria/talaria-agent/venv` (for worktrees that share a venv with the
main checkout).

## Project Structure

File counts shift constantly — don't treat the tree below as exhaustive.
The canonical source is the filesystem. The notes call out the load-bearing
entry points you'll actually edit.

```
talaria-agent/
├── run_agent.py          # AIAgent class — core conversation loop (~12k LOC)
├── model_tools.py        # Tool orchestration, discover_builtin_tools(), handle_function_call()
├── toolsets.py           # Toolset definitions, _TALARIA_CORE_TOOLS list
├── cli_config.py         # load_cli_config() / CLI_CONFIG / save_config_value — shared config loader
├── talaria_state.py       # SessionDB — SQLite session store (FTS5 search)
├── talaria_constants.py   # get_talaria_home(), display_talaria_home() — profile-aware paths
├── talaria_logging.py     # setup_logging() — agent.log / errors.log / gateway.log (profile-aware)
├── batch_runner.py       # Parallel batch processing
├── agent/                # Agent internals (provider adapters, memory, caching, compression, etc.)
├── talaria_cli/           # CLI subcommands, setup wizard, plugins loader, skin engine
├── tools/                # Tool implementations — auto-discovered via tools/registry.py
│   └── environments/     # Terminal backends (local, docker, ssh)
├── gateway/              # Messaging gateway — run.py + session.py + platforms/
│   ├── platforms/        # Adapter per platform (discord, slack, telegram).
│   │                     # See ADDING_A_PLATFORM.md.
│   └── builtin_hooks/    # Extension point for always-registered gateway hooks (none shipped)
├── plugins/              # Plugin system (see "Plugins" section below)
│   ├── memory/           # Memory-provider plugins (user-installable)
│   ├── context_engine/   # Context-engine plugins
│   └── <others>/         # Dashboard, image-gen, disk-cleanup, examples, ...
├── skills/               # Built-in skills bundled with the repo
├── acp_adapter/          # ACP server (VS Code / Zed / JetBrains integration)
├── cron/                 # Scheduler — jobs.py, scheduler.py
├── scripts/              # run_tests.sh, release.py, auxiliary scripts
└── tests/                # Pytest suite
```

**User config:** `~/.talaria/config.yaml` (settings), `~/.talaria/.env` (API keys only).
**Logs:** `~/.talaria/logs/` — `agent.log` (INFO+), `errors.log` (WARNING+),
`gateway.log` when running the gateway. Profile-aware via `get_talaria_home()`.
Browse with `talaria logs [--follow] [--level ...] [--session ...]`.

## File Dependency Chain

```
tools/registry.py  (no deps — imported by all tool files)
       ↑
tools/*.py  (each calls registry.register() at import time)
       ↑
model_tools.py  (imports tools/registry + triggers tool discovery)
       ↑
run_agent.py, talaria_cli/main.py, gateway/run.py
```

---

## AIAgent Class (run_agent.py)

The real `AIAgent.__init__` takes ~60 parameters (credentials, routing, callbacks,
session context, budget, credential pool, etc.). The signature below is the
minimum subset you'll usually touch — read `run_agent.py` for the full list.

```python
class AIAgent:
    def __init__(self,
        base_url: str = None,
        api_key: str = None,
        provider: str = None,
        api_mode: str = None,              # "chat_completions" | "codex_responses" | ...
        model: str = "",                   # empty → resolved from config/provider later
        max_iterations: int = 90,          # tool-calling iterations (shared with subagents)
        enabled_toolsets: list = None,
        disabled_toolsets: list = None,
        quiet_mode: bool = False,
        save_trajectories: bool = False,
        platform: str = None,              # "cli", "telegram", etc.
        session_id: str = None,
        skip_context_files: bool = False,
        skip_memory: bool = False,
        credential_pool=None,
        # ... plus callbacks, thread/user/chat IDs, iteration_budget, fallback_model,
        # checkpoints config, prefill_messages, service_tier, reasoning_config, etc.
    ): ...

    def chat(self, message: str) -> str:
        """Simple interface — returns final response string."""

    def run_conversation(self, user_message: str, system_message: str = None,
                         conversation_history: list = None, task_id: str = None) -> dict:
        """Full interface — returns dict with final_response + messages."""
```

### Agent Loop

The core loop is inside `run_conversation()` — entirely synchronous, with
interrupt checks, budget tracking, and a one-turn grace call:

```python
while (api_call_count < self.max_iterations and self.iteration_budget.remaining > 0) \
        or self._budget_grace_call:
    if self._interrupt_requested: break
    response = client.chat.completions.create(model=model, messages=messages, tools=tool_schemas)
    if response.tool_calls:
        for tool_call in response.tool_calls:
            result = handle_function_call(tool_call.name, tool_call.args, task_id)
            messages.append(tool_result_message(result))
        api_call_count += 1
    else:
        return response.content
```

Messages follow OpenAI format: `{"role": "system/user/assistant/tool", ...}`.
Reasoning content is stored in `assistant_msg["reasoning"]`.

---

## Slash Commands & Config

The terminal-chat REPL (`cli.py`) was removed — Talaria runs headless: the
messaging gateway, one-shot `talaria -q "..."`, and the ACP adapter. Slash
commands now run through the gateway, but the central registry still drives
all of them.

- `load_cli_config()` / `CLI_CONFIG` / `save_config_value()` live in **`cli_config.py`** (root) — the shared config loader. Importing it bridges config → env vars (TERMINAL_*, AUXILIARY_*, etc.) as a side effect, which the gateway relies on.
- **Skin engine** (`talaria_cli/skin_engine.py`) — data-driven theming from the `display.skin` config key
- Skill slash commands: `agent/skill_commands.py` scans `~/.talaria/skills/`, injects as **user message** (not system prompt) to preserve prompt caching

### Slash Command Registry (`talaria_cli/commands.py`)

All slash commands are defined in a central `COMMAND_REGISTRY` list of `CommandDef` objects. Every downstream consumer derives from this registry automatically:

- **Gateway** — `GATEWAY_KNOWN_COMMANDS` frozenset for hook emission, `resolve_command()` for dispatch
- **Gateway help** — `gateway_help_lines()` generates `/help` output
- **Telegram** — `telegram_bot_commands()` generates the BotCommand menu
- **Slack** — `slack_subcommand_map()` generates `/talaria` subcommand routing

### Adding a Slash Command

1. Add a `CommandDef` entry to `COMMAND_REGISTRY` in `talaria_cli/commands.py`:
```python
CommandDef("mycommand", "Description of what it does", "Session",
           aliases=("mc",), args_hint="[arg]"),
```
2. Add a handler in `gateway/run.py` (the gateway dispatches slash commands):
```python
if canonical == "mycommand":
    return await self._handle_mycommand(event)
```
3. For persistent settings, use `save_config_value()` from `cli_config.py`

**CommandDef fields:**
- `name` — canonical name without slash (e.g. `"background"`)
- `description` — human-readable description
- `category` — one of `"Session"`, `"Configuration"`, `"Tools & Skills"`, `"Info"`, `"Exit"`
- `aliases` — tuple of alternative names (e.g. `("bg",)`)
- `args_hint` — argument placeholder shown in help (e.g. `"<prompt>"`, `"[name]"`)
- `cli_only` — legacy flag from the removed terminal REPL; such commands are now reachable in the gateway only when paired with a `gateway_config_gate` (below)
- `gateway_only` — only available in messaging platforms
- `gateway_config_gate` — config dotpath (e.g. `"display.tool_progress_command"`); when set on a `cli_only` command, the command becomes available in the gateway if the config value is truthy. `GATEWAY_KNOWN_COMMANDS` always includes config-gated commands so the gateway can dispatch them; help/menus only show them when the gate is open.

**Adding an alias** requires only adding it to the `aliases` tuple on the existing `CommandDef`. No other file changes needed — dispatch, help text, Telegram menu, Slack mapping, and autocomplete all update automatically.

---

## Adding New Tools

Requires changes in **2 files**:

**1. Create `tools/your_tool.py`:**
```python
import json, os
from tools.registry import registry

def check_requirements() -> bool:
    return bool(os.getenv("EXAMPLE_API_KEY"))

def example_tool(param: str, task_id: str = None) -> str:
    return json.dumps({"success": True, "data": "..."})

registry.register(
    name="example_tool",
    toolset="example",
    schema={"name": "example_tool", "description": "...", "parameters": {...}},
    handler=lambda args, **kw: example_tool(param=args.get("param", ""), task_id=kw.get("task_id")),
    check_fn=check_requirements,
    requires_env=["EXAMPLE_API_KEY"],
)
```

**2. Add to `toolsets.py`** — either `_TALARIA_CORE_TOOLS` (all platforms) or a new toolset.

Auto-discovery: any `tools/*.py` file with a top-level `registry.register()` call is imported automatically — no manual import list to maintain.

The registry handles schema collection, dispatch, availability checking, and error wrapping. All handlers MUST return a JSON string.

**Path references in tool schemas**: If the schema description mentions file paths (e.g. default output directories), use `display_talaria_home()` to make them profile-aware. The schema is generated at import time, which is after `_apply_profile_override()` sets `TALARIA_HOME`.

**State files**: If a tool stores persistent state (caches, logs, checkpoints), use `get_talaria_home()` for the base directory — never `Path.home() / ".talaria"`. This ensures each profile gets its own state.

**Agent-level tools** (todo, memory): intercepted by `run_agent.py` before `handle_function_call()`. See `tools/todo_tool.py` for the pattern.

---

## Adding Configuration

### config.yaml options:
1. Add to `DEFAULT_CONFIG` in `talaria_cli/config.py`
2. Bump `_config_version` (check the current value at the top of `DEFAULT_CONFIG`)
   ONLY if you need to actively migrate/transform existing user config
   (renaming keys, changing structure). Adding a new key to an existing
   section is handled automatically by the deep-merge and does NOT require
   a version bump.

### .env variables (SECRETS ONLY — API keys, tokens, passwords):
1. Add to `OPTIONAL_ENV_VARS` in `talaria_cli/config.py` with metadata:
```python
"NEW_API_KEY": {
    "description": "What it's for",
    "prompt": "Display name",
    "url": "https://...",
    "password": True,
    "category": "tool",  # provider, tool, messaging, setting
},
```

Non-secret settings (timeouts, thresholds, feature flags, paths, display
preferences) belong in `config.yaml`, not `.env`. If internal code needs an
env var mirror for backward compatibility, bridge it from `config.yaml` to
the env var in code (see `gateway_timeout`, `terminal.cwd` → `TERMINAL_CWD`).

### Config loaders (three paths — know which one you're in):

| Loader | Used by | Location |
|--------|---------|----------|
| `load_cli_config()` | gateway, delegation, one-shot | `cli_config.py` — merges CLI-specific defaults + user YAML, bridges to env vars |
| `load_config()` | `talaria tools`, `talaria setup`, most CLI subcommands | `talaria_cli/config.py` — merges `DEFAULT_CONFIG` + user YAML |
| Direct YAML load | Gateway runtime | `gateway/run.py` + `gateway/config.py` — reads user YAML raw |

If you add a new key and the CLI sees it but the gateway doesn't (or vice
versa), you're on the wrong loader. Check `DEFAULT_CONFIG` coverage.

### Working directory:
- **CLI** — uses the process's current directory (`os.getcwd()`).
- **Messaging** — uses `terminal.cwd` from `config.yaml`. The gateway bridges this
  to the `TERMINAL_CWD` env var for child tools. **`MESSAGING_CWD` has been
  removed** — the config loader prints a deprecation warning if it's set in
  `.env`. Same for `TERMINAL_CWD` in `.env`; the canonical setting is
  `terminal.cwd` in `config.yaml`.

---

## Skin/Theme System

The skin engine (`talaria_cli/skin_engine.py`) provides data-driven CLI visual customization. Skins are **pure data** — no code changes needed to add a new skin.

### Architecture

```
talaria_cli/skin_engine.py    # SkinConfig dataclass, built-in skins, YAML loader
~/.talaria/skins/*.yaml       # User-installed custom skins (drop-in)
```

- `init_skin_from_config()` — called at CLI startup, reads `display.skin` from config
- `get_active_skin()` — returns cached `SkinConfig` for the current skin
- `set_active_skin(name)` — switches skin at runtime (used by `/skin` command)
- `load_skin(name)` — loads from user skins first, then built-ins, then falls back to default
- Missing skin values inherit from the `default` skin automatically

### What skins customize

| Element | Skin Key | Used By |
|---------|----------|---------|
| Banner panel border | `colors.banner_border` | `banner.py` |
| Banner panel title | `colors.banner_title` | `banner.py` |
| Banner section headers | `colors.banner_accent` | `banner.py` |
| Banner dim text | `colors.banner_dim` | `banner.py` |
| Banner body text | `colors.banner_text` | `banner.py` |
| Spinner faces (waiting) | `spinner.waiting_faces` | `display.py` |
| Spinner faces (thinking) | `spinner.thinking_faces` | `display.py` |
| Spinner verbs | `spinner.thinking_verbs` | `display.py` |
| Spinner wings (optional) | `spinner.wings` | `display.py` |
| Tool output prefix | `tool_prefix` | `display.py` |
| Per-tool emojis | `tool_emojis` | `display.py` → `get_tool_emoji()` |
| Agent name | `branding.agent_name` | `banner.py` |

### Built-in skins

- `default` — Classic Talaria gold/kawaii (the current look)
- `ares` — Crimson/bronze war-god theme with custom spinner wings
- `mono` — Clean grayscale monochrome
- `slate` — Cool blue developer-focused theme

### Adding a built-in skin

Add to `_BUILTIN_SKINS` dict in `talaria_cli/skin_engine.py`:

```python
"mytheme": {
    "name": "mytheme",
    "description": "Short description",
    "colors": { ... },
    "spinner": { ... },
    "branding": { ... },
    "tool_prefix": "┊",
},
```

### User skins (YAML)

Users create `~/.talaria/skins/<name>.yaml`:

```yaml
name: cyberpunk
description: Neon-soaked terminal theme

colors:
  banner_border: "#FF00FF"
  banner_title: "#00FFFF"
  banner_accent: "#FF1493"

spinner:
  thinking_verbs: ["jacking in", "decrypting", "uploading"]
  wings:
    - ["⟨⚡", "⚡⟩"]

branding:
  agent_name: "Cyber Agent"
  response_label: " ⚡ Cyber "

tool_prefix: "▏"
```

Activate with `/skin cyberpunk` or `display.skin: cyberpunk` in config.yaml.

---

## Plugins

Talaria has two plugin surfaces. Both live under `plugins/` in the repo so
repo-shipped plugins can be discovered alongside user-installed ones in
`~/.talaria/plugins/` and pip-installed entry points.

### General plugins (`talaria_cli/plugins.py` + `plugins/<name>/`)

`PluginManager` discovers plugins from `~/.talaria/plugins/`, `./.talaria/plugins/`,
and pip entry points. Each plugin exposes a `register(ctx)` function that
can:

- Register Python-callback lifecycle hooks:
  `pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`,
  `on_session_start`, `on_session_end`
- Register new tools via `ctx.register_tool(...)`
- Register CLI subcommands via `ctx.register_cli_command(...)` — the
  plugin's argparse tree is wired into `talaria` at startup so
  `talaria <pluginname> <subcmd>` works with no change to `main.py`

Hooks are invoked from `model_tools.py` (pre/post tool) and `run_agent.py`
(lifecycle). **Discovery timing pitfall:** `discover_plugins()` only runs
as a side effect of importing `model_tools.py`. Code paths that read plugin
state without importing `model_tools.py` first must call `discover_plugins()`
explicitly (it's idempotent).

### Memory-provider plugins (`plugins/memory/<name>/`)

Separate discovery system for pluggable memory backends. Talaria ships
only the always-on built-in (`MEMORY.md` / `USER.md`); external providers
are user-installable via `talaria plugins install <repo>`.

Each provider implements the `MemoryProvider` ABC (see `agent/memory_provider.py`)
and is orchestrated by `agent/memory_manager.py`. Lifecycle hooks include
`sync_turn(turn_messages)`, `prefetch(query)`, `shutdown()`, and optional
`post_setup(talaria_home, config)` for setup-wizard integration.

**CLI commands via `plugins/memory/<name>/cli.py`:** if a memory plugin
defines `register_cli(subparser)`, `discover_plugin_cli_commands()` finds
it at argparse setup time and wires it into `talaria <plugin>`. The
framework only exposes CLI commands for the **currently active** memory
provider (read from `memory.provider` in config.yaml), so disabled
providers don't clutter `talaria --help`.

**Rule:** plugins MUST NOT modify core files (`run_agent.py`,
`gateway/run.py`, `talaria_cli/main.py`, etc.). If a plugin needs a
capability the framework doesn't expose, expand the generic plugin
surface (new hook, new ctx method) — never hardcode plugin-specific
logic into core.

### Dashboard / context-engine / image-gen plugin directories

`plugins/context_engine/`, `plugins/image_gen/`, `plugins/example-dashboard/`,
etc. follow the same pattern (ABC + orchestrator + per-plugin directory).
Context engines plug into `agent/context_engine.py`; image-gen providers
into `agent/image_gen_provider.py`.

---

## Skills

- **`skills/`** — built-in skills shipped and loadable by default.
  Organized by category directories (e.g. `skills/configuration/`,
  `skills/devops/`, `skills/software-development/`).

### SKILL.md frontmatter

Standard fields: `name`, `description`, `version`, `platforms`
(OS-gating list: `[macos]`, `[linux, macos]`, ...),
`metadata.talaria.tags`, `metadata.talaria.category`,
`metadata.talaria.config` (config.yaml settings the skill needs — stored
under `skills.config.<key>`, prompted during setup, injected at load time).

---

## Important Policies

### Prompt Caching Must Not Break

Talaria-Agent ensures caching remains valid throughout a conversation. **Do NOT implement changes that would:**
- Alter past context mid-conversation
- Change toolsets mid-conversation
- Reload memories or rebuild system prompts mid-conversation

Cache-breaking forces dramatically higher costs. The ONLY time we alter context is during context compression.

Slash commands that mutate system-prompt state (skills, tools, memory, etc.)
must be **cache-aware**: default to deferred invalidation (change takes
effect next session), with an opt-in `--now` flag for immediate
invalidation. See `/skills install --now` for the canonical pattern.

### Background Process Notifications (Gateway)

When `terminal(background=true, notify_on_complete=true)` is used, the gateway runs a watcher that
detects process completion and triggers a new agent turn. Control verbosity of background process
messages with `display.background_process_notifications`
in config.yaml (or `TALARIA_BACKGROUND_NOTIFICATIONS` env var):

- `all` — running-output updates + final message (default)
- `result` — only the final completion message
- `error` — only the final message when exit code != 0
- `off` — no watcher messages at all

---

## Profiles: Multi-Instance Support

Talaria supports **profiles** — multiple fully isolated instances, each with its own
`TALARIA_HOME` directory (config, API keys, memory, sessions, skills, gateway, etc.).

The core mechanism: `_apply_profile_override()` in `talaria_cli/main.py` sets
`TALARIA_HOME` before any module imports. All `get_talaria_home()` references
automatically scope to the active profile.

### Rules for profile-safe code

1. **Use `get_talaria_home()` for all TALARIA_HOME paths.** Import from `talaria_constants`.
   NEVER hardcode `~/.talaria` or `Path.home() / ".talaria"` in code that reads/writes state.
   ```python
   # GOOD
   from talaria_constants import get_talaria_home
   config_path = get_talaria_home() / "config.yaml"

   # BAD — breaks profiles
   config_path = Path.home() / ".talaria" / "config.yaml"
   ```

2. **Use `display_talaria_home()` for user-facing messages.** Import from `talaria_constants`.
   This returns `~/.talaria` for default or `~/.talaria/profiles/<name>` for profiles.
   ```python
   # GOOD
   from talaria_constants import display_talaria_home
   print(f"Config saved to {display_talaria_home()}/config.yaml")

   # BAD — shows wrong path for profiles
   print("Config saved to ~/.talaria/config.yaml")
   ```

3. **Module-level constants are fine** — they cache `get_talaria_home()` at import time,
   which is AFTER `_apply_profile_override()` sets the env var. Just use `get_talaria_home()`,
   not `Path.home() / ".talaria"`.

4. **Tests that mock `Path.home()` must also set `TALARIA_HOME`** — since code now uses
   `get_talaria_home()` (reads env var), not `Path.home() / ".talaria"`:
   ```python
   with patch.object(Path, "home", return_value=tmp_path), \
        patch.dict(os.environ, {"TALARIA_HOME": str(tmp_path / ".talaria")}):
       ...
   ```

5. **Gateway platform adapters should use token locks** — if the adapter connects with
   a unique credential (bot token, API key), call `acquire_scoped_lock()` from
   `gateway.status` in the `connect()`/`start()` method and `release_scoped_lock()` in
   `disconnect()`/`stop()`. This prevents two profiles from using the same credential.
   See `gateway/platforms/telegram.py` for the canonical pattern.

6. **Profile operations are HOME-anchored, not TALARIA_HOME-anchored** — `_get_profiles_root()`
   returns `Path.home() / ".talaria" / "profiles"`, NOT `get_talaria_home() / "profiles"`.
   This is intentional — it lets `talaria -p coder profile list` see all profiles regardless
   of which one is active.

## Known Pitfalls

### DO NOT hardcode `~/.talaria` paths
Use `get_talaria_home()` from `talaria_constants` for code paths. Use `display_talaria_home()`
for user-facing print/log messages. Hardcoding `~/.talaria` breaks profiles — each profile
has its own `TALARIA_HOME` directory. This was the source of 5 bugs fixed in PR #3575.

### DO NOT introduce new `simple_term_menu` usage
Existing call sites in `talaria_cli/main.py` remain for legacy fallback only;
the preferred UI is curses (stdlib) because `simple_term_menu` has
ghost-duplication rendering bugs in tmux/iTerm2 with arrow keys. New
interactive menus must use `talaria_cli/curses_ui.py` — see
`talaria_cli/tools_config.py` for the canonical pattern.

### DO NOT use `\033[K` (ANSI erase-to-EOL) in spinner/display code
Leaks as literal `?[K` text under `prompt_toolkit`'s `patch_stdout`. Use space-padding: `f"\r{line}{' ' * pad}"`.

### `_last_resolved_tool_names` is a process-global in `model_tools.py`
`_run_single_child()` in `delegate_tool.py` saves and restores this global around subagent execution. If you add new code that reads this global, be aware it may be temporarily stale during child agent runs.

### DO NOT hardcode cross-tool references in schema descriptions
Tool schema descriptions must not mention tools from other toolsets by name (e.g., `browser_navigate` saying "prefer web_search"). Those tools may be unavailable (missing API keys, disabled toolset), causing the model to hallucinate calls to non-existent tools. If a cross-reference is needed, add it dynamically in `get_tool_definitions()` in `model_tools.py` — see the `browser_navigate` / `execute_code` post-processing blocks for the pattern.

### The gateway has TWO message guards — both must bypass approval/control commands
When an agent is running, messages pass through two sequential guards:
(1) **base adapter** (`gateway/platforms/base.py`) queues messages in
`_pending_messages` when `session_key in self._active_sessions`, and
(2) **gateway runner** (`gateway/run.py`) intercepts `/stop`, `/new`,
`/queue`, `/status`, `/approve`, `/deny` before they reach
`running_agent.interrupt()`. Any new command that must reach the runner
while the agent is blocked (e.g. approval prompts) MUST bypass BOTH
guards and be dispatched inline, not via `_process_message_background()`
(which races session lifecycle).

### Squash merges from stale branches silently revert recent fixes
Before squash-merging a PR, ensure the branch is up to date with `main`
(`git fetch origin main && git reset --hard origin/main` in the worktree,
then re-apply the PR's commits). A stale branch's version of an unrelated
file will silently overwrite recent fixes on main when squashed. Verify
with `git diff HEAD~1..HEAD` after merging — unexpected deletions are a
red flag.

### Don't wire in dead code without E2E validation
Unused code that was never shipped was dead for a reason. Before wiring an
unused module into a live code path, E2E test the real resolution chain
with actual imports (not mocks) against a temp `TALARIA_HOME`.

### Tests must not write to `~/.talaria/`
The `_isolate_talaria_home` autouse fixture in `tests/conftest.py` redirects `TALARIA_HOME` to a temp dir. Never hardcode `~/.talaria/` paths in tests.

**Profile tests**: When testing profile features, also mock `Path.home()` so that
`_get_profiles_root()` and `_get_default_talaria_home()` resolve within the temp dir.
Use the pattern from `tests/talaria_cli/test_profiles.py`:
```python
@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    home = tmp_path / ".talaria"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("TALARIA_HOME", str(home))
    return home
```

---

## Testing

**ALWAYS use `scripts/run_tests.sh`** — do not call `pytest` directly. The script enforces
hermetic environment parity with CI (unset credential vars, TZ=UTC, LANG=C.UTF-8,
4 xdist workers matching GHA ubuntu-latest). Direct `pytest` on a 16+ core
developer machine with API keys set diverges from CI in ways that have caused
multiple "works locally, fails in CI" incidents (and the reverse).

```bash
scripts/run_tests.sh                                  # full suite, CI-parity
scripts/run_tests.sh tests/gateway/                   # one directory
scripts/run_tests.sh tests/agent/test_foo.py::test_x  # one test
scripts/run_tests.sh -v --tb=long                     # pass-through pytest flags
```

### Why the wrapper (and why the old "just call pytest" doesn't work)

Five real sources of local-vs-CI drift the script closes:

| | Without wrapper | With wrapper |
|---|---|---|
| Provider API keys | Whatever is in your env (auto-detects pool) | All `*_API_KEY`/`*_TOKEN`/etc. unset |
| HOME / `~/.talaria/` | Your real config+auth.json | Temp dir per test |
| Timezone | Local TZ (PDT etc.) | UTC |
| Locale | Whatever is set | C.UTF-8 |
| xdist workers | `-n auto` = all cores (20+ on a workstation) | `-n 4` matching CI |

`tests/conftest.py` also enforces points 1-4 as an autouse fixture so ANY pytest
invocation (including IDE integrations) gets hermetic behavior — but the wrapper
is belt-and-suspenders.

### Running without the wrapper (only if you must)

If you can't use the wrapper (e.g. on Windows or inside an IDE that shells
pytest directly), at minimum activate the venv and pass `-n 4`:

```bash
source .venv/bin/activate   # or: source venv/bin/activate
python -m pytest tests/ -q -n 4
```

Worker count above 4 will surface test-ordering flakes that CI never sees.

Always run the full suite before pushing changes.

### Don't write change-detector tests

A test is a **change-detector** if it fails whenever data that is **expected
to change** gets updated — model catalogs, config version numbers,
enumeration counts, hardcoded lists of provider models. These tests add no
behavioral coverage; they just guarantee that routine source updates break
CI and cost engineering time to "fix."

**Do not write:**

```python
# catalog snapshot — breaks every model release
assert "gemini-2.5-pro" in _PROVIDER_MODELS["gemini"]
assert "MiniMax-M2.7" in models

# config version literal — breaks every schema bump
assert DEFAULT_CONFIG["_config_version"] == 21

# enumeration count — breaks every time a skill/provider is added
assert len(_PROVIDER_MODELS["huggingface"]) == 8
```

**Do write:**

```python
# behavior: does the catalog plumbing work at all?
assert "gemini" in _PROVIDER_MODELS
assert len(_PROVIDER_MODELS["gemini"]) >= 1

# behavior: does migration bump the user's version to current latest?
assert raw["_config_version"] == DEFAULT_CONFIG["_config_version"]

# invariant: no plan-only model leaks into the legacy list
assert not (set(moonshot_models) & coding_plan_only_models)

# invariant: every model in the catalog has a context-length entry
for m in _PROVIDER_MODELS["huggingface"]:
    assert m.lower() in DEFAULT_CONTEXT_LENGTHS_LOWER
```

The rule: if the test reads like a snapshot of current data, delete it. If
it reads like a contract about how two pieces of data must relate, keep it.
When a PR adds a new provider/model and you want a test, make the test
assert the relationship (e.g. "catalog entries all have context lengths"),
not the specific names.

Reviewers should reject new change-detector tests; authors should convert
them into invariants before re-requesting review.
