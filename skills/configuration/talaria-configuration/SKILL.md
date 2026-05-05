---
name: talaria-configuration
description: "Configure Talaria — model, API keys, bot tokens, identity, branding, and how the running gateway picks up the change."
version: 1.0.0
metadata:
  talaria:
    tags: [configuration, setup, model, api-key, bot-token, gateway, branding, skin, identity]
---

# Talaria Configuration

Use this skill when the user asks to **change anything about how
Talaria itself runs** — the chat model, an API key, a messaging-bot
token, their displayed name, the visual theme, or any other setting
in `~/.talaria/`.

The agent's role is to:

1. Identify what the user actually wants to change.
2. Pick the right CLI command (preferred) or the right file (only when
   no CLI exists).
3. After the change, make sure the running gateway picks it up — the
   setup CLI now does this automatically, but a direct file edit does
   not.

---

## Configuration files

All configuration lives in `~/.talaria/`:

| File | Purpose | Edit how |
|------|---------|----------|
| `config.yaml` | Model selection, gateway, display, agent, user, skills | `talaria setup` / `talaria model` / direct edit |
| `.env` | API keys + secrets (`OPENAI_API_KEY`, `XIAOMI_API_KEY`, `DISCORD_BOT_TOKEN`, …) | `talaria setup` / direct edit |
| `auth.json` | OAuth tokens (Codex, Spotify, Qwen, …) | `talaria auth login <provider>` |
| `SOUL.md` | Agent persona prompt (seeded once, then user-editable) | text editor |
| `skins/<name>.yaml` | Optional user-installed visual themes | text editor |

---

## Common changes

### Change the chat model

```bash
talaria model
```

Interactive picker: select a provider, enter/confirm an API key, pick a
model. Saves to `model.provider` + `model.default` in `config.yaml`.

To inspect the current selection without changing it:
```bash
talaria status
```

### Rotate or replace an API key

```bash
talaria setup model
```

When a key is already saved, the wizard now asks
**`Replace key? [y/N]`**. Press `y` to enter a new value, or
Enter / `n` / Ctrl-C to keep the existing key untouched.

Direct edit alternative — open `~/.talaria/.env` and replace the
relevant line:
```
XIAOMI_API_KEY=tp-newvaluehere
```

The provider env-var keys are well-known: `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `XIAOMI_API_KEY`, `OPENROUTER_API_KEY`,
`LM_API_KEY`, `GITHUB_TOKEN`, `ELEVENLABS_API_KEY`.

### Set or change a messaging-bot token

```bash
talaria setup gateway
```

The wizard walks through Telegram / Discord / Slack one by one and asks
**`Reconfigure <platform>?`** when an existing token is detected. Answer
`y` to enter a new bot token. The bot-token env-var keys are
`DISCORD_BOT_TOKEN`, `TELEGRAM_BOT_TOKEN`, `SLACK_BOT_TOKEN`,
`SLACK_APP_TOKEN`.

> **Bot tokens are restart-required.** Replacing a bot token means the
> gateway must reconnect its websocket — no in-process reload. The
> setup wizard auto-detects this and prompts to restart.

### Set the user's displayed name

```yaml
# ~/.talaria/config.yaml
user:
  display_name: "Johnny"
```

The agent identity prompt becomes:
> You are Talaria Agent, an intelligent AI assistant. … You are working
> with Johnny.

Read with `from talaria_cli.config import get_display_name`.

> The seeded `SOUL.md` only picks up `display_name` on first run. If
> you change `display_name` later and want the seed re-rendered, delete
> `~/.talaria/SOUL.md` and relaunch — back up first if you've edited
> SOUL by hand.

### Change the visual theme (colors, ASCII art, brand strings)

Two paths:

1. **Permanent default** — edit `talaria_cli/branding.py` (fork-level
   change). Holds `BRAND_EMOJI`, `BANNER_LOGO`, `BANNER_HERO_ART`,
   `DEFAULT_COLORS`, `DEFAULT_BRANDING`, `DEFAULT_BANNER_LAYOUT`,
   `DEFAULT_BANNER_CUSTOM_LINES` / `_POSITION`, repo URLs, and the
   spinner faces.

2. **Per-user skin** — drop a YAML file in `~/.talaria/skins/<name>.yaml`:
   ```yaml
   name: ocean
   colors:
     banner_title: "#00BFFF"
   branding:
     brand_emoji: "🌊"
     agent_short_name: "Ocean"
   banner_layout:
     show_skills: false
   ```
   Activate at runtime with `/skin ocean` or persist in
   `config.yaml`:
   ```yaml
   display:
     skin: ocean
   ```

See `CUSTOMIZATION.md` at the repo root for the full guide.

---

## Reload vs. restart — what the running gateway needs

When the user changes something, the gateway has to pick it up. The
``talaria setup`` and ``talaria model`` commands now do this for you —
they snapshot ``.env`` + ``config.yaml`` before and after, then prompt:

```
Gateway is running. Detected changes (env: XIAOMI_API_KEY).
These can be picked up by reloading the gateway.
Reload now? [Y/n]:
```

**Restart-required values (the prompt says "restart"):**
- Bot tokens: `DISCORD_BOT_TOKEN`, `TELEGRAM_BOT_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`
- Webhook secrets / URLs: `TELEGRAM_WEBHOOK_URL`, `TELEGRAM_WEBHOOK_SECRET`
- Allowlists: `DISCORD_ALLOWED_USERS`, `TELEGRAM_ALLOWED_USERS`, `SLACK_ALLOWED_USERS`
- Gateway port / platform on-off: anything under the `gateway` block of `config.yaml`

**Reload-safe values (the prompt says "reload"):**
- API keys (any `*_API_KEY`, `*_BASE_URL`)
- Model selection (`model.*` in `config.yaml`)
- Display / branding (`display.*`, agent, user, skills)

If the user edits a file directly (not via setup), the prompt won't
fire — they need to run `talaria gateway restart` (or `talaria gateway
reload` once that command lands) themselves.

---

## When to suggest setup vs. direct edit

**Prefer the CLI** for anything it covers — wizard validates inputs,
saves to the canonical path, prompts to reload the gateway, and
respects allowed-user lists. Use:

| Goal | Command |
|------|---------|
| Pick or change chat model | `talaria model` |
| Add / rotate API key | `talaria setup model` |
| Configure messaging gateway | `talaria setup gateway` |
| Run an interactive full setup | `talaria setup` |
| View current state | `talaria status` |
| Inspect dependencies / problems | `talaria doctor` |
| List active config | `talaria config get` |

**Direct file edit** is reasonable for:

- Fields the wizard doesn't expose (e.g. `user.display_name`,
  `display.skin`, advanced agent flags in `config.yaml`)
- Bulk changes / scripted setup
- Sensitive value changes the user wants to do once and audit

After a direct edit, remind the user to run
`talaria gateway restart` if the gateway is up — file watching is not
yet wired in, so manual restart is the only path.

---

## Verify after every change

Always confirm the change took effect:

```bash
talaria status        # shows current model, provider, API keys (redacted), gateway state
talaria doctor        # deeper checks: dependency, auth provider reachability
```

If the user asked to test a brand-new API key, also send a one-shot
prompt:

```bash
echo "ping" | talaria
```

A failed call usually surfaces the underlying auth error.

---

## What this skill does NOT cover

- Authoring **new skills** — see `talaria-agent-skill-authoring`
- Webhook subscription management — see `webhook-subscriptions`
- OAuth-based auth flows (Codex, Spotify, Qwen, MiniMax) — use
  `talaria auth login <provider>`
