"""Shared desktop dependency checks; no display, providers or network needed."""
from __future__ import annotations

from typing import Any


def _binding(namespace: str, version: str) -> Any:
    import gi

    gi.require_version(namespace, version)
    return getattr(__import__("gi.repository", fromlist=[namespace]), namespace)


def load_appindicator() -> tuple[str, Any]:
    """Prefer Ayatana, accepting the compatible AppIndicator3 shipped by Fedora."""
    errors = []
    for namespace in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            return namespace, _binding(namespace, "0.1")
        except (ImportError, ValueError) as exc:
            errors.append(f"{namespace}: {exc}")
    raise ImportError("; ".join(errors))


def runtime_status() -> dict[str, Any]:
    result: dict[str, Any] = {
        "gtk3": False,
        "appindicator": False,
        "appindicator_backend": None,
        "ayatana_appindicator": False,
        "notify": False,
    }
    errors = []
    for key, namespace, version in (("gtk3", "Gtk", "3.0"), ("notify", "Notify", "0.7")):
        try:
            binding = _binding(namespace, version)
            result[key] = True
            if key == "gtk3":
                result["gtk_version"] = f"{binding.MAJOR_VERSION}.{binding.MINOR_VERSION}.{binding.MICRO_VERSION}"
        except (ImportError, ValueError) as exc:
            errors.append(f"{namespace}: {exc}")
    try:
        namespace, _ = load_appindicator()
        result.update(appindicator=True, appindicator_backend=namespace,
                      ayatana_appindicator=namespace == "AyatanaAppIndicator3")
    except ImportError as exc:
        errors.append(str(exc))
    if errors:
        result["error"] = "; ".join(errors)
    return result
