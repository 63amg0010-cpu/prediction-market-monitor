from collections.abc import Generator
from contextlib import contextmanager

import pytest
from app.collection.base import CollectionError, CollectionErrorCode


@contextmanager
def _transaction_boundary() -> Generator[None]:
    yield


def test_collection_error_survives_context_manager_traceback_assignment() -> None:
    # Given/When: a database-style context manager propagates a typed failure.
    with pytest.raises(CollectionError) as captured, _transaction_boundary():
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 409)

    # Then: Python can attach traceback state instead of replacing it with TypeError.
    assert captured.value.code is CollectionErrorCode.INVALID_CONTRACT
    assert captured.value.status_code == 409
