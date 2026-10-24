# QuotaLantern

AI usage, quotas, resets and costs in your Linux desktop tray. Python + GTK 3,
Ayatana AppIndicator (or AppIndicator3) and libnotify. Independent project with A/Facho identity.

**v0.1.0-alpha.4 — alerts for announced Codex resets and reset hints.**
Installed and run on KDE Plasma/Wayland: tray item registered, offline check
ready and no runtime errors. GTK windows were reviewed through offscreen renders
with synthetic data; pixel-level native review, GNOME and high-DPI coverage
remain pending. Web examples use synthetic data.

- [PT/EN website](https://caiolombello.github.io/quotalantern/)
- [Releases](https://github.com/caiolombello/quotalantern/releases)
- [Origin and credits](ORIGIN.md), [NOTICE](NOTICE), [dependencies](DEPENDENCIES.md)

## Install and verify

Requires existing Python 3.12+ with system GI bindings for GTK 3, Ayatana
AppIndicator or AppIndicator3, and Notify, plus Bash. The installer checks
these requirements; it downloads nothing and never starts providers.

Install system dependencies for your distribution first:

Fedora (including KDE Plasma):

```sh
sudo dnf install python3-gobject gtk3 libayatana-appindicator-gtk3 libnotify
```

An existing Fedora `libappindicator-gtk3` installation is also supported; you
do not need to replace it with Ayatana. The installer and `--check` report the
selected backend. Package contents: [Ayatana](https://packages.fedoraproject.org/pkgs/libayatana-appindicator/libayatana-appindicator-gtk3/fedora-44.html),
[AppIndicator3](https://packages.fedoraproject.org/pkgs/libappindicator/libappindicator-gtk3/fedora-44.html).

Debian / Ubuntu:

```sh
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7
```

Then, from the cloned checkout:

```sh
bash install.sh --dry-run
bash install.sh
quotalantern --check
quotalantern
```

If `~/.local/bin` is not in your PATH, use `~/.local/bin/quotalantern`.
The command opens the tray independently and returns the terminal; closing the
terminal leaves the app running. Launching it again keeps the existing instance.
Use `quotalantern --foreground` for terminal debugging; `--check`, `--json` and
`--help` remain synchronous. Application logs rotate in
`~/.local/share/codexbar-linux/log/codexbar-linux.log`; early startup errors go
to the private `launcher.log` in the same directory. Quit from the tray menu.

Use Python with access to the distribution's GI packages; an isolated virtualenv
without system packages cannot see them. On GNOME, tray icons additionally need
an enabled AppIndicator extension (Fedora package `gnome-shell-extension-appindicator`).
KDE Plasma supplies the tray host directly.

Autostart is opt-in with `bash install.sh --enable-autostart`; this explicitly
re-enables a disabled entry while preserving its other options. Without that
flag, existing autostart preferences are preserved. Installation also adds a QuotaLantern entry in the application
menu; no terminal or manual `&` is needed. The installed `codexbar-linux` command remains an alias.
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
failed or missing reads stay neutral in the tray. The tray ring displays the actual
consumed fraction of named, recent subscription windows. With one provider the
whole ring is its most-used window. With several, each connected provider gets
its own segment, clockwise from the top in the order of your enabled providers,
filled to and colored by its own most-used window, so one exhausted service no
longer paints the whole ring as if everything were at the limit. It is never a
total or average across services and not a recommendation; the title and menu
still name the largest reported fraction and list every segment. Costs are
excluded and adapters that are not connected stay out of the ring. Unknown uses a
dotted neutral ring/segment (a dash in the center when nothing is confirmed);
stale uses a dashed neutral ring/segment (a clock in the center). Mixed coverage
is disclosed in the title and menu. Source and age update every minute; bindings
with tooltip properties also receive a dedicated tooltip. Older Ayatana
bindings expose Title/IconAccessibleDesc; native hover rendering is pending QA.
Middle-click the tray icon to open the Overview. Tray menu rows carry a small
meter per provider and window: rings by default, or bars under
**Settings → Display → Menu meters**.

Provider credentials and usage history are sensitive. Enabling an adapter
permits its existing code to read its configured local credential source and
query its service. Service terms remain separate from this project's MIT
license. No telemetry or account access occurs in the PT/EN website.

OpenCode Go signs in through **Settings → Credentials → OpenCode Go → Sign in**.
Authorize QuotaLantern in the browser and select the workspace with your Go
subscription. It stores a separate OAuth session in the private
`~/.local/share/codexbar-linux/opencode/` directory and renews it automatically;
it does not copy OpenCode CLI credentials or require a browser cookie. Disconnect
removes only QuotaLantern's session. Go meters come from the Console's current
JSON endpoint, which is not yet a documented public API. The Zen adapter retains
its separate legacy cookie behavior.

Codex reset announcements are optional: **Settings → Providers → Announcements**,
off by default. When enabled, QuotaLantern reads the free public API of
[Codex Resets](https://codex-resets.com) (`/api/v1/status`) at most every 15
minutes, revalidating with ETag and backing off on `Retry-After`. No account
data or credentials are sent. These are global resets announced for all paid
Codex plans, classified by that third-party site from @thsottiaux's posts and
not affiliated with OpenAI. They get their own Overview card and menu item with
a credit link and never feed the tray ring or any quota. Each confirmed or
announced (scheduled, not yet confirmed) reset notifies once, including one
already pending when you turn the feature on. Hints that a reset may come are
AI forecasts; they also notify unless you turn off **Alert on reset hints**.

## Optional Orla quota export (experimental)

[Orla Desktop](https://github.com/caiolombello/orla-desktop) can show subscription
quota in its dock through a passive file that QuotaLantern writes. Off by default;
installing QuotaLantern or the Orla package in `contrib/orla/quotalantern.orla-addon.json`
enables neither the export nor Orla's reading. Orla activation is a separate action
in its settings.

```sh
quotalantern --orla-export enable
quotalantern --orla-export status
quotalantern --orla-export disable
```

These commands manage consent only: they never fetch providers or publish.
`status` prints `enabled`, `exportPresent` and `exportBytes`. Enable removes any
old export and waits for the next refresh of the running tray; it never presents
an earlier reading as new. Disable succeeds only after consent is off and the
export removed; later refreshes cannot publish until you enable it again.

Consent is a strict private file, `$XDG_CONFIG_HOME/quotalantern/exports/orla.json`
(`{"formatVersion":"0.1-experimental","enabled":true}`); missing, malformed or
unreadable consent counts as off. It is separate from `config.json`, so Settings
never rewrites it. After each genuine refresh the tray replaces
`$XDG_STATE_HOME/quotalantern/exports/orla-quota-v1.json` atomically (directories
`0700`, files `0600`; symlinks and shared or special files are refused). A relative
or unset XDG variable falls back to `~/.config` and `~/.local/state`.

The export lists enabled subscription providers only (API cost adapters are left
out) with a finite state (`ready`, `stale`, `unknown`, `unavailable`), the original
observation time and up to three windows: percent used, window length and reset
time. Accounts, sign-in methods, provider messages, reset descriptions, credits,
costs and balances are never written. Percentages are kept only for a recent
successful reading; a failed refresh, an old or future observation, or an invalid
value leaves them unknown, never zero. The lease is three refresh intervals
(10 s to 1 h). Orla ages readings on its own side too; bytes already read cannot
be retracted. Disable the export before rolling back to a version without it.

## Offline validation

```sh
python3 run_offline.py
python3 scripts/check_public.py
```

130 synthetic offline tests passed in the release snapshot. Tests block network,
subprocesses and real user files and use a temporary HOME. This verifies the
covered cases, not every provider response or every Linux desktop. CI repeats
these checks on Python 3.12 and 3.14.

MIT for original project contributions; external notices remain applicable.

The experimental Gemini API adapter requires explicit `GEMINI_AISTUDIO_API_KEY` and `GEMINI_AISTUDIO_PROJECT` (`projects/<id>`) in the process environment, in addition to its existing cookie source. Embedded service-key/project defaults were removed before publication. This does not change the separate Gemini subscription adapter. No key is created or copied by the installer.

Gemini subscription OAuth refresh does not ship a fixed client secret. It uses explicit `GEMINI_OAUTH_CLIENT_SECRET`, a `client_secret` field in the existing credentials, or the same-client literal metadata from an existing legacy CodexBar source file at the retained data root. Legacy code is parsed without execution; values are not copied into new files. With no client metadata, refresh is unavailable and the owning CLI must refresh its credentials. Existing valid access-token reads are unchanged.
