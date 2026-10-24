# Changelog

## v0.1.0-alpha.5

- Tray ring: with two or more connected subscription providers the ring is
  split into one segment per provider, each filled to and colored by its own
  most-used window. One provider at its limit now shows one red segment
  instead of a fully red ring that read as "everything is at the limit".
  Unknown and stale providers keep a neutral dotted/dashed segment; not
  connected adapters stay out. One provider, or no confirmed reading, keeps
  the previous single ring. The icon title lists every segment, and the
  Overview header ring mirrors the tray.
- Tray: saving Settings re-colors the ring with the new warning/critical
  levels immediately instead of on the next minute tick, and the panel label
  keeps the ring's wording. Middle-click on the tray icon opens the Overview.
  Generated ring icons are pruned to the most recent 48 files.
- Tray menu: provider and window rows carry a small ring icon (exported to
  the tray host as PNG) instead of emoji and block-character bars, which
  rendered as grids and dithered patterns in Plasma's menu font. Rows state
  their condition in words ("at the limit", "◷ Stale", "not connected"), so
  the section titles are gone. The header drops to the reading, its source
  and age, the next reset and what is unconfirmed; the ring policy and the
  segment list stay in the icon title and the Overview. Reset countdowns are
  computed live from the reset time ("resets in 1h 41m") and no longer
  repeat the window name ("Session — Session (5h)"). Adapters that are not
  connected no longer count as unconfirmed coverage.
- Settings → Display → Menu meters: choose rings (default) or bars for the
  tray menu rows. Bars are drawn as icons too, with the same colors and
  unknown/stale states, so they render the same in every panel font.
- Optional Orla Desktop quota export, off by default: `--orla-export
  enable|disable|status` manages a separate consent file, and each genuine
  refresh then publishes subscription quota windows (percent, length, reset)
  for Orla's dock. Accounts, provider messages, costs and balances are never
  exported. Package and schema in `contrib/orla/`.
- Fedora: support the system AppIndicator3 binding as well as Ayatana, report
  desktop dependencies separately, and show distribution-specific installation
  commands. Verify source integrity before loading the dependency probe.
- Installed launchers open the tray independently of the terminal, keep a
  single instance and retain private startup diagnostics. `--foreground` keeps
  terminal debugging available; `--check`, `--json` and `--help` stay synchronous.
  Login startup remains opt-in with `install.sh --enable-autostart`.

## v0.1.0-alpha.4

- Codex Resets: announced resets that are not confirmed yet, and hints that
  a reset may come (AI forecasts, labeled as such), now notify. A pending
  announcement or live hint found on the first check is reported instead of
  being recorded silently. Announced banked resets say they are a credit you
  apply, not an automatic reset. New "Alert on reset hints" setting, on by
  default once announcements are enabled.

## v0.1.0-alpha.3

Interface redesign; collection, eligibility and storage are unchanged.

- Overview window: header ring mirrors the tray (same SVG states), provider
  cards with status badges in words (Fresh, Stale, Error, Not connected,
  Spend), bars colored by your warning/critical levels and grey when
  unconfirmed, loading/empty states, refresh feedback, F5/Ctrl+R and Esc.
  Follows the detected light/dark desktop theme instead of forcing dark.
- Tray menu: ring context first (label, source/age, coverage), main actions at
  the top level, no decorative emoji, color only for confirmed readings.
- Settings: sidebar pages with switches and one-line explanations, threshold
  validation before saving, credential files show presence/permissions only.
  The redundant "Save & Refresh" button is gone; changing providers or the
  GLM region still triggers a refresh.
- Notifications use provider display names and the app icon.
- Optional Codex reset announcements from codex-resets.com (off by default):
  strict response validation, at most one check every 15 minutes with ETag
  and Retry-After, an attributed Overview card and menu item, and one
  notification per new regular, banked or scheduled reset. Global data stays
  out of the tray ring and quota readings.
- Overview header drops the decorative label; the ring and its reading lead.
- Website: redesigned as an instrument data sheet (paper and brand ink in
  light mode, blueprint in dark mode, serif headings, mono data, numbered
  figures and tables on the icon's 32 px grid). The tray icon enlarged with
  live callouts, an Overview demo with four synthetic scenarios, ring-state
  and adapter-verification tables, one copyable install block, privacy and
  known limits, an app tour with real offscreen GTK captures, and the icon
  matrix. Copy rewritten in plain PT/EN; no external fonts or requests.

## v0.1.0-alpha.2

Restore an at-a-glance quota ring around the approved Facho optical mark.
Fill follows the actual 0–100% reported usage rather than fixed buckets.
Select one named recent subscription window, never aggregate cost or incompatible
allowances. Separate unknown and stale neutral shapes; disclose source, age and
partial coverage in the tray context. Keep app/launcher/landing branding.
Fix the offline GTK test fixture so CI does not depend on system GI.
The installed Ayatana binding lacks dedicated tooltip properties; full context
is exported as Title/IconAccessibleDesc and shown in the menu. Native hover
and native pixel review remain pending.

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
