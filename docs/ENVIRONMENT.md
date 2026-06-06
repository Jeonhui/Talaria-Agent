# Environment variables

Reference for every `TALARIA_*` environment variable Talaria reads.
Grouped by area so you can find what to set without grepping the source.

> Most users never need to touch any of these. `talaria setup` writes a
> working config and `.env` for you. Reach for these only when overriding
> a default for an unusual deployment (Termux, custom proxy, sandbox host).

Secrets — API keys, bot tokens — live in `~/.talaria/.env`, not here.
Provider keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.) use their
upstream-standard names and are not listed below.

---

## Paths & runtime

| Variable | Purpose |
|---|---|
| `TALARIA_HOME` | Override the default `~/.talaria` data directory. |
| `TALARIA_HOME_MODE` | Permission mode for the home directory (e.g. `0700`). |
| `TALARIA_DIRS` | Additional search paths (`:` separated). |
| `TALARIA_ENV_PATH` | Custom `.env` file location. |
| `TALARIA_OVERLAYS` | Extra config-overlay files merged on top of `config.yaml`. |
| `TALARIA_DEV` | Mark this process as a development build. |
| `TALARIA_CONTAINER` | Tells Talaria it is running inside its own Docker image. |
| `TALARIA_MANAGED` | Hints that a supervisor / systemd unit owns the process. |
| `TALARIA_USER_AGENT` | Override the outbound HTTP `User-Agent` string. |
| `TALARIA_TIMEZONE` | Pin a timezone (rare; usually inherited from system). |
| `TALARIA_VERSION` / `TALARIA_REVISION` | Forced version/revision string for builds. |

## Model & provider

| Variable | Purpose |
|---|---|
| `TALARIA_INFERENCE_PROVIDER` | Override `model.provider` from config.yaml. |
| `TALARIA_INFERENCE_MODEL` | Override `model.default`. |
| `TALARIA_PROVIDER_CLS` | Force a specific provider transport class. |
| `TALARIA_PROVIDER_ENV_BLOCKLIST` | Comma list of env vars to strip from provider context. |
| `TALARIA_PROVIDER_ENV_FORCE_PREFIX` | Force a prefix on provider-bound env vars. |
| `TALARIA_CODEX_BASE_URL` | Custom Codex inference endpoint. |
| `TALARIA_CODEX_REFRESH_TIMEOUT_SECONDS` | Codex OAuth refresh timeout. |
| `TALARIA_QWEN_BASE_URL` | Custom Qwen inference endpoint. |
| `TALARIA_COPILOT_ACP_COMMAND` / `TALARIA_COPILOT_ACP_ARGS` | Path + args for the Copilot ACP bridge. |
| `TALARIA_OAUTH_FILE` | Override OAuth-state file path. |
| `TALARIA_OAUTH_TRACE` | Verbose OAuth refresh logging. |
| `TALARIA_CA_BUNDLE` | Custom CA bundle for HTTPS calls. |
| `TALARIA_ALLOW_PRIVATE_URLS` | Permit requests to RFC1918 / loopback URLs. |

## Agent loop

| Variable | Purpose |
|---|---|
| `TALARIA_MAX_ITERATIONS` | Hard cap on agent turns per request. |
| `TALARIA_AGENT_TIMEOUT` | Overall agent-run timeout (seconds). |
| `TALARIA_AGENT_TIMEOUT_WARNING` | When to emit the timeout-approaching warning. |
| `TALARIA_AGENT_NOTIFY_INTERVAL` | Interval between progress notifications. |
| `TALARIA_AGENT_HELP_GUIDANCE` | Switch the in-context help guidance style. |
| `TALARIA_AUTO_CONTINUE_FRESHNESS` | Window (seconds) where `--continue` auto-resumes. |
| `TALARIA_BACKGROUND_NOTIFICATIONS` | Toggle background notifications. |
| `TALARIA_EPHEMERAL_SYSTEM_PROMPT` | One-shot system-prompt override (cleared after use). |
| `TALARIA_PREFILL_MESSAGES_FILE` | Path to a message log to prefill the session with. |

