"""Rebind the release root after fixing zero-provider binding controls."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Protocol

    class _BaseMigration(Protocol):
        revision: str
        CORRECTION_COMMAND: str
        STATE_BEFORE: str
        ENV_PREFIX: str

        def upgrade(self) -> None: ...

        def downgrade(self) -> None: ...

revision: str = "20260803_0010h"
down_revision: str | None = "20260803_0010g"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base() -> _BaseMigration:
    path = Path(__file__).with_name(
        "20260803_0010g_rebind_canonical_receipt_release.py"
    )
    spec = spec_from_file_location("monitor_release_rebind_0010g", path)
    if spec is None or spec.loader is None:
        error_code = "zero_provider_rebind_base_load_failed"
        raise RuntimeError(error_code)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast("_BaseMigration", cast("object", module))


def _run(name: str) -> None:
    base = _base()
    base.revision = revision
    base.CORRECTION_COMMAND = "release-correction-0010h"
    base.STATE_BEFORE = down_revision or ""
    base.ENV_PREFIX = "MIGRATION_ZERO_PROVIDER_REBIND"
    if name == "upgrade":
        base.upgrade()
    else:
        base.downgrade()


def upgrade() -> None:
    """Append a reviewed root without activating or changing a source."""
    _run("upgrade")


def downgrade() -> None:
    """Remove only an unused 0010h root rebind."""
    _run("downgrade")
