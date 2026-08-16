import { ApiProblem, authenticatedFetch } from "../api/api";

const MAX_SSE_FRAME_CHARACTERS = 1_000_000;
const MAX_RETRY_DELAY_MS = 60_000;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const DECIMAL_SEQUENCE_PATTERN = /^(?:0|[1-9][0-9]*)$/u;

const KNOWN_AGENT_EVENT_TYPES = [
  "agent.run.queued",
  "agent.run.started",
  "agent.run.paused",
  "agent.run.resumed",
  "agent.run.completed",
  "agent.run.failed",
  "agent.run.cancelled",
  "agent.step.started",
  "agent.step.completed",
  "agent.step.failed",
  "agent.model.started",
  "agent.model.delta",
  "agent.model.completed",
  "agent.artifact.created",
  "agent.checkpoint.saved",
] as const;

const knownAgentEventTypes = new Set<string>(KNOWN_AGENT_EVENT_TYPES);
const terminalAgentEventTypes = new Set<string>([
  "agent.run.completed",
  "agent.run.failed",
  "agent.run.cancelled",
]);
const agentRunStatuses = new Set<string>(["queued", "running", "completed", "failed", "cancelled"]);
const terminalAgentRunStatuses = new Set<string>(["completed", "failed", "cancelled"]);

export type AgentEventType = (typeof KNOWN_AGENT_EVENT_TYPES)[number];
export type JsonValue =
  boolean | number | string | null | readonly JsonValue[] | { readonly [key: string]: JsonValue };

interface AgentStreamEnvelope {
  readonly schema_version: number;
  readonly stream_id: string;
  readonly sequence: number;
  readonly occurred_at: string;
  readonly trace_id: string;
  readonly payload: Readonly<Record<string, JsonValue>>;
}

export interface AgentBusinessEvent extends AgentStreamEnvelope {
  readonly type: AgentEventType;
}

export interface AgentStreamSnapshot extends AgentStreamEnvelope {
  readonly type: "stream.snapshot";
}

export type AgentStreamEvent = AgentBusinessEvent | AgentStreamSnapshot;
export type AgentStreamConnectionState = "connecting" | "open" | "reconnecting" | "closed";

export interface FollowAgentRunEventsOptions {
  readonly workspaceId: string;
  readonly runId: string;
  readonly cursor?: number;
  readonly signal?: AbortSignal;
  readonly retryDelayMs?: number;
  readonly onEvent: (event: AgentStreamEvent) => void | Promise<void>;
  readonly onConnectionState: (state: AgentStreamConnectionState) => void;
}

export class AgentStreamContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AgentStreamContractError";
  }
}

class AgentStreamConsumerError extends Error {
  constructor(cause: unknown) {
    super("The Agent stream consumer rejected an Event.", { cause });
    this.name = "AgentStreamConsumerError";
  }
}

interface SseFrame {
  readonly data: string;
  readonly event: string | null;
  readonly id: string | null;
}

interface ParsedEnvelope extends AgentStreamEnvelope {
  readonly type: string;
}

interface StreamCursorState {
  sequence: number;
  streamId: string | null;
  lastSnapshotSequence: number | null;
}

class SseFrameDecoder {
  readonly #maximumCharacters: number;
  #buffer = "";

  constructor(maximumCharacters = MAX_SSE_FRAME_CHARACTERS) {
    this.#maximumCharacters = maximumCharacters;
  }

