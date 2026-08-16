"""Attachment-specific checks for the PostgreSQL Direct Answer loader."""

import hashlib
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

import pytest

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.agent_runtime.adapters.execution import (
    AttachmentRow,
    DirectAnswerRunLoadError,
    DirectAnswerRunNotExecutableError,
    SqlAlchemyDirectAnswerRunLoader,
)
from industry_platform.modules.agent_runtime.model import MAX_MODEL_IMAGE_BYTES
from industry_platform.modules.agent_runtime.runtime_contracts import (
    DirectAnswerRuntimePolicy,
)
from industry_platform.modules.files.domain import (
    AttachmentKind,
    AttachmentMediaType,
    FileObjectStatus,
)

WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
TEXT_FILE_ID = UUID("22222222-2222-4222-8222-222222222222")
IMAGE_FILE_ID = UUID("33333333-3333-4333-8333-333333333333")


@dataclass(slots=True)
class ObjectReaderStub:
    data: bytes
    calls: list[tuple[str, str, int]] = field(default_factory=list)

    async def read_bounded(
        self,
        *,
        bucket: str,
        object_key: str,
        maximum_bytes: int,
    ) -> bytes:
        self.calls.append((bucket, object_key, maximum_bytes))
        return self.data


def loader(reader: ObjectReaderStub | None = None) -> SqlAlchemyDirectAnswerRunLoader:
    return SqlAlchemyDirectAnswerRunLoader(
        session_factory=cast(AsyncSessionFactory, object()),
        policy=cast(DirectAnswerRuntimePolicy, object()),
        attachment_object_reader=reader,
    )


def text_row(text: str = "bounded text attachment") -> AttachmentRow:
    content = text.encode()
    return (
        0,
        TEXT_FILE_ID,
        WORKSPACE_ID,
        FileObjectStatus.READY,
        AttachmentKind.TEXT,
        AttachmentMediaType.TEXT_PLAIN,
        "private-files",
        "ready/text-object",
        len(content),
        hashlib.sha256(content).hexdigest(),
        text,
        "chat-attachment-parser-v1",
        None,
        None,
    )


def image_row(data: bytes, *, ordinal: int = 1) -> AttachmentRow:
    return (
        ordinal,
        IMAGE_FILE_ID,
        WORKSPACE_ID,
        FileObjectStatus.READY,
        AttachmentKind.IMAGE,
        AttachmentMediaType.IMAGE_PNG,
        "private-files",
        "ready/image-object",
        len(data),
        hashlib.sha256(data).hexdigest(),
        None,
        "chat-attachment-parser-v1",
        640,
        480,
    )


@pytest.mark.asyncio
async def test_loader_builds_ordered_text_and_verified_image_context() -> None:
    image_data = b"sanitized-private-image"
    reader = ObjectReaderStub(image_data)

    attachments = await loader(reader)._load_attachments(
        workspace_id=WORKSPACE_ID,
        rows=(text_row(), image_row(image_data)),
    )

    assert [attachment.ordinal for attachment in attachments] == [1, 2]
    assert attachments[0].extracted_text == "bounded text attachment"
    assert attachments[1].image_part is not None
    assert attachments[1].image_part.data == image_data
    assert reader.calls == [("private-files", "ready/image-object", MAX_MODEL_IMAGE_BYTES)]


@pytest.mark.asyncio
async def test_loader_rejects_non_ready_or_non_contiguous_associations() -> None:
    not_ready = list(text_row())
    not_ready[3] = FileObjectStatus.PROCESSING
    with pytest.raises(DirectAnswerRunNotExecutableError):
        await loader()._load_attachments(
            workspace_id=WORKSPACE_ID,
            rows=(cast(AttachmentRow, tuple(not_ready)),),
        )

    wrong_ordinal = list(text_row())
    wrong_ordinal[0] = 1
    with pytest.raises(DirectAnswerRunNotExecutableError):
        await loader()._load_attachments(
            workspace_id=WORKSPACE_ID,
            rows=(cast(AttachmentRow, tuple(wrong_ordinal)),),
        )


@pytest.mark.asyncio
async def test_loader_fails_closed_when_private_image_bytes_do_not_match_metadata() -> None:
    declared_data = b"declared-image"

    with pytest.raises(DirectAnswerRunLoadError):
        await loader(ObjectReaderStub(b"different-image"))._load_attachments(
            workspace_id=WORKSPACE_ID,
            rows=(image_row(declared_data, ordinal=0),),
        )
