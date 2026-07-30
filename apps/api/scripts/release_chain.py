"""Public parser-integration hooks for release chain commands."""

from .release_chain_acceptance import (
    AcceptanceInputManifestRequest,
    AcceptanceRefreshRequest,
    NamedPath,
    handle_acceptance_input_manifest,
    handle_acceptance_refresh,
)
from .release_chain_aggregate import AggregateRequest, handle_aggregate
from .release_chain_capture import (
    AcceptanceCaptureRequest,
    CaptureObservation,
    CurrentCaptureProvider,
    handle_acceptance_capture,
)
from .release_chain_common import (
    PathReceiptIO,
    ReceiptIO,
    ReleaseChainError,
    utc_now,
)
from .release_chain_final import (
    FinalFanInRequest,
    FinalLaneRequest,
    handle_final_fan_in,
    handle_final_lane,
)
from .release_chain_materialize import (
    MaterializeChainRequest,
    handle_materialize_chain,
    validate_chain_manifest,
)

__all__ = (
    "AcceptanceCaptureRequest",
    "AcceptanceInputManifestRequest",
    "AcceptanceRefreshRequest",
    "AggregateRequest",
    "CaptureObservation",
    "CurrentCaptureProvider",
    "FinalFanInRequest",
    "FinalLaneRequest",
    "MaterializeChainRequest",
    "NamedPath",
    "PathReceiptIO",
    "ReceiptIO",
    "ReleaseChainError",
    "handle_acceptance_capture",
    "handle_acceptance_input_manifest",
    "handle_acceptance_refresh",
    "handle_aggregate",
    "handle_final_fan_in",
    "handle_final_lane",
    "handle_materialize_chain",
    "utc_now",
    "validate_chain_manifest",
)