## Tools

| Variable | Purpose |
|---|---|
| `TALARIA_CORE_TOOLS` | Comma list overriding the default core toolset. |
| `TALARIA_TOOL_PROGRESS` / `TALARIA_TOOL_PROGRESS_MODE` | Toggle / configure tool progress UI. |
| `TALARIA_EXEC_ASK` | Default approval policy for shell exec (`once`/`session`/`always`). |
| `TALARIA_ACCEPT_HOOKS` | Trust on-disk shell hooks without prompting. |
| `TALARIA_DOCKER_BINARY` | Path to the `docker` (or `podman`) binary. |
| `TALARIA_GIT_BASH_PATH` | Windows: path to Git-bash for shell execution. |
| `TALARIA_WORKDIR` | Working directory for tool execution. |
| `TALARIA_WRITE_SAFE_ROOT` | Restrict edit/write tools to this root. |
| `TALARIA_FORCE_FILE_SYNC` | Force fsync on every tool-side write. |
| `TALARIA_DISABLE_FILE_STATE_GUARD` | Bypass the read-before-edit guard. |
| `TALARIA_CHECKPOINT_TIMEOUT` | Checkpoint creation timeout (seconds). |
| `TALARIA_VISION_DOWNLOAD_TIMEOUT` | Image-download timeout for vision tools. |
| `TALARIA_YOLO_MODE` | Skip every approval prompt (use with care). |
| `TALARIA_MD_NAMES` | Custom names for hierarchical `AGENTS.md` files. |

## Skills & plugins

| Variable | Purpose |
|---|---|
| `TALARIA_BUNDLED_SKILLS` / `TALARIA_BUNDLED_PLUGINS` | Override the bundled lists. |
| `TALARIA_SKILL_DIR` | Extra search path for skills. |
| `TALARIA_SKILLS_INDEX_URL` | Custom skills-registry index. |
| `TALARIA_ENABLE_PROJECT_PLUGINS` | Allow loading plugins from the current project. |
| `TALARIA_INDEX_URL` / `TALARIA_INDEX_CACHE_FILE` / `TALARIA_INDEX_TTL` | Generic registry index URL, cache, and TTL. |

## Gateway & messaging platforms

| Variable | Purpose |
|---|---|
| `TALARIA_GATEWAY_PORT` | Port the gateway service binds. |
| `TALARIA_GATEWAY_LOCK_DIR` | Where the gateway writes lock/pid files. |
| `TALARIA_GATEWAY_SESSION` | Override session id for the running gateway. |
| `TALARIA_GATEWAY_BUSY_INPUT_MODE` | Behavior when input arrives while the agent is busy. |
| `TALARIA_GATEWAY_PLATFORM_CONNECT_TIMEOUT` | Per-platform connect timeout. |
| `TALARIA_PLATFORM` | Force-tag the current platform (`telegram` / `discord` / `slack`). |
| `TALARIA_TELEGRAM_HTTP_CONNECT_TIMEOUT` | Telegram HTTP connect timeout. |
| `TALARIA_TELEGRAM_HTTP_READ_TIMEOUT` | Read timeout. |
| `TALARIA_TELEGRAM_HTTP_WRITE_TIMEOUT` | Write timeout. |
| `TALARIA_TELEGRAM_HTTP_POOL_SIZE` / `TALARIA_TELEGRAM_HTTP_POOL_TIMEOUT` | HTTP pool tuning. |
| `TALARIA_TELEGRAM_DISABLE_FALLBACK_IPS` | Disable fallback IP set for Telegram API. |
| `TALARIA_TELEGRAM_FOLLOWUP_GRACE_SECONDS` | Window where a follow-up replaces the prior. |
| `TALARIA_TELEGRAM_TEXT_BATCH_DELAY_SECONDS` / `_SPLIT_DELAY_SECONDS` | Text batching cadence. |
| `TALARIA_TELEGRAM_MEDIA_BATCH_DELAY_SECONDS` | Media batching cadence. |
| `TALARIA_DISCORD_TEXT_BATCH_DELAY_SECONDS` / `_SPLIT_DELAY_SECONDS` | Discord text batching cadence. |
| `TALARIA_HUMAN_DELAY_MODE` | `off` / `static` / `dynamic`. |
| `TALARIA_HUMAN_DELAY_MIN_MS` / `_MAX_MS` | Bounds for the inter-message human-delay. |
| `TALARIA_SPINNER_PAUSE` | Pause the spinner animation. |

