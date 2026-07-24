"""GitHub Actions collector and verifier command-line facade."""

from __future__ import annotations

import argparse
import os
from typing import TYPE_CHECKING

import anyio

from .cli_config import CliError
from .collector_command import collect, execute_collect_command
from .control_plane_client import ControlPlaneClient
from .verification_command import verify

if TYPE_CHECKING:
    from collections.abc import Sequence


class _Arguments(argparse.Namespace):
    command: str = ""


def main(argv: Sequence[str] | None = None) -> None:
    """Run the bounded collector or independent verifier command."""
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("command", choices=("collect", "verify"))
    arguments = _Arguments()
    _ = parser.parse_args(argv, namespace=arguments)
    operation = collect if arguments.command == "collect" else verify
    anyio.run(operation, os.environ)


if __name__ == "__main__":
    main()


__all__ = ("CliError", "ControlPlaneClient", "execute_collect_command", "main")
