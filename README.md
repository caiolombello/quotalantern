# QuotaLantern

AI usage, quotas, resets and costs in your Linux desktop tray. Python + GTK 3,
Ayatana AppIndicator and libnotify. Independent project with A/Facho identity.

**v0.1.0-alpha.1 — initial source preview.** Native integration was exercised on
KDE Plasma/Wayland: GTK window mapped, SVG loaded, tray registered and test
notification accepted. Pixel-level native visual review, GNOME and high-DPI
coverage remain pending. Web screenshots/concepts use synthetic data.

- [PT/EN website](https://caiolombello.github.io/quotalantern/)
- [Releases](https://github.com/caiolombello/quotalantern/releases)
- [Origin and credits](ORIGIN.md), [NOTICE](NOTICE), [dependencies](DEPENDENCIES.md)

## Install and verify

Requires existing Python 3.12+ with system GI bindings for GTK 3, Ayatana
AppIndicator and Notify, plus Bash and util-linux flock. The installer checks
these requirements; it downloads nothing and never starts providers.

```sh
bash install.sh --dry-run
bash install.sh
quotalantern --check
quotalantern
```

Autostart is opt-in with `bash install.sh --enable-autostart`; existing autostart
options are preserved. The installed `codexbar-linux` command remains an alias.
Settings and data stay in `~/.config/codexbar-linux` and
`~/.local/share/codexbar-linux`. Versions and backups stay in that data root;
credentials are used in their existing provider-owned storage and never copied
by the installer. Existing enabled providers are preserved. A fresh install
seeds only Codex; enable other adapters deliberately in Settings.

Rollback command is printed by the installer. It restores launcher/desktop
entry points while leaving data and version directories recoverable. Close the
app before rollback. `--json` queries enabled providers; `--check` is offline.

## Reliability and privacy

Missing or invalid readings are unknown, not zero. Costs use currency units
and are not displayed as quota. Pagination must complete or report an error.
Headroom recommendations are limited to qualified fresh Codex readings; stale,
failed or missing reads stay neutral in the tray. The tray menu gives context.

Provider credentials and usage history are sensitive. Enabling an adapter
permits its existing code to read its configured local credential source and
query its service. Service terms remain separate from this project's MIT
license. No telemetry or account access occurs in the PT/EN website.

## Offline validation

```sh
python3 run_offline.py
python3 scripts/check_public.py
```

70 synthetic offline tests passed in the release snapshot. Tests block network,
subprocesses and real user files and use a temporary HOME. This verifies the
covered cases, not every provider response or every Linux desktop. CI repeats
these checks on Python 3.12 and 3.14.

MIT for original project contributions; external notices remain applicable.

The experimental Gemini API adapter requires explicit `GEMINI_AISTUDIO_API_KEY` and `GEMINI_AISTUDIO_PROJECT` (`projects/<id>`) in the process environment, in addition to its existing cookie source. Embedded service-key/project defaults were removed before publication. This does not change the separate Gemini subscription adapter. No key is created or copied by the installer.

Gemini subscription OAuth refresh does not ship a fixed client secret. It uses explicit `GEMINI_OAUTH_CLIENT_SECRET`, a `client_secret` field in the existing credentials, or the same-client literal metadata from an existing legacy CodexBar source file at the retained data root. Legacy code is parsed without execution; values are not copied into new files. With no client metadata, refresh is unavailable and the owning CLI must refresh its credentials. Existing valid access-token reads are unchanged.
