from app.collection.commands import aggregate_command_status
from app.domain.enums import CommandStatus, RunStatus


def test_completion_aggregate_truth_table_is_mutually_exclusive() -> None:
    # Given: every meaningful terminal source-outcome class.
    cases = (
        ((RunStatus.SUCCEEDED,), CommandStatus.SUCCEEDED),
        (
            (RunStatus.SUCCEEDED, RunStatus.FAILED_RETRYABLE),
            CommandStatus.PARTIAL,
        ),
        ((RunStatus.SUCCEEDED, RunStatus.SKIPPED_QUOTA), CommandStatus.PARTIAL),
        (
            (RunStatus.SKIPPED_POLICY, RunStatus.SKIPPED_QUOTA),
            CommandStatus.SKIPPED,
        ),
        (
            (RunStatus.FAILED_TERMINAL, RunStatus.FAILED_RETRYABLE),
            CommandStatus.FAILED_RETRYABLE,
        ),
        (
            (RunStatus.FAILED_TERMINAL, RunStatus.SKIPPED_POLICY),
            CommandStatus.FAILED_TERMINAL,
        ),
    )

    # When: each set is reduced to the command status.
    results = tuple(aggregate_command_status(statuses) for statuses, _ in cases)

    # Then: the result exactly matches the normative truth table.
    assert results == tuple(expected for _, expected in cases)
