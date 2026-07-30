"""Import surface that registers every declarative model."""

from typing import Final

from sqlalchemy import MetaData

from . import (
    analysis_models,
    auth_models,
    manifest_models,
    operations_models,
    page_models,
    post_models,
    publication_models,
    release_cadence_models,
    release_models,
    release_receipt_models,
    report_models,
    rule_models,
    run_models,
    scheduler_models,
    tombstone_models,
    verifier_models,
)
from .base import Base

metadata: Final[MetaData] = Base.metadata

__all__ = [
    "analysis_models",
    "auth_models",
    "manifest_models",
    "metadata",
    "operations_models",
    "page_models",
    "post_models",
    "publication_models",
    "release_cadence_models",
    "release_models",
    "release_receipt_models",
    "report_models",
    "rule_models",
    "run_models",
    "scheduler_models",
    "tombstone_models",
    "verifier_models",
]
