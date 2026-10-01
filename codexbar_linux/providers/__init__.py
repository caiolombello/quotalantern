"""AI coding provider modules for codexbar-linux."""

from __future__ import annotations

from .base import CATEGORY_LABELS, ProviderSpec
from .claude import fetch_claude_usage
from .claude_api import fetch_claude_api_usage
from .codex import fetch_codex_usage
from .cursor import fetch_cursor_usage
from .gemini import fetch_gemini_usage
from .gemini_api import fetch_gemini_api_usage
from .glm import fetch_glm_usage
from .grok import fetch_grok_usage
from .kiro import fetch_kiro_usage
from .openai_api import fetch_openai_api_usage
from .opencode_go import fetch_opencode_go_usage
from .zen import fetch_zen_usage

PROVIDER_SPECS: list[ProviderSpec] = [
    ProviderSpec(
        id="codex",
        display_name="Codex",
        category="subscription",
        open_url="https://chatgpt.com/codex/settings/usage",
        auth_hint="Needs ~/.codex/auth.json (Codex CLI login)",
        default_enabled=True,
        fetch=fetch_codex_usage,
    ),
    ProviderSpec(
        id="claude",
        display_name="Claude",
        category="subscription",
        open_url="https://claude.ai/settings/usage",
        auth_hint="Optional — run claude auth login, or disable in Settings",
        default_enabled=True,
        fetch=fetch_claude_usage,
    ),
    ProviderSpec(
        id="gemini",
        display_name="Gemini",
        category="subscription",
        open_url="https://aistudio.google.com/",
        auth_hint="Sign in via Antigravity CLI (agy) or ~/.gemini/oauth_creds.json",
        default_enabled=True,
        fetch=fetch_gemini_usage,
    ),
    ProviderSpec(
        id="grok",
        display_name="Grok",
        category="subscription",
        open_url="https://grok.com/?_s=usage",
        auth_hint="Login with Grok Build (~/.grok/auth.json)",
        default_enabled=True,
        fetch=fetch_grok_usage,
    ),
    ProviderSpec(
        id="kiro",
        display_name="Kiro",
        category="subscription",
        open_url="https://app.kiro.dev/settings/account",
        auth_hint="Uses kiro-cli login (SSO cache). Fallback: kiro_headers.json",
        default_enabled=True,
        fetch=fetch_kiro_usage,
    ),
    ProviderSpec(
        id="cursor",
        display_name="Cursor",
        category="subscription",
        open_url="https://cursor.com/dashboard",
        auth_hint="Save Cookie header to cursor_cookie (chmod 600)",
        default_enabled=True,
        fetch=fetch_cursor_usage,
    ),
    ProviderSpec(
        id="glm",
        display_name="GLM (z.ai)",
        category="subscription",
        open_url="https://z.ai/",
        auth_hint="Set Z_AI_API_KEY or zai_api_key file",
        default_enabled=True,
        fetch=fetch_glm_usage,
    ),
    ProviderSpec(
        id="opencode-go",
        display_name="OpenCode Go",
        category="subscription",
        open_url="https://opencode.ai/",
        auth_hint="Set OPENCODE_AUTH_COOKIE / opencode_auth_cookie",
        default_enabled=True,
        fetch=fetch_opencode_go_usage,
    ),
    ProviderSpec(
        id="zen",
        display_name="Zen",
        category="subscription",
        open_url="https://opencode.ai/",
        auth_hint="Uses same cookie as OpenCode dashboard",
        default_enabled=True,
        fetch=fetch_zen_usage,
    ),
    ProviderSpec(
        id="claude-api",
        display_name="Claude API",
        category="api_cost",
        open_url="https://console.anthropic.com/settings/usage",
        auth_hint="Set ANTHROPIC_ADMIN_API_KEY / anthropic_admin_key",
        default_enabled=False,
        fetch=fetch_claude_api_usage,
    ),
    ProviderSpec(
        id="openai-api",
        display_name="OpenAI API",
        category="api_cost",
        open_url="https://platform.openai.com/settings/organization/billing/overview",
        auth_hint="Set OpenAI admin key file",
        default_enabled=False,
        fetch=fetch_openai_api_usage,
    ),
    ProviderSpec(
        id="gemini-api",
        display_name="Gemini API",
        category="api_cost",
        open_url="https://aistudio.google.com/app/apikey",
        auth_hint="Set GOOGLE_COOKIES / google_cookies file",
        default_enabled=False,
        fetch=fetch_gemini_api_usage,
    ),
]

PROVIDERS: dict[str, object] = {spec.id: spec.fetch for spec in PROVIDER_SPECS}
PROVIDER_BY_ID: dict[str, ProviderSpec] = {spec.id: spec for spec in PROVIDER_SPECS}
DISPLAY_NAMES: dict[str, str] = {spec.id: spec.display_name for spec in PROVIDER_SPECS}

__all__ = [
    "CATEGORY_LABELS",
    "DISPLAY_NAMES",
    "PROVIDERS",
    "PROVIDER_BY_ID",
    "PROVIDER_SPECS",
    "ProviderSpec",
]
