"""PATH environment resolution inspired by CodexBar's PathEnvironment.swift.

Replicates the login-shell PATH capture and binary resolution logic.
"""

import os
import shutil
import subprocess
from typing import Optional


# Cache for login PATH (computed once per app launch)
_login_path_cache: Optional[list[str]] = None


def _detect_shell() -> str:
    """Detect the user's preferred shell."""
    shell = os.environ.get("SHELL", "/bin/sh")
    return shell


def get_login_path() -> list[str]:
    """Capture PATH from an interactive login shell (like CodexBar's LoginShellPathCache).

    This is critical for finding binaries installed by nvm, fnm, mise, asdf, etc.
    which mutate PATH in ~/.zshrc or ~/.bashrc.
    """
    global _login_path_cache
    if _login_path_cache is not None:
        return _login_path_cache

    shell = _detect_shell()

    # Build command to print PATH from a login interactive shell
    # We use a short timeout and suppress errors since not all shells support -l -i
    cmd = [shell, "-ilc", 'echo "$PATH"']

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            path_str = result.stdout.strip().split("\n")[-1].strip()
            if path_str:
                _login_path_cache = [p for p in path_str.split(":") if p]
                return _login_path_cache
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        pass

    # Fallback: try with just -l (non-interactive login shell)
    try:
        result = subprocess.run(
            [shell, "-lc", 'echo "$PATH"'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            path_str = result.stdout.strip().split("\n")[-1].strip()
            if path_str:
                _login_path_cache = [p for p in path_str.split(":") if p]
                return _login_path_cache
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        pass

    # Final fallback: current PATH
    current = os.environ.get("PATH", "")
    _login_path_cache = [p for p in current.split(":") if p]
    return _login_path_cache


def effective_path() -> str:
    """Build effective PATH combining login PATH and current PATH.

    Order: login PATH entries first, then current PATH entries that aren't already present.
    """
    login = get_login_path()
    current = [p for p in os.environ.get("PATH", "").split(":") if p]

    seen = set()
    ordered: list[str] = []

    for p in login + current:
        if p not in seen:
            seen.add(p)
            ordered.append(p)

    return ":".join(ordered)


def resolve_binary(
    name: str,
    env_override_key: Optional[str] = None,
    well_known_paths: Optional[list[str]] = None,
) -> Optional[str]:
    """Resolve a binary using the same priority as CodexBar's BinaryLocator.

    Priority:
    1. Environment override (e.g. CODEX_CLI_PATH)
    2. which via effective PATH
    3. Well-known paths
    4. which via login PATH
    """
    # 1. Environment override
    if env_override_key:
        override = os.environ.get(env_override_key, "").strip()
        if override:
            if os.path.isfile(override) and os.access(override, os.X_OK):
                return override

    # 2. which via effective PATH
    found = shutil.which(name, path=effective_path())
    if found:
        return found

    # 3. Well-known paths
    if well_known_paths:
        for p in well_known_paths:
            candidate = os.path.join(p, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

    # 4. which via login PATH only
    login_path_str = ":".join(get_login_path())
    found = shutil.which(name, path=login_path_str)
    if found:
        return found

    return None


def resolve_codex() -> Optional[str]:
    """Resolve the codex CLI binary."""
    return resolve_binary("codex", env_override_key="CODEX_CLI_PATH")


def resolve_claude() -> Optional[str]:
    """Resolve the claude CLI binary."""
    well_known = [
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/.claude/local"),
        os.path.expanduser("~/.claude/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    return resolve_binary("claude", env_override_key="CLAUDE_CLI_PATH", well_known_paths=well_known)


def resolve_opencode() -> Optional[str]:
    """Resolve the opencode CLI binary."""
    return resolve_binary("opencode")


def resolve_kiro() -> Optional[str]:
    """Resolve the kiro-cli binary."""
    return resolve_binary("kiro-cli")


def resolve_agy() -> Optional[str]:
    """Resolve the Antigravity (agy) CLI binary."""
    well_known = [
        os.path.expanduser("~/.local/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    return resolve_binary("agy", env_override_key="ANTIGRAVITY_CLI_PATH", well_known_paths=well_known)


def enriched_env(base: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Create an enriched environment for subprocess execution.

    Mirrors TTYCommandRunner.enrichedEnvironment() from CodexBar.
    """
    env = dict(base) if base else dict(os.environ)
    env["PATH"] = effective_path()

    if not env.get("HOME"):
        env["HOME"] = os.path.expanduser("~")
    if not env.get("TERM"):
        env["TERM"] = "xterm-256color"
    if not env.get("COLORTERM"):
        env["COLORTERM"] = "truecolor"
    if not env.get("LANG"):
        env["LANG"] = "en_US.UTF-8"
    if "CI" not in env:
        env["CI"] = "0"

    return env
