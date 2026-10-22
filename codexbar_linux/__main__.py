"""Entrypoint for codexbar-linux."""

import argparse
import json
import sys


def _json_output() -> None:
    from .codexbar import fetch_all
    from .config import load_config
    from .state import snapshot

    cfg = load_config()
    print(json.dumps(snapshot(fetch_all(enabled=cfg.enabled_providers), None), sort_keys=True))


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Print one usage snapshot as JSON and exit")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check local desktop integration without reading credentials or using the network",
    )
    parser.add_argument(
        "--orla-export",
        choices=("enable", "disable", "status"),
        help="Manage the opt-in Orla quota export; never fetches providers",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    parser.add_argument("--foreground", action="store_true", help="Keep the installed tray attached to the terminal for debugging")
    return parser


def main() -> None:
    args = argument_parser().parse_args()

    if args.check:
        from .diagnostics import platform_report

        print(json.dumps(platform_report(), indent=2, sort_keys=True))
        return

    if args.orla_export:
        from .orla_export import ExportError, command

        try:
            print(json.dumps(command(args.orla_export), sort_keys=True))
        except ExportError as exc:
            print(json.dumps({"error": exc.code}), file=sys.stderr)
            raise SystemExit(1) from None
        return

    if args.json:
        _json_output()
        return

    from .app import CodexBarLinuxApp

    app = CodexBarLinuxApp(verbose=args.verbose)
    try:
        app.run()
    except KeyboardInterrupt:
        app.quit()


if __name__ == "__main__":
    main()