  push(chunk: string): SseFrame[] {
    this.#buffer += chunk;
    const frames: SseFrame[] = [];
    for (;;) {
      const boundary = findFrameBoundary(this.#buffer);
      if (boundary === null) {
        if (this.#buffer.length > this.#maximumCharacters) {
          throw new AgentStreamContractError("An Agent SSE frame exceeded the client limit.");
        }
        return frames;
      }
      if (boundary.index > this.#maximumCharacters) {
        throw new AgentStreamContractError("An Agent SSE frame exceeded the client limit.");
      }
      const frameText = this.#buffer.slice(0, boundary.index);
      this.#buffer = this.#buffer.slice(boundary.index + boundary.length);
      const frame = parseSseFrame(frameText);
      if (frame !== null) {
        frames.push(frame);
      }
    }
  }
}

function findFrameBoundary(
  value: string,
): { readonly index: number; readonly length: number } | null {
  const separators = ["\r\n\r\n", "\r\n\n", "\n\r\n", "\n\n", "\r\r"] as const;
  let selected: { readonly index: number; readonly length: number } | null = null;
  for (const separator of separators) {
    const index = value.indexOf(separator);
    if (index >= 0 && (selected === null || index < selected.index)) {
      selected = { index, length: separator.length };
    }
  }
  return selected;
}

function parseSseFrame(value: string): SseFrame | null {
  const dataLines: string[] = [];
  let event: string | null = null;
  let id: string | null = null;
  for (const line of value.split(/\r\n|\r|\n/u)) {
    if (line === "" || line.startsWith(":")) {
      continue;
    }
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let fieldValue = colon < 0 ? "" : line.slice(colon + 1);
    if (fieldValue.startsWith(" ")) {
      fieldValue = fieldValue.slice(1);
    }
    if (field === "data") {
      dataLines.push(fieldValue);
    } else if (field === "event") {
      event = fieldValue;
    } else if (field === "id" && !fieldValue.includes("\0")) {
      id = fieldValue;
    }
  }
  if (dataLines.length === 0) {
    return null;
  }
  return { data: dataLines.join("\n"), event, id };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isJsonValue(value: unknown): value is JsonValue {
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "string" ||
    (typeof value === "number" && Number.isFinite(value))
  ) {
    return true;
  }
  if (Array.isArray(value)) {
    return value.every((item) => isJsonValue(item));
  }
  return isRecord(value) && Object.values(value).every((item) => isJsonValue(item));
}

function requiredString(
  value: Record<string, unknown>,
  fieldName: string,
  maximumLength = Number.MAX_SAFE_INTEGER,
): string {
  const field = value[fieldName];
  if (typeof field !== "string" || field.length === 0 || field.length > maximumLength) {
    throw new AgentStreamContractError(`Agent Event field ${fieldName} is invalid.`);
  }
  return field;
}

function requiredSequence(value: Record<string, unknown>): number {
  const sequence = value.sequence;
  if (!Number.isSafeInteger(sequence) || typeof sequence !== "number" || sequence < 0) {
    throw new AgentStreamContractError("Agent Event sequence is invalid.");
  }
  return sequence;
}

function parseEnvelope(frame: SseFrame): ParsedEnvelope {
  if (frame.id === null || !DECIMAL_SEQUENCE_PATTERN.test(frame.id)) {
    throw new AgentStreamContractError("Agent SSE Event ID is invalid.");
  }
  const eventId = Number(frame.id);
  if (!Number.isSafeInteger(eventId)) {
    throw new AgentStreamContractError("Agent SSE Event ID exceeds the browser integer limit.");
  }

  let decoded: unknown;
  try {
    decoded = JSON.parse(frame.data) as unknown;
  } catch {
    throw new AgentStreamContractError("Agent SSE data is not valid JSON.");
  }
  if (!isRecord(decoded)) {
    throw new AgentStreamContractError("Agent SSE data must be an object.");
  }

  const schemaVersion = decoded.schema_version;
  if (
    typeof schemaVersion !== "number" ||
    !Number.isSafeInteger(schemaVersion) ||
    schemaVersion !== 1
  ) {
    throw new AgentStreamContractError("Agent Event schema version is unsupported.");
  }
  const sequence = requiredSequence(decoded);
  if (sequence !== eventId) {
    throw new AgentStreamContractError("Agent Event ID does not match its envelope sequence.");
  }
  const type = requiredString(decoded, "type", 128);
  if (frame.event === null || frame.event !== type) {
    throw new AgentStreamContractError("Agent SSE event name does not match its envelope type.");
  }
  if (type !== "stream.snapshot" && sequence < 1) {
    throw new AgentStreamContractError("A business Agent Event requires a positive sequence.");
  }
  const streamId = requiredString(decoded, "stream_id", 36);
  if (!UUID_PATTERN.test(streamId)) {
    throw new AgentStreamContractError("Agent Event stream ID is invalid.");
  }
  const occurredAt = requiredString(decoded, "occurred_at", 64);
  if (Number.isNaN(Date.parse(occurredAt))) {
    throw new AgentStreamContractError("Agent Event occurrence time is invalid.");
  }
  const traceId = requiredString(decoded, "trace_id", 128);
  const payload = decoded.payload;
  if (!isRecord(payload) || !isJsonValue(payload)) {
    throw new AgentStreamContractError("Agent Event payload is invalid.");
  }
  return {
    occurred_at: occurredAt,
    payload,
    schema_version: schemaVersion,
    sequence,
    stream_id: streamId,
    trace_id: traceId,
    type,
  };
}

function requireSnapshotNonNegativeInteger(
  payload: Readonly<Record<string, JsonValue>>,
  fieldName: string,
): number {
  const value = payload[fieldName];
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new AgentStreamContractError(`Agent snapshot field ${fieldName} is invalid.`);
  }
  return value;
}