## Sessions

Every variable here scopes Talaria to one logical conversation; the gateway
sets these per inbound message so the agent knows where to reply.

| Variable | Purpose |
|---|---|
| `TALARIA_SESSION_ID` | Session id (used for persistence + log routing). |
| `TALARIA_SESSION_KEY` | Stable per-user session key. |
| `TALARIA_SESSION_PLATFORM` | Source platform (`telegram` / `discord` / `slack` / `cli` / `acp`). |
| `TALARIA_SESSION_SOURCE` | Free-form source label. |
| `TALARIA_SESSION_USER_ID` / `_USER_NAME` | Sender identity. |
| `TALARIA_SESSION_CHAT_ID` / `_CHAT_NAME` | Channel / chat identifier. |
| `TALARIA_SESSION_THREAD_ID` | Thread / topic id (where applicable). |

## Cron

| Variable | Purpose |
|---|---|
| `TALARIA_CRON_MAX_PARALLEL` | Concurrent cron-run cap. |
| `TALARIA_CRON_SESSION` | Session id used by cron-triggered runs. |
| `TALARIA_CRON_AUTO_DELIVER_PLATFORM` | Platform to deliver cron output to. |
| `TALARIA_CRON_AUTO_DELIVER_CHAT_ID` / `_THREAD_ID` | Destination chat / thread id. |

## Spotify (skill)

| Variable | Purpose |
|---|---|
| `TALARIA_SPOTIFY_CLIENT_ID` | Spotify OAuth client id. |
| `TALARIA_SPOTIFY_REDIRECT_URI` | OAuth redirect URI. |
| `TALARIA_SPOTIFY_ACCOUNTS_BASE_URL` / `TALARIA_SPOTIFY_API_BASE_URL` | API overrides (rarely changed). |

## RPC / IPC

These are internal — set automatically when Talaria fans out subprocesses.
Override only when integrating into a non-standard supervisor.

| Variable | Purpose |
|---|---|
| `TALARIA_RPC_DIR` / `TALARIA_RPC_SOCKET` | RPC working dir + socket path. |
| `TALARIA_PERSIST_EOF` | EOF marker for persistent stdin streams. |

## Privacy / safety

| Variable | Purpose |
|---|---|
| `TALARIA_REDACT_SECRETS` | Enable the redacting log formatter (opt-in). |

## Debug / dev

| Variable | Purpose |
|---|---|
| `TALARIA_DEBUG_INTERRUPT` | Verbose tracing for the interrupt subsystem. |
| `TALARIA_INTERACTIVE` | Force interactive vs non-interactive mode. |
| `TALARIA_QUIET` | Suppress non-essential CLI output. |
| `TALARIA_SUBCOMMANDS` | Override the available CLI subcommand list (testing). |
| `TALARIA_SKIP_CHMOD` | Skip `chmod` on files Talaria writes (Termux). |
| `TALARIA_RESTART_DRAIN_TIMEOUT` | How long restart waits for in-flight work. |

---

If you need an exhaustive walk-through of every config key as well, see
`cli-config.yaml.full.example` next to this file.
