"""``talaria skin`` — manage icon / branding / colors from the shell.

Skins already drive every visual string (icon/emoji, agent name, colors,
spinner, prompt symbol) via YAML in ``~/.talaria/skins/`` + ``display.skin``
in config.yaml. This command makes them easy to use without hand-writing
YAML:

    talaria skin list                # show built-in + user skins (★ = active)
    talaria skin show                # print the active skin's branding/colors
    talaria skin set <name>          # activate a skin (persists to config.yaml)
    talaria skin new <name>          # scaffold ~/.talaria/skins/<name>.yaml
    talaria skin new <name> --from mono   # scaffold from an existing skin
    talaria skin path [<name>]       # print the YAML path (for your editor)
"""

from __future__ import annotations

from talaria_cli.colors import Colors, color


def _info(t: str):
    print(color(f"  {t}", Colors.DIM))


def _ok(t: str):
    print(color(f"  ✓ {t}", Colors.GREEN))


def _err(t: str):
    print(color(f"  ✗ {t}", Colors.RED))


def _active_name() -> str:
    """Active skin name from config.yaml (display.skin), default 'default'."""
    try:
        from talaria_cli.config import cfg_get, load_config

        return cfg_get(load_config(), "display", "skin") or "default"
    except Exception:
        return "default"


# ─── list ──────────────────────────────────────────────────────────────────

def cmd_list(args=None):
    from talaria_cli.skin_engine import list_skins

    active = _active_name()
    skins = list_skins()
    if not skins:
        _info("No skins found.")
        return

    print()
    print(color("  Skins:", Colors.CYAN + Colors.BOLD))
    print()
    for s in skins:
        mark = color(" ★ active", Colors.GREEN) if s["name"] == active else ""
        src = color(f"[{s['source']}]", Colors.DIM)
        print(f"    {color(s['name'], Colors.GREEN):26s} {src:18s} {s.get('description', '')}{mark}")
    print()
    _info("Switch:   talaria skin set <name>")
    _info("Create:   talaria skin new <name>")
    print()


# ─── show ──────────────────────────────────────────────────────────────────

def cmd_show(args=None):
    from talaria_cli.skin_engine import load_skin

    name = _active_name()
    skin = load_skin(name)
    print()
    print(color(f"  Active skin: {name}", Colors.CYAN + Colors.BOLD))
    if skin.description:
        _info(skin.description)
    print()
    print(color("  Branding:", Colors.CYAN))
    for key in ("brand_emoji", "agent_name", "response_label", "prompt_symbol",
                "help_header", "goodbye", "welcome"):
        val = skin.get_branding(key, "")
        if val:
            print(f"    {key:16s} {val}")
    print()
    print(color("  Key colors:", Colors.CYAN))
    for key in ("banner_title", "banner_border", "banner_accent", "ui_accent",
                "ui_ok", "ui_error"):
        val = skin.colors.get(key, "")
        if val:
            print(f"    {key:16s} {val}")
    print()


# ─── set ───────────────────────────────────────────────────────────────────

def cmd_set(args):
    from talaria_cli.config import load_config, save_config
    from talaria_cli.skin_engine import list_skins

    name = args.name
    names = {s["name"] for s in list_skins()}
    if name not in names:
        _err(f"Skin '{name}' not found.")
        _info(f"Available: {', '.join(sorted(names))}")
        _info(f"Create one: talaria skin new {name}")
        return

    config = load_config()
    if not isinstance(config.get("display"), dict):
        config["display"] = {}
    config["display"]["skin"] = name
    save_config(config)
    _ok(f"Active skin: {name}")
    _info("Saved to config.yaml (display.skin). Start a new session to see it.")


# ─── new (scaffold) ──────────────────────────────────────────────────────────

def cmd_new(args):
    from talaria_cli.skin_engine import _skins_dir, list_skins, load_skin

    name = args.name.strip().lower()
    if not name or not all(c.isalnum() or c in "-_" for c in name):
        _err("Skin name must be lowercase letters/digits/hyphens.")
        return

    skins_dir = _skins_dir()
    skins_dir.mkdir(parents=True, exist_ok=True)
    path = skins_dir / f"{name}.yaml"
    if path.exists() and not getattr(args, "force", False):
        _err(f"{path} already exists. Use --force to overwrite.")
        return

    base = getattr(args, "from_skin", None) or "default"
    if base not in {s["name"] for s in list_skins()}:
        _err(f"Base skin '{base}' not found.")
        return
    src = load_skin(base)

    path.write_text(_scaffold_yaml(name, src), encoding="utf-8")
    _ok(f"Created {path}")
    _info(f"Edit the icon / name / colors, then: talaria skin set {name}")


def _scaffold_yaml(name: str, src) -> str:
    """Render a commented starter YAML seeded from *src* (a SkinConfig)."""
    emoji = src.get_branding("brand_emoji", "🪽")
    agent_name = src.get_branding("agent_name", "Talaria Agent")
    response_label = src.get_branding("response_label", " 🪽 Talaria ")
    prompt_symbol = src.get_branding("prompt_symbol", "❯")
    c = src.colors
    return f"""# Talaria skin — edit and activate with: talaria skin set {name}
# All fields are optional; missing values inherit from the default skin.
name: {name}
description: My custom {name} theme

# ── Icon & branding (the quick wins) ──────────────────────────────
branding:
  brand_emoji: "{emoji}"            # main icon/emoji used across the UI
  agent_name: "{agent_name}"        # banner title + status line
  response_label: "{response_label}"  # header on the response box
  prompt_symbol: "{prompt_symbol}"  # input prompt symbol
  # welcome: "Welcome!"
  # goodbye: "Bye! {emoji}"
  # help_header: "(^_^)? Commands"

# ── Colors (hex) ──────────────────────────────────────────────────
colors:
  banner_title: "{c.get('banner_title', '#FFD700')}"
  banner_border: "{c.get('banner_border', '#CD7F32')}"
  banner_accent: "{c.get('banner_accent', '#FFBF00')}"
  ui_accent: "{c.get('ui_accent', '#FFBF00')}"
  ui_ok: "{c.get('ui_ok', '#4caf50')}"
  ui_error: "{c.get('ui_error', '#ef5350')}"

# ── Optional: per-tool icons ──────────────────────────────────────
# tool_emojis:
#   terminal: "⚔"
#   web_search: "🔮"
"""


# ─── path ──────────────────────────────────────────────────────────────────

def cmd_path(args):
    from talaria_cli.skin_engine import _skins_dir

    name = getattr(args, "name", None) or _active_name()
    print(_skins_dir() / f"{name}.yaml")


# ─── dispatcher ──────────────────────────────────────────────────────────────

def skin_command(args):
    action = getattr(args, "skin_action", None)
    handlers = {
        "list": cmd_list,
        "ls": cmd_list,
        "show": cmd_show,
        "set": cmd_set,
        "use": cmd_set,
        "new": cmd_new,
        "create": cmd_new,
        "path": cmd_path,
    }
    handler = handlers.get(action)
    if handler:
        handler(args)
    else:
        cmd_list(args)
