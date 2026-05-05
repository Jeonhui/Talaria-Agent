"""Default theme assets — ASCII art and color palette.

Holds the default Talaria branding (logo, hero art, hex colors) used by the
banner and any other module that needs a fallback when no skin is active.
Skins in ``skin_engine.py`` may override any of these.
"""

# =========================================================================
# ASCII Art
# =========================================================================

# Full-width TALARIA-AGENT block logo (requires ~95 char terminal)
BANNER_LOGO = """[bold #FFD700]
████████╗ █████╗ ██╗      █████╗ ██████╗ ██╗ █████╗
╚══██╔══╝██╔══██╗██║     ██╔══██╗██╔══██╗██║██╔══██╗
   ██║   ███████║██║     ███████║██████╔╝██║███████║
   ██║   ██╔══██║██║     ██╔══██║██╔══██╗██║██╔══██║
   ██║   ██║  ██║███████╗██║  ██║██║  ██║██║██║  ██║
   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝╚═╝  ╚═╝
[/]"""

# Banner hero art (left column of the welcome panel).
#
# Recommended size: ~28 chars wide × ~10 lines tall. Rich markup tags
# don't count toward visible width. Each line picks its own color so a
# vertical gradient is easy. Use Braille blocks (⢀⣀⠀⣄⣆⣇ etc.) for
# smooth diagonal strokes.
BANNER_HERO_ART = """
[#FFD700]                       ░████████ [/]
[#FFD700]               ████████████████  [/]
[#FFD700]         █████████████████░      [/]
[#FFBF00]     ███████████████████████████ [/]
[#FFBF00]  ░███████████████████████████   [/]
[#FFBF00] ███████████████████░            [/]
[#CD7F32] ███████████████████████████████ [/]
[#CD7F32] █████████████████████████████   [/]
[#CD7F32]  █████████████████████          [/]
[#B8860B]   ██████████████████████        [/]
[#B8860B]    ███████░                     [/]
"""


# =========================================================================
# Default color palette (hex)
#
# Used as fallback when no skin is active or a key is missing from the
# active skin. Mirrors the ``default`` skin in ``skin_engine.py``.
# =========================================================================

DEFAULT_COLORS = {
    "banner_border": "#CD7F32",
    "banner_title": "#FFD700",
    "banner_accent": "#FFBF00",
    "banner_dim": "#B8860B",
    "banner_text": "#FFF8DC",
    "ui_accent": "#FFBF00",
    "ui_label": "#DAA520",
    "ui_ok": "#4caf50",
    "ui_error": "#ef5350",
    "ui_warn": "#ffa726",
    "prompt": "#FFF8DC",
    "input_rule": "#CD7F32",
    "response_border": "#FFD700",
    "status_bar_bg": "#1a1a2e",
    "session_label": "#DAA520",
    "session_border": "#8B8682",
}


def default_color(key: str, fallback: str = "") -> str:
    """Return the default hex color for ``key``, or ``fallback`` if unknown."""
    return DEFAULT_COLORS.get(key, fallback)


# =========================================================================
# Default branding strings
#
# Used as fallback when no skin is active or a key is missing from the
# active skin's ``branding`` block. Mirrors the ``default`` skin in
# ``skin_engine.py``.
# =========================================================================

BRAND_EMOJI = "🪽"

DEFAULT_BRANDING = {
    "brand_emoji": BRAND_EMOJI,
    "agent_name": "Talaria Agent",
    "agent_short_name": "Talaria",
    "welcome": "Welcome to Talaria Agent! Type your message or /help for commands.",
    "goodbye": f"Goodbye! {BRAND_EMOJI}",
    "response_label": f" {BRAND_EMOJI} Talaria ",
    "prompt_symbol": "❯",
    "help_header": "(^_^)? Available Commands",
}


def default_branding(key: str, fallback: str = "") -> str:
    """Return the default branding string for ``key``, or ``fallback`` if unknown."""
    return DEFAULT_BRANDING.get(key, fallback)


