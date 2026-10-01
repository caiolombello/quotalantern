# Changelog

## v0.1.0-alpha.1

Initial public source snapshot: A/Facho logo and optical small-size assets,
GTK/tray/launcher identity, PT/EN static website, context for unknown/stale
readings, corrected Codex/Kiro invalid percentages, OpenAI/Claude cost units
and complete-or-error cost pagination. Existing Linux features and adapters
are retained. Installer preserves user data and compatible entry points.

Known limits: native pixel-level review and GNOME/high-DPI QA are pending;
GTK/Wayland emitted a DBus-properties warning during the synthetic native test.
Ayatana's current binding reports a deprecation warning. Provider access and
terms require per-service qualification; not every adapter has equal coverage.

Publication sanitization: Gemini API no longer ships a fixed Google client key or project; configuration is explicit and missing values produce an unknown/error reading without a request. The adapter remains present and is off by default in installer settings.

Gemini subscription client-secret metadata is not redistributed. Existing legacy installations retain refresh compatibility; fresh installations require explicit client metadata or credential refresh through the owning CLI. No provider is removed.
