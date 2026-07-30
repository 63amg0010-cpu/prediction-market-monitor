"""Static schema boundary owned by revision 20260721_0001."""

from typing import Final

from sqlalchemy import MetaData

from .revision_0001_parts import (
    analysis_models,
    auth_models,
    manifest_models,
    operations_models,
    page_models,
    post_models,
    publication_models,
    report_models,
    rule_models,
    run_models,
    scheduler_models,
    tombstone_models,
    verifier_models,
)
from .revision_0001_parts.base import Base

metadata: Final[MetaData] = MetaData(naming_convention=Base.metadata.naming_convention)
for _table in Base.metadata.tables.values():
    _ = _table.to_metadata(metadata)

__all__ = [
    "analysis_models",
    "auth_models",
    "manifest_models",
    "metadata",
    "operations_models",
    "page_models",
    "post_models",
    "publication_models",
    "report_models",
    "rule_models",
    "run_models",
    "scheduler_models",
    "tombstone_models",
    "verifier_models",
]
