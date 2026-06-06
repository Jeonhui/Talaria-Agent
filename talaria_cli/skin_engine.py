"""Talaria CLI skin/theme engine.

A data-driven skin system that lets users customize the CLI's visual appearance.
Skins are defined as YAML files in ~/.talaria/skins/ or as built-in presets.
No code changes are needed to add a new skin.

SKIN YAML SCHEMA
================

All fields are optional. Missing values inherit from the ``default`` skin.

.. code-block:: yaml

    # Required: skin identity
    name: mytheme                         # Unique skin name (lowercase, hyphens ok)
    description: Short description        # Shown in /skin listing

    # Colors: hex values for Rich markup (banner, UI, response box)
    colors:
      banner_border: "#CD7F32"            # Panel border color
      banner_title: "#FFD700"             # Panel title text color
      banner_accent: "#FFBF00"            # Section headers (Available Tools, etc.)
      banner_dim: "#B8860B"               # Dim/muted text (separators, labels)
      banner_text: "#FFF8DC"              # Body text (tool names, skill names)
      ui_accent: "#FFBF00"               # General UI accent
      ui_label: "#DAA520"                # UI labels (warm gold; teal clashed w/ default banner gold)
      ui_ok: "#4caf50"                   # Success indicators
      ui_error: "#ef5350"                # Error indicators
      ui_warn: "#ffa726"                 # Warning indicators
      prompt: "#FFF8DC"                  # Prompt text color
      input_rule: "#CD7F32"              # Input area horizontal rule
      response_border: "#FFD700"         # Response box border (ANSI)
      status_bar_bg: "#1a1a2e"           # Status bar background
      status_bar_text: "#C0C0C0"         # Status bar default text
      status_bar_strong: "#FFD700"       # Status bar highlighted text
      status_bar_dim: "#8B8682"          # Status bar separators/muted text
      status_bar_good: "#8FBC8F"         # Healthy context usage
      status_bar_warn: "#FFD700"         # Warning context usage
      status_bar_bad: "#FF8C00"          # High context usage
      status_bar_critical: "#FF6B6B"     # Critical context usage
      session_label: "#DAA520"           # Session label color
      session_border: "#8B8682"          # Session ID dim color
      status_bar_bg: "#1a1a2e"          # TUI status/usage bar background
      completion_menu_bg: "#1a1a2e"      # Completion menu background
      completion_menu_current_bg: "#333355"  # Active completion row background
      completion_menu_meta_bg: "#1a1a2e"     # Completion meta column background
      completion_menu_meta_current_bg: "#333355"  # Active completion meta background

    # Spinner: customize the animated spinner during API calls
    spinner:
      waiting_faces:                      # Faces shown while waiting for API
        - "(⚔)"
        - "(⛨)"
      thinking_faces:                     # Faces shown during reasoning
        - "(⌁)"
        - "(<>)"
      thinking_verbs:                     # Verbs for spinner messages
        - "forging"
        - "plotting"
      wings:                              # Optional left/right spinner decorations
        - ["⟪⚔", "⚔⟫"]                  # Each entry is [left, right] pair
        - ["⟪▲", "▲⟫"]

    # Branding: text strings used throughout the CLI
    branding:
      agent_name: "Talaria Agent"          # Banner title, status display
      welcome: "Welcome message"          # Shown at CLI startup
      goodbye: "Goodbye! 🪽"              # Shown on exit
      response_label: " 🪽 Talaria "       # Response box header label
      prompt_symbol: "❯"                 # Input prompt symbol (bare token; renderers add trailing space)
      help_header: "(^_^)? Commands"      # /help header text

    # Tool prefix: character for tool output lines (default: ┊)
    tool_prefix: "┊"

    # Tool emojis: override the default emoji for any tool (used in spinners & progress)
    tool_emojis:
      terminal: "⚔"           # Override terminal tool emoji
      web_search: "🔮"        # Override web_search tool emoji
      # Any tool not listed here uses its registry default

USAGE
=====

.. code-block:: python

    from talaria_cli.skin_engine import get_active_skin, list_skins, set_active_skin

    skin = get_active_skin()
    print(skin.colors["banner_title"])    # "#FFD700"
    print(skin.get_branding("agent_name"))  # "Talaria Agent"

    set_active_skin("ares")               # Switch to built-in ares skin
    set_active_skin("mytheme")            # Switch to user skin from ~/.talaria/skins/

BUILT-IN SKINS
==============

- ``default`` — Sky Messenger (Hermes crossing the sky, blue/cloud-white)
- ``ares``    — Crimson/bronze war-god theme with custom spinner wings
- ``mono``    — Clean grayscale monochrome
- ``slate``   — Cool blue developer-focused theme
- ``daylight`` — Light background theme with dark text and blue accents
- ``warm-lightmode`` — Warm brown/gold text for light terminal backgrounds

USER SKINS
==========

Drop a YAML file in ``~/.talaria/skins/<name>.yaml`` following the schema above.
Activate with ``/skin <name>`` in the CLI or ``display.skin: <name>`` in config.yaml.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from talaria_constants import get_talaria_home

logger = logging.getLogger(__name__)


# =============================================================================
# Skin data structure
# =============================================================================

@dataclass
class SkinConfig:
    """Complete skin configuration."""
    name: str
    description: str = ""
    colors: Dict[str, str] = field(default_factory=dict)
    spinner: Dict[str, Any] = field(default_factory=dict)
    branding: Dict[str, str] = field(default_factory=dict)
    tool_prefix: str = "┊"
    tool_emojis: Dict[str, str] = field(default_factory=dict)  # per-tool emoji overrides
    banner_logo: str = ""    # Rich-markup ASCII art logo (overrides theme.BANNER_LOGO)
    banner_hero: str = ""    # Rich-markup hero art (overrides theme.BANNER_HERO_ART)
    banner_layout: Dict[str, bool] = field(default_factory=dict)  # which banner sections to show
    banner_custom_lines: List[str] = field(default_factory=list)  # custom Rich-markup lines at top of right column

    def get_color(self, key: str, fallback: str = "") -> str:
        """Get a color value with fallback."""
        return self.colors.get(key, fallback)

    def get_spinner_wings(self) -> List[Tuple[str, str]]:
        """Get spinner wing pairs, or empty list if none."""
        raw = self.spinner.get("wings", [])
        result = []
        for pair in raw:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                result.append((str(pair[0]), str(pair[1])))
        return result

    def get_branding(self, key: str, fallback: str = "") -> str:
        """Get a branding value with fallback."""
        return self.branding.get(key, fallback)


# =============================================================================
# Built-in skin definitions
# =============================================================================

from talaria_cli.branding import ALTERNATE_SKINS, DEFAULT_SKIN

_BUILTIN_SKINS: Dict[str, Dict[str, Any]] = {
    "default": DEFAULT_SKIN,
    **ALTERNATE_SKINS,
}


# =============================================================================
# Skin loading and management
# =============================================================================

_active_skin: Optional[SkinConfig] = None
_active_skin_name: str = "default"


def _skins_dir() -> Path:
    """User skins directory."""
    return get_talaria_home() / "skins"


def _load_skin_from_yaml(path: Path) -> Optional[Dict[str, Any]]:
    """Load a skin definition from a YAML file."""
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and "name" in data:
            return data
    except Exception as e:
        logger.debug("Failed to load skin from %s: %s", path, e)
    return None


def _build_skin_config(data: Dict[str, Any]) -> SkinConfig:
    """Build a SkinConfig from a raw dict (built-in or loaded from YAML)."""
    # Start with default values as base for missing keys
    default = _BUILTIN_SKINS["default"]
    colors = dict(default.get("colors", {}))
    colors.update(data.get("colors", {}))
    spinner = dict(default.get("spinner", {}))
    spinner.update(data.get("spinner", {}))
    branding = dict(default.get("branding", {}))
    branding.update(data.get("branding", {}))
    banner_layout = dict(default.get("banner_layout", {}))
    banner_layout.update(data.get("banner_layout", {}))
    custom_lines = data.get("banner_custom_lines",
                            default.get("banner_custom_lines", []))
    if not isinstance(custom_lines, list):
        custom_lines = []

    return SkinConfig(
        name=data.get("name", "unknown"),
        description=data.get("description", ""),
        colors=colors,
        spinner=spinner,
        branding=branding,
        tool_prefix=data.get("tool_prefix", default.get("tool_prefix", "┊")),
        tool_emojis=data.get("tool_emojis", {}),
        banner_logo=data.get("banner_logo", ""),
        banner_hero=data.get("banner_hero", ""),
        banner_layout=banner_layout,
        banner_custom_lines=list(custom_lines),
    )


def list_skins() -> List[Dict[str, str]]:
    """List all available skins (built-in + user-installed).

    Returns list of {"name": ..., "description": ..., "source": "builtin"|"user"}.
    """
    result = []
    for name, data in _BUILTIN_SKINS.items():
        result.append({
            "name": name,
            "description": data.get("description", ""),
            "source": "builtin",
        })

    skins_path = _skins_dir()
    if skins_path.is_dir():
        for f in sorted(skins_path.glob("*.yaml")):
            data = _load_skin_from_yaml(f)
            if data:
                skin_name = data.get("name", f.stem)
                # Skip if it shadows a built-in
                if any(s["name"] == skin_name for s in result):
                    continue
                result.append({
                    "name": skin_name,
                    "description": data.get("description", ""),
                    "source": "user",
                })

    return result


def load_skin(name: str) -> SkinConfig:
    """Load a skin by name. Checks user skins first, then built-in."""
    # Check user skins directory
    skins_path = _skins_dir()
    user_file = skins_path / f"{name}.yaml"
    if user_file.is_file():
        data = _load_skin_from_yaml(user_file)
        if data:
            return _build_skin_config(data)

    # Check built-in skins
    if name in _BUILTIN_SKINS:
        return _build_skin_config(_BUILTIN_SKINS[name])

    # Fallback to default
    logger.warning("Skin '%s' not found, using default", name)
    return _build_skin_config(_BUILTIN_SKINS["default"])


def get_active_skin() -> SkinConfig:
    """Get the currently active skin config (cached)."""
    global _active_skin
    if _active_skin is None:
        _active_skin = load_skin(_active_skin_name)
    return _active_skin


def set_active_skin(name: str) -> SkinConfig:
    """Switch the active skin. Returns the new SkinConfig."""
    global _active_skin, _active_skin_name
    _active_skin_name = name
    _active_skin = load_skin(name)
    return _active_skin


def get_active_skin_name() -> str:
    """Get the name of the currently active skin."""
    return _active_skin_name


def init_skin_from_config(config: dict) -> None:
    """Initialize the active skin from CLI config at startup.

    Call this once during CLI init with the loaded config dict.
    """
    display = config.get("display") or {}
    if not isinstance(display, dict):
        display = {}
    skin_name = display.get("skin", "default")
    if isinstance(skin_name, str) and skin_name.strip():
        set_active_skin(skin_name.strip())
    else:
        set_active_skin("default")


# =============================================================================
# Convenience helpers for CLI modules
# =============================================================================


def get_active_prompt_symbol(fallback: str = "❯") -> str:
    """Return the interactive prompt symbol with a single trailing space.

    Skins store ``prompt_symbol`` as a bare token (no spaces). The trailing
    space is appended here so callers can drop it straight into a rendered
    prompt without hand-rolling whitespace.
    """
    try:
        raw = get_active_skin().get_branding("prompt_symbol", fallback)
    except Exception:
        raw = fallback

    cleaned = (raw or fallback).strip()

    return f"{cleaned or fallback.strip()} "



def get_active_help_header(fallback: str = "(^_^)? Available Commands") -> str:
    """Get the /help header from the active skin."""
    try:
        return get_active_skin().get_branding("help_header", fallback)
    except Exception:
        return fallback



def get_active_goodbye(fallback: str = "Goodbye! 🪽") -> str:
    """Get the goodbye line from the active skin."""
    try:
        return get_active_skin().get_branding("goodbye", fallback)
    except Exception:
        return fallback


def get_active_brand_emoji(fallback: str = "🪽") -> str:
    """Get the brand emoji from the active skin (theme-driven default)."""
    from talaria_cli.branding import default_branding
    resolved = fallback or default_branding("brand_emoji", "🪽")
    try:
        return get_active_skin().get_branding("brand_emoji", resolved)
    except Exception:
        return resolved


def get_active_agent_name(fallback: str = "Talaria Agent") -> str:
    """Full agent name from the active skin (theme-driven default)."""
    from talaria_cli.branding import default_branding
    resolved = default_branding("agent_name", fallback)
    try:
        return get_active_skin().get_branding("agent_name", resolved)
    except Exception:
        return resolved


def banner_layout_enabled(key: str, fallback: bool = True) -> bool:
    """Whether the given banner section should render (theme/skin driven)."""
    from talaria_cli.branding import banner_layout as _theme_layout
    resolved = _theme_layout(key, fallback)
    try:
        skin_layout = get_active_skin().banner_layout or {}
        if key in skin_layout:
            return bool(skin_layout[key])
    except Exception:
        pass
    return resolved


def get_active_agent_short_name(fallback: str = "Talaria") -> str:
    """Short agent name (first word) from the active skin."""
    from talaria_cli.branding import default_branding
    resolved = default_branding("agent_short_name", fallback)
    try:
        return get_active_skin().get_branding("agent_short_name", resolved)
    except Exception:
        return resolved



def get_prompt_toolkit_style_overrides() -> Dict[str, str]:
    """Return prompt_toolkit style overrides derived from the active skin.

    These are layered on top of the CLI's base TUI style so /skin can refresh
    the live prompt_toolkit UI immediately without rebuilding the app.
    """
    try:
        skin = get_active_skin()
    except Exception:
        return {}

    prompt = skin.get_color("prompt", "#FFF8DC")
    input_rule = skin.get_color("input_rule", "#CD7F32")
    title = skin.get_color("banner_title", "#FFD700")
    text = skin.get_color("banner_text", prompt)
    dim = skin.get_color("banner_dim", "#555555")
    label = skin.get_color("ui_label", title)
    warn = skin.get_color("ui_warn", "#FF8C00")
    error = skin.get_color("ui_error", "#FF6B6B")
    status_bg = skin.get_color("status_bar_bg", "#1a1a2e")
    status_text = skin.get_color("status_bar_text", text)
    status_strong = skin.get_color("status_bar_strong", title)
    status_dim = skin.get_color("status_bar_dim", dim)
    status_good = skin.get_color("status_bar_good", skin.get_color("ui_ok", "#8FBC8F"))
    status_warn = skin.get_color("status_bar_warn", warn)
    status_bad = skin.get_color("status_bar_bad", skin.get_color("banner_accent", warn))
    status_critical = skin.get_color("status_bar_critical", error)
    menu_bg = skin.get_color("completion_menu_bg", "#1a1a2e")
    menu_current_bg = skin.get_color("completion_menu_current_bg", "#333355")
    menu_meta_bg = skin.get_color("completion_menu_meta_bg", menu_bg)
    menu_meta_current_bg = skin.get_color("completion_menu_meta_current_bg", menu_current_bg)

    return {
        "input-area": prompt,
        "placeholder": f"{dim} italic",
        "prompt": prompt,
        "prompt-working": f"{dim} italic",
        "hint": f"{dim} italic",
        "status-bar": f"bg:{status_bg} {status_text}",
        "status-bar-strong": f"bg:{status_bg} {status_strong} bold",
        "status-bar-dim": f"bg:{status_bg} {status_dim}",
        "status-bar-good": f"bg:{status_bg} {status_good} bold",
        "status-bar-warn": f"bg:{status_bg} {status_warn} bold",
        "status-bar-bad": f"bg:{status_bg} {status_bad} bold",
        "status-bar-critical": f"bg:{status_bg} {status_critical} bold",
        "input-rule": input_rule,
        "image-badge": f"{label} bold",
        "completion-menu": f"bg:{menu_bg} {text}",
        "completion-menu.completion": f"bg:{menu_bg} {text}",
        "completion-menu.completion.current": f"bg:{menu_current_bg} {title}",
        "completion-menu.meta.completion": f"bg:{menu_meta_bg} {dim}",
        "completion-menu.meta.completion.current": f"bg:{menu_meta_current_bg} {label}",
        "clarify-border": input_rule,
        "clarify-title": f"{title} bold",
        "clarify-question": f"{text} bold",
        "clarify-choice": dim,
        "clarify-selected": f"{title} bold",
        "clarify-active-other": f"{title} italic",
        "clarify-countdown": input_rule,
        "sudo-prompt": f"{error} bold",
        "sudo-border": input_rule,
        "sudo-title": f"{error} bold",
        "sudo-text": text,
        "approval-border": input_rule,
        "approval-title": f"{warn} bold",
        "approval-desc": f"{text} bold",
        "approval-cmd": f"{dim} italic",
        "approval-choice": dim,
        "approval-selected": f"{title} bold",
    }
