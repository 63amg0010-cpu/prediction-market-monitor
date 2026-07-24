"""Typed facade around PyYAML's untyped safe loader."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable

type YamlValue = (
    str | int | float | bool | None | list["YamlValue"] | dict[str, "YamlValue"]
)
_loader: Callable[[str], YamlValue] = yaml.safe_load


def load(stream: str) -> YamlValue:
    """Load one YAML document through the typed boundary facade."""
    return _loader(stream)
