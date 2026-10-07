# Changelog

## Unreleased

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
