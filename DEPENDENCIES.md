# Dependencies and boundaries

This source release does not bundle a Python interpreter, GI shared libraries,
third-party CLIs, fonts, desktop sounds, credentials or user data. Pure-Python
code uses the standard library; requirements.txt contains no pip packages.
Install system components through your distribution's official packages.

| Component | Use and license boundary |
| --- | --- |
| Python | Interpreter and standard library, PSF license family and component notices |
| PyGObject | GI bindings; LGPL-2.1-or-later and included component notices |
| GTK/GDK, GLib/GIO, Pango, GdkPixbuf | System GUI/runtime components; LGPL families plus package-specific notices |
| Ayatana AppIndicator | System StatusNotifier integration; retain the distribution's LGPL/component notices |
| libnotify | System desktop notifications, LGPL-2.1-or-later |
| Bash / util-linux flock | Local launcher and instance locking; system packages with their own GPL/component notices |
| libcanberra, kreadconfig, gsettings, busctl, xdg-open | Optional system sound/theme/desktop helpers; package licenses remain applicable |
| CodexBar CLI | Legacy helper retained in source, not in the active native provider registry; upstream MIT, Peter Steinberger |
| Provider CLIs and services | Not redistributed. Their terms, authentication policy and account access rules remain separate from MIT |

Adapter code is present for Codex, Claude, Gemini, Kiro, Grok, Cursor, GLM,
OpenCode Go/Zen, OpenAI API, Claude API and Gemini API. Presence is not a claim
that every endpoint is supported or permitted by its service. Claude's adapter
uses Claude Code OAuth credentials; check Anthropic's current third-party
authentication policy before enabling or distributing a dependent integration.
No new login or access grant is performed by this installer.

Any future binary/Flatpak/AppImage bundle must inventory its actual included
components and carry their exact notices; this source-only release makes no
binary-bundle license claim.

The experimental Gemini API adapter requires explicit `GEMINI_AISTUDIO_API_KEY` and `GEMINI_AISTUDIO_PROJECT` (`projects/<id>`) in the process environment, in addition to its existing cookie source. Embedded service-key/project defaults were removed before publication. This does not change the separate Gemini subscription adapter. No key is created or copied by the installer.

Gemini subscription OAuth refresh does not ship a fixed client secret. It uses explicit `GEMINI_OAUTH_CLIENT_SECRET`, a `client_secret` field in the existing credentials, or the same-client literal metadata from an existing legacy CodexBar source file at the retained data root. Legacy code is parsed without execution; values are not copied into new files. With no client metadata, refresh is unavailable and the owning CLI must refresh its credentials. Existing valid access-token reads are unchanged.
