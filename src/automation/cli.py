"""Argparse wiring and the command dispatch table.

This module is the console entry point declared in ``pyproject.toml``. The
repository root ``main.py`` is a thin shim so the CLI also runs straight from a
clone, without installing anything.

Examples:
    gitlab-automation doctor
    gitlab-automation validate --strict
    gitlab-automation stories create --dry-run
    gitlab-automation epics create
    gitlab-automation milestones create --file data/milestones.json
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from . import __version__
from .client import GitLabClient, GitLabError
from .commands import (
    doctor,
    epics,
    issues,
    labels,
    milestones,
    stories,
    validate,
)
from .config import ConfigError, load_config
from .logger import configure_logging

COMMANDS = {
    "doctor": doctor,
    "validate": validate,
    "epics": epics,
    "stories": stories,
    "issues": issues,
    "milestones": milestones,
    "labels": labels,
}

EPILOG = """\
Typical workflow:
  1. doctor                              verify token, group and projects
  2. validate                            check every JSON file offline
  3. labels create                       create the labels the payloads use
  4. milestones create                   create the schedule windows
  5. epics create --skip-unsupported     create the epics (tolerates Free groups)
  6. stories create --dry-run            preview the user stories
  7. stories create                      create them for real

Run it as `gitlab-automation <command>`, or as `python main.py <command>` from a clone.
On a Free/Starter group the epics API answers 403; `--skip-unsupported` turns that
licence limit into a warning, and `stories create --no-assign-epics` skips the gated
epic lookup entirely.

Environment is read from .env (see .env.example).
"""


def _add_trailing_global_flags(parser: argparse.ArgumentParser) -> None:
    """Accept the global options after the subcommand too.

    ``--dry-run`` is a global option, so argparse only allows it before the
    subcommand. ``stories create --dry-run`` is what everybody types first, and
    answering "unrecognized arguments" is a needlessly sharp edge. Every flag is
    added with ``default=SUPPRESS`` so an unspecified flag leaves the value the
    main parser already stored untouched.
    """
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Report every action without writing anything to GitLab.",
    )
    parser.add_argument(
        "--data-dir",
        default=argparse.SUPPRESS,
        help="Directory holding the JSON documents. Defaults to ./data.",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default=argparse.SUPPRESS,
        help="Override GITLAB_LOG_LEVEL.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Disable ANSI colors.",
    )


def _accept_global_flags_everywhere(
    parser: argparse.ArgumentParser, seen: set[int] | None = None
) -> None:
    """Recurse into every subparser, including nested ones like ``stories create``."""
    seen = set() if seen is None else seen
    for action in parser._actions:  # noqa: SLF001
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for subparser in action.choices.values():
            # ``stories`` and ``user-stories`` are two names for one parser object,
            # so the same instance can show up twice and must only be patched once.
            if id(subparser) in seen:
                continue
            seen.add(id(subparser))
            _add_trailing_global_flags(subparser)
            _accept_global_flags_everywhere(subparser, seen)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitlab-automation",
        description=(
            "Mirror JSON documents into GitLab: epics, user stories, issues, "
            "milestones and labels."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report every action without writing anything to GitLab.",
    )
    parser.add_argument(
        "--data-dir",
        help="Directory holding the JSON documents. Defaults to ./data.",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Override GITLAB_LOG_LEVEL.",
    )
    parser.add_argument(
        "--no-color", action="store_true", help="Disable ANSI colors."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    for module in COMMANDS.values():
        module.register(subparsers)
    _accept_global_flags_everywhere(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(
            data_dir=args.data_dir,
            dry_run=True if args.dry_run else None,
            require_token=not getattr(args, "offline", False),
        )
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    if args.log_level:
        config = replace(config, log_level=args.log_level)
    if args.no_color:
        config = replace(config, color=False)

    logger = configure_logging(config.log_level, color=config.color)

    if config.dry_run:
        logger.info("Dry run enabled: no write will be performed.")

    try:
        with GitLabClient(config) as client:
            return int(args.handler(client, config, args))
    except ConfigError as error:
        logger.error("%s", error)
        return 2
    except GitLabError as error:
        logger.error("%s", error)
        return 3
    except KeyboardInterrupt:
        logger.warning("Interrupted by the user.")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())