function validateSnapshotPayload(payload: Readonly<Record<string, JsonValue>>): void {
  const runId = payload.run_id;
  const status = payload.status;
  const stopReason = payload.stop_reason;
  const terminal = payload.terminal;
  const contentMarkdown = payload.content_markdown;
  if (typeof runId !== "string" || !UUID_PATTERN.test(runId)) {
    throw new AgentStreamContractError("Agent snapshot Run ID is invalid.");
  }
  if (typeof status !== "string" || !agentRunStatuses.has(status)) {
    throw new AgentStreamContractError("Agent snapshot status is invalid.");
  }
  if (typeof terminal !== "boolean" || terminal !== terminalAgentRunStatuses.has(status)) {
    throw new AgentStreamContractError("Agent snapshot terminal state is inconsistent.");
  }
  if (
    (stopReason !== null &&
      (typeof stopReason !== "string" || stopReason.length === 0 || stopReason.length > 128)) ||
    (terminal && stopReason === null) ||
    (!terminal && stopReason !== null) ||
    (status === "completed" && stopReason !== "final") ||
    (status === "cancelled" && stopReason !== "cancelled")
  ) {
    throw new AgentStreamContractError("Agent snapshot stop reason is inconsistent.");
  }
  if (typeof contentMarkdown !== "string" || contentMarkdown.length > MAX_SSE_FRAME_CHARACTERS) {
    throw new AgentStreamContractError("Agent snapshot content is invalid.");
  }
  const inputTokens = requireSnapshotNonNegativeInteger(payload, "input_tokens");
  const cachedInputTokens = requireSnapshotNonNegativeInteger(payload, "cached_input_tokens");
  requireSnapshotNonNegativeInteger(payload, "output_tokens");
  requireSnapshotNonNegativeInteger(payload, "cost_micro_usd");
  if (cachedInputTokens > inputTokens) {
    throw new AgentStreamContractError("Agent snapshot cached usage is inconsistent.");
  }
}

function publicEvent(envelope: ParsedEnvelope): AgentStreamEvent | null {
  if (envelope.type === "stream.snapshot") {
    validateSnapshotPayload(envelope.payload);
    return { ...envelope, type: "stream.snapshot" };
  }
  if (!knownAgentEventTypes.has(envelope.type)) {
    return null;
  }
  return { ...envelope, type: envelope.type as AgentEventType };
}

function advanceCursor(state: StreamCursorState, envelope: ParsedEnvelope): boolean {
  if (state.streamId === null) {
    state.streamId = envelope.stream_id;
  } else if (state.streamId !== envelope.stream_id) {
    throw new AgentStreamContractError("An Agent stream changed identity while being followed.");
  }

  if (envelope.type === "stream.snapshot") {
    if (envelope.sequence < state.sequence) {
      return false;
    }
    if (state.lastSnapshotSequence === envelope.sequence) {
      return false;
    }
    state.sequence = envelope.sequence;
    state.lastSnapshotSequence = envelope.sequence;
    return true;
  }
  if (envelope.sequence <= state.sequence) {
    return false;
  }
  if (envelope.sequence !== state.sequence + 1) {
    throw new AgentStreamContractError("Agent Events contain a sequence gap.");
  }
  state.sequence = envelope.sequence;
  return true;
}

function isAbortError(error: unknown, signal: AbortSignal | undefined): boolean {
  return (
    signal?.aborted === true ||
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

function isSignalAborted(signal: AbortSignal | undefined): boolean {
  return signal?.aborted ?? false;
}

function isRetryableTransportError(error: unknown): boolean {
  if (error instanceof ApiProblem) {
    return error.status === 408 || error.status === 429 || error.status >= 500;
  }
  return error instanceof TypeError;
}

function waitForRetry(delayMs: number, signal: AbortSignal | undefined): Promise<void> {
  if (delayMs === 0 || signal?.aborted === true) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const timer = window.setTimeout(finish, delayMs);
    function finish(): void {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", finish);
      resolve();
    }
    signal?.addEventListener("abort", finish, { once: true });
  });
}