# =========================================================================
# Agent identity prompt
#
# Single source for the "You are <Agent>, an intelligent..." identity
# blurb. Used by:
#
# - ``talaria_cli/default_soul.py`` — seeds ``~/.talaria/SOUL.md`` on first
#   run.
# - ``agent/prompt_builder.py`` — injected into the system prompt every
#   turn (with the live user display name when available).
#
# Edit the body below or override ``agent_name`` in ``DEFAULT_BRANDING``.
# =========================================================================


def build_agent_identity(user_name: str = "") -> str:
    """Render the agent identity prompt.

    When ``user_name`` is provided, appends "You are working with
    <name>." so the agent knows who it's talking to. Pulls the agent's
    own name from ``DEFAULT_BRANDING``.
    """
    agent = DEFAULT_BRANDING.get("agent_name", "Talaria Agent")
    body = (
        f"You are {agent}, an intelligent AI assistant. "
        "You are helpful, knowledgeable, and direct. You assist users with a wide "
        "range of tasks including answering questions, writing and editing code, "
        "analyzing information, creative work, and executing actions via your tools. "
        "You communicate clearly, admit uncertainty when appropriate, and prioritize "
        "being genuinely useful over being verbose unless otherwise directed below. "
        "Be targeted and efficient in your exploration and investigations."
    )
    if user_name:
        body += f" You are working with {user_name}."
    return body


DEFAULT_AGENT_IDENTITY = build_agent_identity()


# =========================================================================
# Default skin descriptor
#
# Single source of truth for the ``default`` built-in skin. ``skin_engine``
# imports this rather than redeclaring the same colors/branding so a fork
# only needs to edit ``theme.py`` to rebrand the entire CLI.
# =========================================================================

# =========================================================================
# Banner layout — toggle which sections appear in the welcome banner.
#
# Edit these flags (or override per-skin via ``banner_layout:`` YAML block)
# to hide sections you don't want at startup. ``False`` removes the section
# entirely; ``True`` keeps it.
# =========================================================================

DEFAULT_BANNER_LAYOUT = {
    "show_logo": True,           # ASCII block logo above panel (≥95 col terms)
    "show_hero_art": True,       # winged-sandal art in left column
    "show_model": True,          # model name + context length line
    "show_cwd": True,            # current working directory line
    "show_session_id": True,     # "Session: <id>" line
    "show_custom": True,         # custom user lines at the top of the right column
    "show_tools": True,          # "Available Tools" section
    "show_mcp_servers": True,    # "MCP Servers" section (only if any configured)
    "show_skills": True,         # "Available Skills" section
    "show_profile": True,        # "Profile: <name>" line (only if non-default)
    "show_summary": True,        # "N tools · N skills · /help..." footer
    "show_update_warning": True, # "⚠ N commits behind" line
}


# =========================================================================
# Custom banner lines — your own text at the top of the right column.
#
# Each entry is one line. Rich markup supported (e.g. "[bold #FFD700]hi[/]").
# Empty list = no custom block. Skin YAML may override via
# ``banner_custom_lines:`` key.
# =========================================================================

DEFAULT_BANNER_CUSTOM_LINES: list = []


def banner_layout(key: str, fallback: bool = True) -> bool:
    """Return whether ``key`` banner section should render (theme default)."""
    return bool(DEFAULT_BANNER_LAYOUT.get(key, fallback))


DEFAULT_SKIN_NAME = "default"
DEFAULT_SKIN_DESCRIPTION = "Classic Talaria — gold and kawaii"
DEFAULT_TOOL_PREFIX = "┊"

DEFAULT_SKIN = {
    "name": DEFAULT_SKIN_NAME,
    "description": DEFAULT_SKIN_DESCRIPTION,
    "colors": dict(DEFAULT_COLORS),
    "spinner": {},
    "branding": dict(DEFAULT_BRANDING),
    "tool_prefix": DEFAULT_TOOL_PREFIX,
    "banner_logo": BANNER_LOGO,
    "banner_hero": BANNER_HERO_ART,
    "banner_layout": dict(DEFAULT_BANNER_LAYOUT),
    "banner_custom_lines": list(DEFAULT_BANNER_CUSTOM_LINES),
}


