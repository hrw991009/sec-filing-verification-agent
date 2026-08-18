"""Static database constraints for Agent and Conversation tenant ownership."""

from sqlalchemy import ForeignKeyConstraint, Index, Table, UniqueConstraint

from industry_platform.modules.agent_runtime.models import (
    AgentEventRecord,
    AgentRunRecord,
    AgentStepRecord,
    ContextManifestRecord,
)
from industry_platform.modules.conversations.models import Message, MessageAttachment, Turn
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.tools.models import ToolCallRecord, ToolRunRecord


def constraint_columns(
    constraint: ForeignKeyConstraint | UniqueConstraint,
) -> tuple[str, ...]:
    columns = constraint.columns
    return tuple(column.name for column in columns)


def test_workspace_is_part_of_every_cross_resource_foreign_key() -> None:
    required = {
        ("conversation_turns", ("conversation_id", "workspace_id")),
        ("agent_runs", ("conversation_id", "workspace_id")),
        ("agent_runs", ("turn_id", "workspace_id")),
        ("conversation_messages", ("turn_id", "workspace_id")),
        ("conversation_messages", ("agent_run_id", "workspace_id")),
        ("agent_steps", ("run_id", "workspace_id")),
        ("agent_events", ("run_id", "workspace_id")),
        ("context_manifests", ("step_id", "run_id", "workspace_id")),
        ("message_attachments", ("message_id", "workspace_id")),
        ("message_attachments", ("file_id", "workspace_id")),
        ("tool_calls", ("requested_by_step_id", "run_id", "workspace_id")),
        ("tool_calls", ("execution_step_id", "run_id", "workspace_id")),
        ("tool_runs", ("id", "run_id", "workspace_id")),
    }
    tables = tuple(
        table
        for table in (
            Turn.__table__,
            AgentRunRecord.__table__,
            Message.__table__,
            AgentStepRecord.__table__,
            AgentEventRecord.__table__,
            ContextManifestRecord.__table__,
            MessageAttachment.__table__,
            ToolCallRecord.__table__,
            ToolRunRecord.__table__,
        )
        if isinstance(table, Table)
    )
    actual = {
        (table.name, constraint_columns(constraint))
        for table in tables
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }

    assert required <= actual


def test_run_job_and_stream_are_one_to_one_and_event_sequences_are_unique() -> None:
    run_table = AgentRunRecord.__table__
    event_table = AgentEventRecord.__table__
    assert isinstance(run_table, Table)
    assert isinstance(event_table, Table)
    run_unique_columns = {
        constraint_columns(constraint)
        for constraint in run_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    event_unique_columns = {
        constraint_columns(constraint)
        for constraint in event_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("job_id",) in run_unique_columns
    assert ("event_stream_id",) in run_unique_columns
    assert ("stream_id", "sequence") in event_unique_columns


def test_database_allows_only_one_user_input_and_one_final_message() -> None:
    message_table = Message.__table__
    assert isinstance(message_table, Table)
    partial_unique_indexes = {
        index.name for index in message_table.indexes if isinstance(index, Index) and index.unique
    }

    assert "uq_conversation_messages_one_user_input_per_turn" in partial_unique_indexes
    assert "uq_conversation_messages_one_final_per_run" in partial_unique_indexes


def test_private_file_keys_and_message_attachment_order_are_unique() -> None:
    file_table = FileObject.__table__
    attachment_table = MessageAttachment.__table__
    assert isinstance(file_table, Table)
    assert isinstance(attachment_table, Table)
    file_unique_columns = {
        constraint_columns(constraint)
        for constraint in file_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    attachment_unique_columns = {
        constraint_columns(constraint)
        for constraint in attachment_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("id", "workspace_id") in file_unique_columns
    assert ("bucket", "staging_object_key") in file_unique_columns
    assert ("file_id",) in attachment_unique_columns
    assert ("message_id", "ordinal") in attachment_unique_columns