function agentEventsUrl(workspaceId: string, runId: string): URL {
  return new URL(
    `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/agent-runs/${encodeURIComponent(runId)}/events`,
    window.location.origin,
  );
}

async function consumeResponse(
  response: Response,
  state: StreamCursorState,
  options: FollowAgentRunEventsOptions,
): Promise<boolean> {
  if (response.body === null) {
    throw new AgentStreamContractError("The Agent SSE response has no body.");
  }
  const reader = response.body.getReader();
  const textDecoder = new TextDecoder();
  const frameDecoder = new SseFrameDecoder();
  try {
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) {
        frameDecoder.push(textDecoder.decode());
        return false;
      }
      const frames = frameDecoder.push(textDecoder.decode(chunk.value, { stream: true }));
      for (const frame of frames) {
        const envelope = parseEnvelope(frame);
        if (!advanceCursor(state, envelope)) {
          continue;
        }
        const event = publicEvent(envelope);
        if (event !== null) {
          try {
            await options.onEvent(event);
          } catch (error: unknown) {
            throw new AgentStreamConsumerError(error);
          }
        }
        if (
          terminalAgentEventTypes.has(envelope.type) ||
          (envelope.type === "stream.snapshot" && envelope.payload.terminal === true)
        ) {
          await reader.cancel();
          return true;
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/**
 * Follow one committed Agent Event stream until a terminal Event or caller abort.
 * A transport close reconnects from the last validated sequence and never starts
 * or resumes model execution by itself.
 */
export async function followAgentRunEvents(options: FollowAgentRunEventsOptions): Promise<number> {
  const initialCursor = options.cursor ?? 0;
  const retryDelayMs = options.retryDelayMs ?? 750;
  if (!Number.isSafeInteger(initialCursor) || initialCursor < 0) {
    throw new RangeError("The initial Agent Event cursor is invalid.");
  }
  if (
    !Number.isSafeInteger(retryDelayMs) ||
    retryDelayMs < 0 ||
    retryDelayMs > MAX_RETRY_DELAY_MS
  ) {
    throw new RangeError("The Agent stream retry delay is invalid.");
  }

  const state: StreamCursorState = {
    lastSnapshotSequence: null,
    sequence: initialCursor,
    streamId: null,
  };
  let connectionState: AgentStreamConnectionState | null = null;
  let firstAttempt = true;
  function report(next: AgentStreamConnectionState): void {
    if (connectionState !== next) {
      connectionState = next;
      options.onConnectionState(next);
    }
  }

  if (isSignalAborted(options.signal)) {
    report("closed");
    return state.sequence;
  }

  for (;;) {
    report(firstAttempt ? "connecting" : "reconnecting");
    const headers = new Headers({ Accept: "text/event-stream" });
    if (state.sequence > 0) {
      headers.set("Last-Event-ID", String(state.sequence));
    }
    try {
      const requestInit: RequestInit =
        options.signal === undefined
          ? { cache: "no-store", headers, method: "GET" }
          : { cache: "no-store", headers, method: "GET", signal: options.signal };
      const response = await authenticatedFetch(
        agentEventsUrl(options.workspaceId, options.runId),
        requestInit,
      );
      const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
      if (!contentType.startsWith("text/event-stream")) {
        throw new AgentStreamContractError("The Agent Event endpoint did not return SSE.");
      }
      report("open");
      const terminal = await consumeResponse(response, state, options);
      if (terminal) {
        report("closed");
        return state.sequence;
      }
    } catch (error: unknown) {
      if (isAbortError(error, options.signal)) {
        report("closed");
        return state.sequence;
      }
      if (!isRetryableTransportError(error)) {
        report("closed");
        throw error;
      }
    }

    firstAttempt = false;
    report("reconnecting");
    await waitForRetry(retryDelayMs, options.signal);
    if (isSignalAborted(options.signal)) {
      report("closed");
      return state.sequence;
    }
  }
}
