"""Manual worker capability check entry point."""

import json
import sys
from dataclasses import asdict

from .capability import local_capability_decision
from .worker import WorkerBlocked


def main() -> int:
    """Print the current fail-closed state without touching credentials."""
    decision = local_capability_decision()
    result = WorkerBlocked(reason_codes=decision.reason_codes)
    payload = json.dumps(asdict(result), separators=(",", ":"), sort_keys=True)
    _ = sys.stdout.write(payload + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
