# Talaria Agent

> A lean personal assistant for Discord, Slack, Telegram, CLI, and optional MCP.

Talaria is a slim, opinionated fork of [Hermes Agent](https://github.com/NousResearch/hermes-agent) that drops the everything-included framework surface and keeps a small, well-defined assistant. **~71% smaller** than the upstream code base (~29% the original size).

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
  diff against ~186k LOC, not a hunt across ~649k.
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
| **MCP** | Supported. Zero servers shipped — add your own with `talaria mcp add`. |
| **Skills** | Bundled `configuration` + `devops` + `software-development`; install more with `talaria skills install <repo>` |

## What's been removed

Whole subsystems dropped to keep the assistant focused:

- **Voice & TTS** — speech-to-text, text-to-speech, push-to-talk, Discord voice channels, ElevenLabs/Edge/MiniMax/NeuTTS providers
- **Web dashboard & ink/React TUI frontend** — no graphical surface ships
- **Interactive terminal chat** — the `talaria chat` REPL was removed; Talaria now runs headless. Use the messaging gateway (Discord/Slack/Telegram), one-shot `talaria -q "..."`, or the ACP editor integration
- **Aggregator paths** — Vercel AI Gateway and Nous Portal subscription system (OpenRouter is now a first-class provider with its own key)
- **Auth flows** — Nous Portal device-code login, OpenClaw migration, the `talaria login` subcommand
- **Backends** — Modal, Daytona, Singularity sandbox executors (only local / Docker / SSH remain)
- **Messaging** — every platform other than Discord / Slack / Telegram (DingTalk, BlueBubbles, WhatsApp, Matrix, Signal, etc.)
- **Other** — RL/eval harnesses, third-party memory plugins (Honcho / Mem0 / Hindsight), the official `optional-skills/` registry, ~2200 lines of dead provider catalogs and unused detectors

Net: **~186k Python LOC** in **218 files**, down from **~649k LOC** in **1,340 files** in upstream Hermes.

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
agent/               agent loop, prompt builder, transports (anthropic / chat_completions / codex)
tools/               built-in tools: terminal, file, web, browser, memory, todo, vision, MCP, skills
gateway/             messaging gateway (Discord / Slack / Telegram adapters + session store)
plugins/             user-extensible plugin host
cron/                cron scheduler
skills/              bundled skills (devops, software-development)
docker/              Docker entrypoint
scripts/             install / uninstall / build helpers
```

`run_agent.py` (agent core), `talaria_cli/main.py` (CLI commands), and `gateway/run.py` (messaging gateway) are the big entry surfaces; everything else fans out from there.

---

## Status

- **Version:** v0.1.0 (2026-05-05) — first clean Talaria release after the lean refactor.
- **Stability:** dogfood-grade. Used personally; report issues you hit.
- **Tests:** the `tests/` directory currently holds smoke checks only — contributions welcome.

---

## License

[MIT](LICENSE). Forked from [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), itself MIT-licensed.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports + feature requests at [GitHub Issues](https://github.com/Jeonhui/Talaria-Agent/issues).
