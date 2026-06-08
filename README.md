# Talaria Agent

> A lean personal assistant for Discord, Slack, Telegram, CLI, and optional MCP.

Talaria is a slim, opinionated fork of [Hermes Agent](https://github.com/NousResearch/hermes-agent) that drops the everything-included framework surface and keeps a small, well-defined assistant. Against the [`ref/hermes-agent`](ref/hermes-agent) snapshot in this repo, Talaria is **~61% smaller** by Python production code (excluding tests) — **~173k LOC across 212 files**, down from **~448k LOC across 630 files**. With tests included the gap is even wider (**~175k vs ~900k** LOC; **~80% smaller / 5× reduction**), driven by 15+ whole directories that were removed wholesale (`acp_adapter/`, `tui_gateway/`, `web/`, `optional-skills/`, `locales/`, `providers/`, `plugins/` Marketplace tier, etc.).

## 3-minute start

```bash
# 1. Install (Linux/macOS/Termux)
curl -fsSL https://raw.githubusercontent.com/Jeonhui/Talaria-Agent/main/scripts/install.sh | bash

# 2. Wizard picks provider + API key + default model
talaria setup

# 3. Try it
talaria -q "what is 2+2"        # one-shot
talaria gateway run             # run Discord / Slack / Telegram gateway
```

Done. Skip to [Common commands](#common-commands) for the day-to-day. The rest of this README is reference.

```
                       ░████████ 
               ████████████████  
         █████████████████░      
     ███████████████████████████ 
  ░███████████████████████████   
 ███████████████████░            
 ███████████████████████████████ 
 █████████████████████████████   
  █████████████████████          
   ██████████████████████        
    ███████░                     
```

## Why slim

Talaria exists because Hermes ships everything-and-the-kitchen-sink and that
broad surface gets in the way of personal use. The lean fork is shaped around
three goals:

- **Fit to purpose** — only the surfaces I actually use (Discord / Slack /
  Telegram + Claude / GPT / Codex / Xiaomi / OpenRouter / local). No voice,
  no web dashboard, no aggregator middlemen, no third-party memory plugins.
  Less code = less to misconfigure and less to break on upgrade.
- **Easier to customize** — fewer abstractions, shorter call paths, no
  optional-skill registry or plugin marketplace layers to thread through.
  Adding a tool, swapping a prompt, or changing the agent loop is a small
  diff against ~173k Python LOC, not a hunt across ~448k.
- **Resource efficiency** — smaller install footprint, faster cold start,
  lower memory baseline, fewer background services. Runs comfortably on a
  Termux phone or a tiny VPS, not just a workstation. No optional extras
  pulled in "just in case."

If you need the full Hermes feature matrix (voice, web UI, every messenger,
RL/eval harnesses, plugin registries), use upstream Hermes. Talaria is the
opinionated subset for one developer's daily driver.

> 한국어 README: [README.ko.md](README.ko.md)

## What ships

| Surface | Talaria |
|---|---|
| **Providers** | Anthropic (Claude), OpenAI (GPT), OpenAI Codex, Xiaomi MiMo, OpenRouter (200+ models), local (LM Studio / Ollama / vLLM), custom (any OpenAI-compatible endpoint) |
| **Messaging** | Discord, Slack, Telegram |
| **Terminal backends** | local, Docker, SSH |
| **MCP** | Supported, with automatic reconnection (infinite retry + backoff by default). Zero servers shipped — add your own with `talaria mcp add`. |
| **Skills** | Bundled `configuration` + `devops` + `software-development`; install more with `talaria skills install <repo>` |
| **Integration modules** | One swappable unit for identity, MCP wiring, per-user tools, context, skills, and logging — `talaria integration setup` |
| **Skins** | Icon / branding / colors as YAML; scaffold + switch with `talaria skin new` / `talaria skin set` |

## What's been removed

Whole subsystems dropped to keep the assistant focused:

- **Voice & TTS** — speech-to-text, text-to-speech, push-to-talk, Discord voice channels, ElevenLabs/Edge/MiniMax/NeuTTS providers
- **Web dashboard & ink/React TUI frontend** — no graphical surface ships
- **Interactive terminal chat** — the `talaria chat` REPL was removed; Talaria now runs headless. Use the messaging gateway (Discord/Slack/Telegram) or one-shot `talaria -q "..."`
- **ACP editor adapter** — the Agent Client Protocol server for Zed / VS Code / JetBrains integration was removed; Talaria is messaging-first
- **Aggregator paths** — Vercel AI Gateway and Nous Portal subscription system (OpenRouter is now a first-class provider with its own key)
- **Auth flows** — Nous Portal device-code login, OpenClaw migration, the `talaria login` subcommand
- **Backends** — Modal, Daytona, Singularity sandbox executors (only local / Docker / SSH remain)
- **Messaging** — every platform other than Discord / Slack / Telegram (DingTalk, BlueBubbles, WhatsApp, Matrix, Signal, etc.); the `Platform` enum is now 6 members (LOCAL, TELEGRAM, DISCORD, SLACK, API_SERVER, WEBHOOK) and plugin platforms still register dynamically via `Platform._missing_()`
- **Other** — RL/eval harnesses, third-party memory plugins (Honcho / Mem0 / Hindsight), the official `optional-skills/` registry, ~2200 lines of dead provider catalogs and unused detectors

Net (production code, tests excluded): **~173k Python LOC in 212 files**, down from **~448k Python LOC in 630 files** in the `ref/hermes-agent` snapshot. Including tests/, the comparison widens to **~175k vs ~900k LOC** (Hermes carries ~1,238 test files versus Talaria's 20). Specific killer cuts: `plugins/` went from 124 files to 3 (Hermes's plugin marketplace tier was dropped entirely), `providers/` and `tui_gateway/` and `web/` and `acp_adapter/` are gone, and the messaging-adapter set was cut from 15+ down to 3 (Discord / Slack / Telegram).

---

## Install

### One-liner (Linux / macOS / Termux)

```bash
curl -fsSL https://raw.githubusercontent.com/Jeonhui/Talaria-Agent/main/scripts/install.sh | bash
```

This clones the repo, creates a venv, installs all extras, links `talaria` onto your `PATH`, and runs `talaria setup` so you finish with a working bot.

Skip the wizard with `bash -s -- --skip-setup` and rerun later with `talaria setup`.

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/Jeonhui/Talaria-Agent/main/scripts/install.ps1 | iex
```

### Manual

```bash
git clone https://github.com/Jeonhui/Talaria-Agent.git
cd Talaria-Agent
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
talaria setup
```

### Docker

Pre-built image from GitHub Container Registry:

```bash
mkdir -p ~/.talaria
docker run --rm -it -v ~/.talaria:/opt/data \
    -e TALARIA_UID=$(id -u) -e TALARIA_GID=$(id -g) \
    ghcr.io/jeonhui/talaria-agent:latest setup
docker run -d --name talaria --restart unless-stopped \
    --network host -v ~/.talaria:/opt/data \
    -e TALARIA_UID=$(id -u) -e TALARIA_GID=$(id -g) \
    ghcr.io/jeonhui/talaria-agent:latest
docker logs -f talaria
```

Or build locally with the included compose file:

```bash
git clone https://github.com/Jeonhui/Talaria-Agent.git
cd Talaria-Agent
mkdir -p ~/.talaria
TALARIA_UID=$(id -u) TALARIA_GID=$(id -g) docker compose build
docker compose run --rm gateway setup            # interactive wizard, writes ~/.talaria/.env
TALARIA_UID=$(id -u) TALARIA_GID=$(id -g) docker compose up -d
docker compose logs -f
```

The image runs `gateway run` by default. Use `docker exec -it talaria /opt/talaria/talaria <cmd>` for one-off commands against the running container.

---

## Quick start

After `talaria setup`:

```bash
talaria -q "what is 2+2"      # one-shot query (answer to stdout, then exit)
talaria gateway run           # run the messaging gateway in the foreground
talaria gateway start         # install + start as a background service
talaria status                # show provider / API keys / platforms / gateway state
talaria sessions status       # active sessions + live MCP connection status
talaria doctor                # detailed diagnostics
```

Configuration lives in `~/.talaria/`:

```
~/.talaria/
├── .env                # secrets (API keys, bot tokens)
├── config.yaml         # provider, terminal backend, agent settings
├── sessions/           # conversation history
├── skills/             # installed skills
└── plugins/            # user-added plugins
```

Almost everything is set via `talaria setup` (interactive) or `talaria config set <key> <value>`. Direct file edits are also fine.

---

## Setup wizard

`talaria setup` walks the three essentials:

1. **Model & Provider** — pick provider, enter API key, choose default model
2. **Terminal Backend** — local / Docker / SSH
3. **Messaging Platforms** — Discord / Slack / Telegram bot tokens + allowlists

Advanced sections are opt-in:

```bash
talaria setup tools           # toolset checklist per platform
talaria setup agent           # max iterations, compression, display
```

Or run a single section directly: `talaria setup model | terminal | gateway | tools | agent`.

---

## Common commands

```bash
talaria -q "what is 2+2"           # one-shot query (also: --oneshot / -z)
talaria gateway run                # chat via Discord / Slack / Telegram
talaria sessions status            # active sessions + MCP connection status

talaria model                      # switch provider/model
talaria config show                # show current config
talaria config set model.default mimo-v2.5-pro
talaria config set model.provider xiaomi

talaria gateway run                # foreground gateway
talaria gateway start | stop | restart | status
talaria gateway install            # install as systemd / launchd service
talaria logs --follow              # tail gateway logs

talaria mcp add <name> <url-or-cmd>  # connect a Model Context Protocol server
talaria mcp list

talaria integration setup          # pick + configure the active integration module
talaria integration status         # show active module + what's installed

talaria skin list                  # list skins (★ = active)
talaria skin new mybrand           # scaffold a skin YAML (icon / name / colors)
talaria skin set mybrand           # activate it

talaria skills browse              # browse available skills
talaria skills install <repo>      # install from GitHub

talaria cron list                  # scheduled jobs
talaria cron create "0 9 * * *" "Daily standup reminder"

talaria agents list                # show every profile + running state
talaria agents start A B           # spawn detached gateways for multiple profiles
talaria agents stop A | --all      # stop one or every running agent
talaria agents logs A -f           # tail a specific profile's log

talaria checkpoints                # disk usage + per-project breakdown
talaria checkpoints prune          # delete orphan/stale shadow repos
talaria security audit             # OSV.dev scan of venv + plugins + pinned MCP servers

talaria insights --days 7          # session usage report
talaria status                     # health summary (now includes session recap)
talaria doctor                     # detailed diagnostics
talaria uninstall [--full]         # remove (--full also wipes ~/.talaria)
```

---

## Integration modules

An **integration module** is one swappable unit that owns every external-service
concern for a deployment: identity/authorization, MCP endpoint + key, the set of
MCP tools each user may call, per-user context files, per-user skills, and message
logging. Swap the whole backend (e.g. a different tenant/service) by changing one
config line — running with **no module is fully supported** (everything degrades
to the built-in behavior).

```bash
talaria integration setup     # pick a module, walk its config schema
talaria integration status    # active module + what's installed
talaria integration off       # disable
```

A module is a directory under `integrations/<name>/` implementing the
`IntegrationModule` ABC (`agent/integration_module.py`). One is active at a time,
selected by `integration.module` in config.yaml. A worked reference ships at
`integrations/example/`.

What a module does, per user, on every message:

| Capability | Method | Effect |
|---|---|---|
| **Identity / auth** | `resolve_user()` | Network-backed allow/deny — owns authorization when active; the env allowlist is the fallback. Approvals/bans take effect immediately (denied verdicts aren't cached). |
| **MCP wiring** | `mcp_url()` / `mcp_key()` | Registers the module's MCP server (inherits auto-reconnect). |
| **Tool gating** | `available_tools()` | The agent sees only the **intersection** of the user's allowed tools and the registered MCP tools. Built-in tools are never gated. |
| **Context** | `context_files()` | Per-user files injected on each new session. |
| **Skills** | `skills()` | Per-user skills auto-loaded on each new session. |
| **Memory** | `log_message()` / `log_response()` | Capture turns per user; recall them on the next session (the `example` module ships a working per-user memory store). |

Drop a directory in `~/.talaria/integrations/<name>/` to install your own without
touching the source tree.

## Skins & branding

Icon, agent name, colors, prompt symbol, and spinner are all driven by **skin**
YAML — no code changes to rebrand. Scaffold one, edit it, switch to it:

```bash
talaria skin new mybrand          # writes ~/.talaria/skins/mybrand.yaml (icon/name/colors prefilled)
talaria skin set mybrand          # activate (persists to display.skin in config.yaml)

talaria skin list                 # built-in + user skins (★ = active)
talaria skin show                 # the active skin's branding + key colors
talaria skin path mybrand         # YAML path, for your editor
```

The scaffolded YAML leads with the quick wins — `brand_emoji`, `agent_name`,
`response_label`, and the main colors — with everything else inheriting from the
default skin. Built-in skins: `default`, `mono`.

---

## Configuration via environment

`~/.talaria/.env` holds secrets. The full template lives at [`.env.example`](.env.example). Common keys:

```bash
# Provider — pick one or several
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
XIAOMI_API_KEY=...
OPENROUTER_API_KEY=sk-or-...            # 200+ models via one endpoint
LM_BASE_URL=http://localhost:11434/v1   # local Ollama / LM Studio

# Discord
DISCORD_BOT_TOKEN=...
DISCORD_ALLOWED_USERS=123456789012345678
DISCORD_HOME_CHANNEL=...

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USERS=...

# Terminal backend (local / docker / ssh)
TERMINAL_ENV=local
```

Provider/model live in `~/.talaria/config.yaml` so multi-bot setups don't fight over env vars.

Internal `TALARIA_*` knobs (timeouts, paths, gateway tuning, etc.) are documented in [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md). Every supported `config.yaml` key is listed in [`cli-config.yaml.full.example`](cli-config.yaml.full.example) — only reach for these when overriding a default.

---

## Running multiple agents

Talaria supports running several independent agents concurrently — each with
its own SOUL.md, skills, API keys, and even messaging platform. This is built
on top of the profile system: every profile is a fully isolated
`TALARIA_HOME` directory.

```bash
# Create two independent profiles
talaria profile create work --clone-config       # copies config.yaml + .env scaffolding
talaria profile create personal --clone-config

# Customize each separately
talaria --profile work config set model.default claude-sonnet-4.6
talaria --profile personal config set model.default gpt-5

# Each profile has its own SOUL.md, skills, and .env
$EDITOR ~/.talaria/profiles/work/SOUL.md
$EDITOR ~/.talaria/profiles/personal/.env        # different Discord bot token!

# Run both concurrently as detached background processes
talaria agents start work personal

talaria agents list
# NAME       STATE     PID  SKILLS  MODEL
# default    stopped    —        0  —
# work       running  41023      3  anthropic/claude-sonnet-4.6
# personal   running  41045      5  openai/gpt-5

talaria agents logs work -f                       # tail one
talaria agents stop personal                      # stop one
talaria agents stop --all                         # stop everything
```

Per-profile isolation includes: `config.yaml`, `.env`, `SOUL.md`, `skills/`,
`state.db` (sessions), `checkpoints/`, MCP server processes, and the
systemd/launchd service name (`talaria-gateway-work` vs
`talaria-gateway-personal`).

---

## Project layout

```
talaria_cli/         CLI entrypoint, setup wizard, model picker, gateway commands
  ├─ main.py             argparse build + small cmd_ handlers + dispatch
  ├─ provider_flows.py   select_provider_and_model + _model_flow_* / _aux_* / OAuth
  ├─ sessions.py         interactive session picker + session-name argv coalescer
  ├─ commands.py         slash-command registry (shared by CLI / gateway / adapters)
  └─ auth.py / config.py setup state + provider credentials
agent/               agent loop, prompt builder, transports (anthropic / chat_completions / codex)
tools/               built-in tools: terminal, file, web, browser, memory, todo, vision, MCP, skills
gateway/             messaging gateway (Discord / Slack / Telegram adapters + session store)
  ├─ run.py              GatewayRunner — adapter lifecycle, message dispatch
  ├─ auth.py             user-authorization policy (allowlists + pairing + integration override)
  ├─ integration_bridge.py  runtime facade for the active integration module
  ├─ session.py          SessionSource / SessionStore + session_key builder
  └─ platforms/          per-platform adapters (discord, slack, telegram)
integrations/        swappable integration modules (identity + MCP + context + skills + logging)
  ├─ __init__.py         loader (discover / load / active)
  └─ example/            worked reference module
plugins/             user-extensible plugin host
cron/                cron scheduler
skills/              bundled skills (configuration, devops, software-development)
docker/              Docker entrypoint
scripts/             install / uninstall / build helpers
docs/                ENVIRONMENT.md (env-var reference) and REFACTOR-ROADMAP.md
```

The three big entry surfaces are `run_agent.py` (agent core), `talaria_cli/main.py` (CLI), and `gateway/run.py` (messaging gateway); each carries a NAVIGATION docstring at the top so you can jump to the right region without grepping. Progress on splitting them into smaller modules is tracked in [`docs/REFACTOR-ROADMAP.md`](docs/REFACTOR-ROADMAP.md).

---

## Status

- **Version:** v0.1.0 (2026-05-05) — first clean Talaria release after the lean refactor.
- **Stability:** dogfood-grade. Used personally; report issues you hit.
- **Tests:** `pytest` currently reports **403 tests** (import-smoke across every first-party module + unit coverage for the agent loop's decision logic, message sanitizers, budget accounting, and config loaders). Integration coverage for the gateway message pipeline is still light — see [`docs/REFACTOR-ROADMAP.md`](docs/REFACTOR-ROADMAP.md) for the test gaps that block the deeper monolith splits. Contributions welcome.

---

## License

[MIT](LICENSE). Forked from [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), itself MIT-licensed.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports + feature requests at [GitHub Issues](https://github.com/Jeonhui/Talaria-Agent/issues).
