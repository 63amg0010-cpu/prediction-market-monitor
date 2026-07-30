"""Shared parser construction for every committed release-gate command."""

# pyright: reportArgumentType=false
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Never, override

from scripts.release_gate_cli_specs import COMMANDS, SPECS, Option
from scripts.release_gate_cli_specs_more import MORE_SPECS

if TYPE_CHECKING:
    from collections.abc import Sequence


class SafeArgumentParser(argparse.ArgumentParser):
    """Reject malformed argv without echoing possibly sensitive values."""

    @override
    def error(self, message: str) -> Never:
        _ = message
        error_code = "release_gate_argument_contract_rejected"
        raise ValueError(error_code)


def _add_option(parser: argparse.ArgumentParser, option: Option) -> None:
    kwargs: dict[str, object] = {"required": option.required}
    if option.dest is not None:
        kwargs["dest"] = option.dest
    if option.choices:
        kwargs["choices"] = option.choices
    if option.action == "append":
        kwargs["action"] = "append"
    elif option.action == "one_or_more":
        kwargs["nargs"] = "+"
    elif option.action == "flag":
        kwargs = {"action": "store_true"}
    elif option.action == "integer":
        kwargs["type"] = int
    _ = parser.add_argument(f"--{option.name}", **kwargs)


def parser() -> argparse.ArgumentParser:
    """Build the sole executable release-gate parser."""
    root = SafeArgumentParser(
        prog="fresh_search_release_gate.py",
        description="Fail-closed fresh-search release gate.",
    )
    subcommands = root.add_subparsers(
        dest="command",
        required=True,
        parser_class=SafeArgumentParser,
    )
    specs = {**SPECS, **MORE_SPECS}
    if tuple(specs) != COMMANDS:
        missing = set(COMMANDS).symmetric_difference(specs)
        msg = f"release_gate_command_registry_invalid:{','.join(sorted(missing))}"
        raise RuntimeError(msg)
    for command in COMMANDS:
        command_parser = subcommands.add_parser(command)
        for option in specs[command]:
            _add_option(command_parser, option)
    return root


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse only the complete, prefixed command surface."""
    return parser().parse_args(argv)


__all__ = ("COMMANDS", "parse_args", "parser")
