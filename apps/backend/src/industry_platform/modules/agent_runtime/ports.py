"""Technology-independent boundaries owned by the unified Agent Runtime."""

from collections.abc import AsyncIterator
from typing import Protocol
from uuid import UUID

from industry_platform.modules.agent_runtime.checkpoints import (
    CheckpointEnvelope,
    LoadCheckpointRequest,
    SaveCheckpointCommand,
)
from industry_platform.modules.agent_runtime.context import (
    CompiledContext,
    ContextCompilationInput,
    ContextManifest,
)
from industry_platform.modules.agent_runtime.events import AgentEvent
from industry_platform.modules.agent_runtime.model import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelStreamItem,
)


class ModelProvider(Protocol):
    """Invoke a model without exposing any vendor SDK to the Runtime."""

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        """Return a directly iterable normalized response stream."""

        ...

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Return one normalized non-streaming response."""

        ...


class ContextTokenCounter(Protocol):
    """Estimate model input with one named, replaceable counting policy."""

    @property
    def version(self) -> str:
        """Return the stable counter version recorded in each manifest."""

        ...

    def count(self, *, model: str, messages: tuple[ModelMessage, ...]) -> int:
        """Return a conservative positive estimate for the supplied messages."""

        ...


class ContextCompiler(Protocol):
    """Turn explicit safe sources into one Provider request and audit manifest."""

    def compile(self, compilation: ContextCompilationInput) -> CompiledContext:
        """Compile without loading permissions, dependencies, or Secrets itself."""

        ...


class ContextManifestStoreError(RuntimeError):
    """A sanitized failure to commit the Context manifest fact."""


class ContextManifestStore(Protocol):
    """Persist a manifest before the matching Provider call begins."""

    async def save(self, manifest: ContextManifest) -> None:
        """Save idempotently or reject a conflicting manifest for the same Step."""

        ...


class AgentEventCommitter(Protocol):
    """Commit an Event before Runtime exposes it to Harness or another observer."""

    async def append(self, event: AgentEvent) -> None:
        """Append by stream sequence, accepting only an identical idempotent replay."""

        ...


class CancellationProbe(Protocol):
    """Read the explicit persisted cancellation request at Runtime safe points."""

    async def is_cancel_requested(self, *, run_id: UUID, workspace_id: UUID) -> bool:
        """Return true only for an authorized explicit Run cancellation request."""

        ...


class TrajectoryRecorder(Protocol):
    """Observe sanitized Events without becoming a recovery fact source."""

    async def record(self, event: AgentEvent) -> None:
        """Record one already committed or commit-ready Agent Event."""

        ...


class CheckpointStore(Protocol):
    """Persist and retrieve versioned Run State using optimistic CAS."""

    async def save(self, command: SaveCheckpointCommand) -> CheckpointEnvelope:
        """Create revision zero or save exactly the expected successor."""

        ...

    async def load(self, request: LoadCheckpointRequest) -> CheckpointEnvelope:
        """Load one requested revision or the latest available revision."""

        ...


class ToolExecutor[CallT, RuntimeContextT, ResultT](Protocol):
    """Execute a typed capability without defining Day 3 Tool contracts early."""

    async def execute(
        self,
        call: CallT,
        runtime_context: RuntimeContextT,
    ) -> ResultT:
        """Execute one already validated typed call in trusted context."""

        ...


class AgentRuntime[CommandT, RuntimeContextT](Protocol):
    """Single production execution entry used by API, Worker, and Harness."""

    def run(
        self,
        command: CommandT,
        runtime_context: RuntimeContextT,
    ) -> AsyncIterator[AgentEvent]:
        """Return an Event stream that can be consumed directly with async-for."""

        ...
