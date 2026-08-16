"""Versioned final-answer contract produced by the unified Agent Runtime."""

import re
from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    RunStopReason,
    require_current_schema_version,
    require_non_nil_uuid,
)
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelResponse,
    ModelUsage,
)

FINAL_MARKDOWN_CONTRACT_VERSION: Final = "final-markdown-v1"
MAX_FINAL_MARKDOWN_LENGTH: Final = 2_000_000

_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")


@dataclass(frozen=True, slots=True)
class DirectAnswerFinalOutput:
    """One successful Markdown answer plus its auditable model accounting."""

    schema_version: int
    contract_version: str
    run_id: UUID
    step_id: UUID
    workspace_id: UUID
    model: str
    usage: ModelUsage
    content_markdown: str = field(repr=False)
    provider_request_id: str | None = None
    format: str = field(default="markdown", init=False)
    finish_reason: ModelFinishReason = field(default=ModelFinishReason.STOP, init=False)
    stop_reason: RunStopReason = field(default=RunStopReason.FINAL, init=False)

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        if self.contract_version != FINAL_MARKDOWN_CONTRACT_VERSION:
            raise ValueError("Final output contract version is unsupported")
        for identifier, field_name in (
            (self.run_id, "Final output Run ID"),
            (self.step_id, "Final output Step ID"),
            (self.workspace_id, "Final output Workspace ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if not _MODEL_PATTERN.fullmatch(self.model):
            raise ValueError("Final output model is invalid")
        if (
            not self.content_markdown.strip()
            or len(self.content_markdown) > MAX_FINAL_MARKDOWN_LENGTH
        ):
            raise ValueError("Final Markdown content is invalid")

    @classmethod
    def from_response(
        cls,
        *,
        contract_version: str,
        run_id: UUID,
        step_id: UUID,
        workspace_id: UUID,
        response: ModelResponse,
    ) -> "DirectAnswerFinalOutput":
        """Wrap only a complete normal stop; truncation or refusal is not success."""

        if response.finish_reason is not ModelFinishReason.STOP:
            raise ValueError("Only a normally completed model response can become final output")
        return cls(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            contract_version=contract_version,
            run_id=run_id,
            step_id=step_id,
            workspace_id=workspace_id,
            model=response.model,
            usage=response.usage,
            content_markdown=response.output_text,
            provider_request_id=response.provider_request_id,
        )

    def to_event_payload(self) -> dict[str, object]:
        """Return flat public JSON fields; ordinary final text is not an Artifact."""

        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "contract_version": self.contract_version,
            "format": self.format,
            "content_markdown": self.content_markdown,
            "model": self.model,
            "finish_reason": self.finish_reason.value,
            "stop_reason": self.stop_reason.value,
        }
        if self.provider_request_id is not None:
            payload["provider_request_id"] = self.provider_request_id
        return payload
