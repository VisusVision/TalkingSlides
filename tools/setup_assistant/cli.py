from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from .actions import SafeActionExecutor
from .checks import CheckEngine
from .models import APP_NAME, VERSION, CheckStatus, Profile
from .reports import render_json, render_markdown, render_text, write_report
from .repository import discover_repository
from .runtime import RuntimeManager

EXIT_OK = 0
EXIT_CHECK_FAILURE = 1
EXIT_USAGE_OR_SAFETY = 2


def _profile(value: str) -> Profile:
    try:
        return Profile(value.lower())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("profile must be core, tts, or avatar") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="talkingslides-setup", description=APP_NAME)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subcommands = parser.add_subparsers(dest="command")

    check = subcommands.add_parser("check", help="Run read-only system diagnostics.")
    check.add_argument("--profile", type=_profile, default=Profile.CORE)
    check.add_argument("--full", action="store_true", help="Include ports, Compose validation, health, and profile assets.")
    check.add_argument("--internet", action="store_true", help="Explicitly include an internet connectivity probe.")
    check.add_argument("--repository", type=Path)
    check.add_argument("--json", action="store_true", dest="json_output")

    report = subcommands.add_parser("report", help="Run checks and render a sanitized report.")
    report.add_argument("--profile", type=_profile, default=Profile.CORE)
    report.add_argument("--full", action="store_true")
    report.add_argument("--internet", action="store_true")
    report.add_argument("--repository", type=Path)
    report.add_argument("--format", choices=("json", "markdown", "text"), default="json")
    report.add_argument("--output", type=Path, help="Write to this path; stdout is used when omitted.")

    runtime = subcommands.add_parser("runtime", help="Inspect or manage supported runtime profiles.")
    runtime_subcommands = runtime.add_subparsers(dest="runtime_action", required=True)
    for action in ("status", "health", "start", "stop"):
        action_parser = runtime_subcommands.add_parser(action)
        action_parser.add_argument("--profile", type=_profile, default=Profile.CORE)
        action_parser.add_argument("--repository", type=Path)
        action_parser.add_argument("--no-frontend", action="store_true")
        if action in {"start", "stop"}:
            action_parser.add_argument("--confirm", action="store_true", help="Execute after showing the exact command.")
        if action == "start":
            action_parser.add_argument("--allow-avatar-queue-risk", action="store_true")

    gui = subcommands.add_parser("gui", help="Launch the desktop application.")
    gui.add_argument("--repository", type=Path)

    action = subcommands.add_parser("action", help="Preview or execute a narrow local setup action.")
    action.add_argument("action_id", choices=("config.create_env", "config.create_storage"))
    action.add_argument("--repository", type=Path, required=True)
    action.add_argument("--confirm", action="store_true", help="Execute the displayed action.")
    return parser


def _run_checks(args: argparse.Namespace):
    return CheckEngine().run(
        profile=args.profile,
        full=args.full,
        internet=args.internet,
        repository=args.repository,
    )


def _exit_for_run(run) -> int:
    return EXIT_CHECK_FAILURE if run.status is CheckStatus.FAILURE else EXIT_OK


def _print_human(run) -> None:
    output = render_text(run)
    stream = sys.stdout
    stream.write(output)


def _runtime(args: argparse.Namespace) -> int:
    validation = discover_repository(args.repository)
    if not validation or not validation.valid:
        print("A valid TalkingSlides repository is required.", file=sys.stderr)
        return EXIT_USAGE_OR_SAFETY
    manager = RuntimeManager(validation.path)
    confirmed = bool(getattr(args, "confirm", False))
    avatar_ack = bool(getattr(args, "allow_avatar_queue_risk", False))
    preview = manager.preview(args.runtime_action, args.profile, args.no_frontend)
    print(f"Command: {preview}")
    result = manager.execute(
        args.runtime_action,
        args.profile,
        no_frontend=args.no_frontend,
        confirmed=confirmed,
        allow_avatar_queue_risk=avatar_ack,
    )
    if not result.executed:
        print(result.error, file=sys.stderr)
        return EXIT_USAGE_OR_SAFETY
    if result.result:
        if result.result.stdout:
            sys.stdout.write(result.result.stdout)
        if result.result.stderr:
            sys.stderr.write(result.result.stderr)
        if not result.result.ok:
            if result.result.error:
                print(result.result.error, file=sys.stderr)
            return EXIT_CHECK_FAILURE
    return EXIT_OK


def _action(args: argparse.Namespace) -> int:
    validation = discover_repository(args.repository)
    if not validation or not validation.valid:
        print("A valid TalkingSlides repository is required.", file=sys.stderr)
        return EXIT_USAGE_OR_SAFETY
    executor = SafeActionExecutor()
    try:
        preview = executor.preview(args.action_id, validation.path)
        print(f"Action: {preview}")
        if not args.confirm:
            print("Preview only. Re-run with --confirm to execute.")
            return EXIT_OK
        result = executor.execute(args.action_id, validation.path, confirmed=True)
        print(result.summary)
        return EXIT_OK
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CHECK_FAILURE


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        try:
            from .gui import launch_gui
        except ImportError as exc:
            parser.print_help(sys.stderr)
            print(f"\nGUI dependency unavailable: {exc}", file=sys.stderr)
            return EXIT_USAGE_OR_SAFETY
        return launch_gui(None)
    if args.command == "gui":
        try:
            from .gui import launch_gui
        except ImportError as exc:
            print(f"GUI dependency unavailable: {exc}", file=sys.stderr)
            return EXIT_USAGE_OR_SAFETY
        return launch_gui(args.repository)
    if args.command == "check":
        run = _run_checks(args)
        if args.json_output:
            sys.stdout.write(render_json(run))
        else:
            _print_human(run)
        return _exit_for_run(run)
    if args.command == "report":
        run = _run_checks(args)
        if args.output:
            target = write_report(run, args.output, args.format)
            print(os.fspath(target))
        else:
            renderer = {"json": render_json, "markdown": render_markdown, "text": render_text}[args.format]
            sys.stdout.write(renderer(run))
        return _exit_for_run(run)
    if args.command == "runtime":
        return _runtime(args)
    if args.command == "action":
        return _action(args)
    parser.error(f"Unsupported command: {args.command}")
    return EXIT_USAGE_OR_SAFETY


if __name__ == "__main__":
    raise SystemExit(main())
