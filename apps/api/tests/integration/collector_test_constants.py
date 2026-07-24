from datetime import UTC, datetime
from uuid import UUID

RUN_ID = UUID("7c4ade1f-b450-46b2-aaed-cda121160d1e")
COMMAND_ID = UUID("826936f4-9eae-43f4-aa16-68955681cb88")
COMMIT_ID = UUID("c84f41e8-62c2-4498-a037-60307ffbf22b")
IDEMPOTENCY_KEY = UUID("fb0959b9-4d1d-4e8a-b1dd-fe29b47d8e72")
SOURCE_ID = UUID("0c90e846-67f0-4fa8-9a22-eb2e226faab5")
NOW = datetime(2026, 7, 21, 16, 17, tzinfo=UTC)