# =========================================================================
# Spinner defaults
#
# Faces, verbs, and motion patterns shown during agent activity. Skins may
# override via the ``spinner:`` YAML block.
# =========================================================================

DEFAULT_WAITING_FACES = [
    "(｡◕‿◕｡)", "(◕‿◕✿)", "٩(◕‿◕｡)۶", "(✿◠‿◠)", "( ˘▽˘)っ",
    "♪(´ε` )", "(◕ᴗ◕✿)", "ヾ(＾∇＾)", "(≧◡≦)", "(★ω★)",
]

DEFAULT_THINKING_FACES = [
    "(｡•́︿•̀｡)", "(◔_◔)", "(¬‿¬)", "( •_•)>⌐■-■", "(⌐■_■)",
    "(´･_･`)", "◉_◉", "(°ロ°)", "( ˘⌣˘)♡", "ヽ(>∀<☆)☆",
    "٩(๑❛ᴗ❛๑)۶", "(⊙_⊙)", "(¬_¬)", "( ͡° ͜ʖ ͡°)", "ಠ_ಠ",
]

DEFAULT_THINKING_VERBS = [
    "pondering", "contemplating", "musing", "cogitating", "ruminating",
    "deliberating", "mulling", "reflecting", "processing", "reasoning",
    "analyzing", "computing", "synthesizing", "formulating", "brainstorming",
]


# =========================================================================
# Repository / release URLs
#
# Fork users: replace these four constants with your own GitHub URLs.
# Code that needs them imports from here so a single edit re-points the
# update checker, release links, and clone instructions.
# =========================================================================

DEFAULT_REPO_URL = "https://github.com/Jeonhui/Talaria-Agent.git"
DEFAULT_REPO_HTTPS_URL = "https://github.com/Jeonhui/Talaria-Agent"
DEFAULT_RELEASE_URL_BASE = "https://github.com/Jeonhui/Talaria-Agent/releases/tag"
DEFAULT_INSTALL_SCRIPT_URL = (
    "https://raw.githubusercontent.com/Jeonhui/Talaria-Agent/main/scripts/install.sh"
)


# =========================================================================
# Alternate built-in skins
#
# Move additional themes here so a fork can rebrand or remove them in one
# place. ``skin_engine`` reads ``ALTERNATE_SKINS`` and merges with the
# default skin to populate ``_BUILTIN_SKINS``.
# =========================================================================

ALTERNATE_SKINS = {
    "mono": {
        "name": "mono",
        "description": "Monochrome — clean grayscale",
        "colors": {
            "banner_border": "#555555",
            "banner_title": "#e6edf3",
            "banner_accent": "#aaaaaa",
            "banner_dim": "#444444",
            "banner_text": "#c9d1d9",
            "ui_accent": "#aaaaaa",
            "ui_label": "#888888",
            "ui_ok": "#888888",
            "ui_error": "#cccccc",
            "ui_warn": "#999999",
            "prompt": "#c9d1d9",
            "input_rule": "#444444",
            "response_border": "#aaaaaa",
            "status_bar_bg": "#1F1F1F",
            "status_bar_text": "#C9D1D9",
            "status_bar_strong": "#E6EDF3",
            "status_bar_dim": "#777777",
            "status_bar_good": "#B5B5B5",
            "status_bar_warn": "#AAAAAA",
            "status_bar_bad": "#D0D0D0",
            "status_bar_critical": "#F0F0F0",
            "session_label": "#888888",
            "session_border": "#555555",
        },
        "spinner": {},
        "branding": {
            "agent_name": DEFAULT_BRANDING["agent_name"],
            "agent_short_name": DEFAULT_BRANDING["agent_short_name"],
            "welcome": DEFAULT_BRANDING["welcome"],
            "goodbye": DEFAULT_BRANDING["goodbye"],
            "response_label": DEFAULT_BRANDING["response_label"],
            "prompt_symbol": "❯",
            "help_header": "[?] Available Commands",
        },
        "tool_prefix": DEFAULT_TOOL_PREFIX,
    },
}
