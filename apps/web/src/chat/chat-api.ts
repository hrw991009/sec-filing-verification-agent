import type { components } from "@sec-filing-verification/api-contract";

import { ApiProblem, apiClient, assertNoContent, unwrapData, withAccessToken } from "../api/api";

export { followAgentRunEvents } from "./agent-stream";
export type {
  AgentStreamConnectionState,
  AgentStreamEvent,
  FollowAgentRunEventsOptions,
} from "./agent-stream";

export type ConversationSummary = components["schemas"]["ConversationSummaryResponse"];
export type ConversationDetail = components["schemas"]["ConversationDetailResponse"];
export type ConversationMessage = components["schemas"]["ConversationMessageResponse"];
export type ConversationAttachment = components["schemas"]["ConversationAttachmentResponse"];
export type FileUpload = components["schemas"]["FileUploadResponse"];
export type FileSnapshot = components["schemas"]["FileResponse"];
export type FileDownload = components["schemas"]["FileDownloadResponse"];
export type AttachmentMediaType = components["schemas"]["AttachmentMediaType"];
export type StartTurnRequest = components["schemas"]["StartConversationTurnRequest"];
export type StartTurnReceipt = components["schemas"]["StartConversationTurnResponse"];
export type CreateMemoryCandidateRequest = components["schemas"]["CreateMemoryCandidateRequest"];
export type MemoryCandidate = components["schemas"]["MemoryCandidateResponse"];
export type MemoryCandidateCreated = components["schemas"]["MemoryCandidateCreatedResponse"];
export type ResolveMemoryCandidateRequest = components["schemas"]["ResolveMemoryCandidateRequest"];
export type MemoryResolution = components["schemas"]["MemoryResolutionResponse"];
export type MemorySnapshot = components["schemas"]["MemoryResponse"];
export type MemoryDetail = components["schemas"]["MemoryDetailResponse"];
export type UpdateMemoryRequest = components["schemas"]["UpdateMemoryRequest"];
export type RecordMemoryFeedbackRequest = components["schemas"]["RecordMemoryFeedbackRequest"];
export type MemoryFeedback = components["schemas"]["MemoryFeedbackResponse"];

export interface ConversationPage {
  readonly conversations: ConversationSummary[];
  readonly next_cursor: string | null;
}

export interface ConversationMessagePage {
  readonly messages: ConversationMessage[];
  readonly next_cursor: string | null;
}

export interface PageOptions {
  readonly cursor?: string;
  readonly limit?: number;
}

export interface MemoryCandidateListOptions {
  readonly conversationId?: string;
  readonly limit?: number;
}

export interface MemoryListOptions {
  readonly query?: string;
  readonly status?: components["schemas"]["MemoryStatus"];
  readonly scope?: components["schemas"]["MemoryScope"];
  readonly kind?: components["schemas"]["MemoryKind"];
  readonly limit?: number;
}

export type TraceUsage = components["schemas"]["TraceUsageResponse"];
export type AgentTraceRun = components["schemas"]["TraceRunResponse"];
export type AgentTraceStep = components["schemas"]["TraceStepResponse"];
export type AgentTraceContextSource = components["schemas"]["ContextSourceResponse"];
export type AgentTraceContextManifest = components["schemas"]["ContextManifestResponse"];
export type AgentTraceEvent = components["schemas"]["TraceEventResponse"];
export type AgentTrace = components["schemas"]["AgentTraceResponse"];

export class AgentTraceContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AgentTraceContractError";
  }
}

interface ApiResult<T> {
  readonly data?: T;
  readonly error?: unknown;
  readonly response: Response;
}

interface ApiCommandResult {
  readonly error?: unknown;
  readonly response: Response;
}

const DEFAULT_PAGE_SIZE = 20;
const MAX_PAGE_SIZE = 100;
const MAX_ATTACHMENT_BYTES = 5_000_000;
const MAX_TEXT_ATTACHMENT_BYTES = 1_048_576;
const IDEMPOTENCY_KEY_PATTERN = /^[\x21-\x7e]{1,200}$/u;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;

const attachmentMediaTypeByExtension: Readonly<Record<string, AttachmentMediaType>> = {
  ".jpeg": "image/jpeg",
  ".jpg": "image/jpeg",
  ".markdown": "text/markdown",
  ".md": "text/markdown",
  ".png": "image/png",
  ".txt": "text/plain",
  ".webp": "image/webp",
};

function authorization(accessToken: string): { readonly Authorization: string } {
  return { Authorization: `Bearer ${accessToken}` };
}

function pageQuery(options: PageOptions): { readonly limit: number; readonly cursor?: string } {
  const limit = options.limit ?? DEFAULT_PAGE_SIZE;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > MAX_PAGE_SIZE) {
    throw new RangeError("The page size must be between 1 and 100.");
  }
  return options.cursor === undefined ? { limit } : { cursor: options.cursor, limit };
}

function authenticatedData<T>(request: (accessToken: string) => Promise<ApiResult<T>>): Promise<T> {
  return withAccessToken(async (accessToken) => unwrapData<T>(await request(accessToken)));
}

function authenticatedCommand(
  request: (accessToken: string) => Promise<ApiCommandResult>,
): Promise<void> {
  return withAccessToken(async (accessToken) => {
    assertNoContent(await request(accessToken));
  });
}

export function listConversations(
  workspaceId: string,
  options: PageOptions = {},
): Promise<ConversationPage> {
  return authenticatedData((accessToken) =>
    apiClient.GET("/api/v1/workspaces/{workspace_id}/conversations", {
      headers: authorization(accessToken),
      params: {
        path: { workspace_id: workspaceId },
        query: pageQuery(options),
      },
    }),
  );
}

export function getConversation(
  workspaceId: string,
  conversationId: string,
): Promise<ConversationDetail> {
  return authenticatedData((accessToken) =>
    apiClient.GET("/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}", {
      headers: authorization(accessToken),
      params: {
        path: { conversation_id: conversationId, workspace_id: workspaceId },
      },
    }),
  );
}

export function listMessages(
  workspaceId: string,
  conversationId: string,
  options: PageOptions = {},
): Promise<ConversationMessagePage> {
  return authenticatedData((accessToken) =>
    apiClient.GET("/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages", {
      headers: authorization(accessToken),
      params: {
        path: { conversation_id: conversationId, workspace_id: workspaceId },
        query: pageQuery(options),
      },
    }),
  );
}

export function startTurn(
  workspaceId: string,
  request: StartTurnRequest,
  idempotencyKey: string,
): Promise<StartTurnReceipt> {
  if (!IDEMPOTENCY_KEY_PATTERN.test(idempotencyKey)) {
    throw new TypeError("The Turn idempotency key is invalid.");
  }
  return authenticatedData((accessToken) =>
    apiClient.POST("/api/v1/workspaces/{workspace_id}/conversations", {
      body: request,
      headers: authorization(accessToken),
      params: {
        header: { "Idempotency-Key": idempotencyKey },
        path: { workspace_id: workspaceId },
      },
    }),
  );
}

export function renameConversation(
  workspaceId: string,
  conversationId: string,
  title: string,
): Promise<ConversationSummary> {
  return authenticatedData((accessToken) =>
    apiClient.PATCH("/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}", {
      body: { title },
      headers: authorization(accessToken),
      params: {
        path: { conversation_id: conversationId, workspace_id: workspaceId },
      },
    }),
  );
}

export function deleteConversation(workspaceId: string, conversationId: string): Promise<void> {
  return authenticatedCommand((accessToken) =>
    apiClient.DELETE("/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}", {
      headers: authorization(accessToken),
      params: {
        path: { conversation_id: conversationId, workspace_id: workspaceId },
      },
    }),
  );
}

export function createMemoryCandidate(
  workspaceId: string,
  request: CreateMemoryCandidateRequest,
  idempotencyKey: string,
): Promise<MemoryCandidateCreated> {
  if (!IDEMPOTENCY_KEY_PATTERN.test(idempotencyKey)) {
    throw new TypeError("The Memory idempotency key is invalid.");
  }
  return authenticatedData((accessToken) =>
    apiClient.POST("/api/v1/workspaces/{workspace_id}/memories/candidates", {
      body: request,
      headers: authorization(accessToken),
      params: {
        header: { "Idempotency-Key": idempotencyKey },
        path: { workspace_id: workspaceId },
      },
    }),
  );
}

export function listMemoryCandidates(
  workspaceId: string,
  options: MemoryCandidateListOptions = {},
): Promise<MemoryCandidate[]> {
  const limit = options.limit ?? DEFAULT_PAGE_SIZE;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > MAX_PAGE_SIZE) {
    throw new RangeError("The Memory candidate page size must be between 1 and 100.");
  }
  return authenticatedData(async (accessToken) => {
    const result = await apiClient.GET("/api/v1/workspaces/{workspace_id}/memories/candidates", {
      headers: authorization(accessToken),
      params: {
        path: { workspace_id: workspaceId },
        query: {
          conversation_id: options.conversationId ?? null,
          limit,
        },
      },
    });
    const page = unwrapData<components["schemas"]["MemoryCandidateCollectionResponse"]>(result);
    return { data: page.candidates, response: result.response };
  });
}

export function getMemoryCandidate(
  workspaceId: string,
  candidateId: string,
): Promise<MemoryCandidate> {
  return authenticatedData((accessToken) =>
    apiClient.GET("/api/v1/workspaces/{workspace_id}/memories/candidates/{candidate_id}", {
      headers: authorization(accessToken),
      params: { path: { candidate_id: candidateId, workspace_id: workspaceId } },
    }),
  );
}

export function confirmMemoryCandidate(
  workspaceId: string,
  candidateId: string,
  revision: number,
  request: ResolveMemoryCandidateRequest,
): Promise<MemoryResolution> {
  if (!Number.isSafeInteger(revision) || revision < 1) {
    throw new RangeError("The Memory candidate revision is invalid.");
  }
  return authenticatedData((accessToken) =>
    apiClient.POST("/api/v1/workspaces/{workspace_id}/memories/candidates/{candidate_id}/confirm", {
      body: request,
      headers: authorization(accessToken),
      params: {
        header: { "If-Match": `"${String(revision)}"` },
        path: { candidate_id: candidateId, workspace_id: workspaceId },
      },
    }),
  );
}

export function rejectMemoryCandidate(
  workspaceId: string,
  candidateId: string,
  revision: number,
): Promise<MemoryCandidate> {
  if (!Number.isSafeInteger(revision) || revision < 1) {
    throw new RangeError("The Memory candidate revision is invalid.");
  }
  return authenticatedData((accessToken) =>
    apiClient.POST("/api/v1/workspaces/{workspace_id}/memories/candidates/{candidate_id}/reject", {
      headers: authorization(accessToken),
      params: {
        header: { "If-Match": `"${String(revision)}"` },
        path: { candidate_id: candidateId, workspace_id: workspaceId },
      },
    }),
  );
}

export function listMemories(
  workspaceId: string,
  options: MemoryListOptions | number = {},
): Promise<MemorySnapshot[]> {
  const normalized = typeof options === "number" ? { limit: options } : options;
  const limit = normalized.limit ?? DEFAULT_PAGE_SIZE;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > MAX_PAGE_SIZE) {
    throw new RangeError("The Memory page size must be between 1 and 100.");
  }
  return authenticatedData(async (accessToken) => {
    const result = await apiClient.GET("/api/v1/workspaces/{workspace_id}/memories", {
      headers: authorization(accessToken),
      params: {
        path: { workspace_id: workspaceId },
        query: {
          kind: normalized.kind ?? null,
          limit,
          query: normalized.query ?? null,
          scope: normalized.scope ?? null,
          status: normalized.status ?? null,
        },
      },
    });
    const page = unwrapData<components["schemas"]["MemoryCollectionResponse"]>(result);
    return { data: page.memories, response: result.response };
  });
}

export function getMemory(workspaceId: string, memoryId: string): Promise<MemoryDetail> {
  return authenticatedData((accessToken) =>
    apiClient.GET("/api/v1/workspaces/{workspace_id}/memories/{memory_id}", {
      headers: authorization(accessToken),
      params: { path: { memory_id: memoryId, workspace_id: workspaceId } },
    }),
  );
}

function memoryRevisionHeader(revision: number): { readonly "If-Match": string } {
  if (!Number.isSafeInteger(revision) || revision < 1) {
    throw new RangeError("The Memory resource revision is invalid.");
  }
  return { "If-Match": `"${String(revision)}"` };
}

export function updateMemory(
  workspaceId: string,
  memoryId: string,
  revision: number,
  request: UpdateMemoryRequest,
): Promise<MemoryDetail> {
  return authenticatedData((accessToken) =>
    apiClient.PATCH("/api/v1/workspaces/{workspace_id}/memories/{memory_id}", {
      body: request,
      headers: authorization(accessToken),
      params: {
        header: memoryRevisionHeader(revision),
        path: { memory_id: memoryId, workspace_id: workspaceId },
      },
    }),
  );
}

export function disableMemory(
  workspaceId: string,
  memoryId: string,
  revision: number,
): Promise<MemoryDetail> {
  return authenticatedData((accessToken) =>
    apiClient.POST("/api/v1/workspaces/{workspace_id}/memories/{memory_id}/disable", {
      headers: authorization(accessToken),
      params: {
        header: memoryRevisionHeader(revision),
        path: { memory_id: memoryId, workspace_id: workspaceId },
      },
    }),
  );
}

export function enableMemory(
  workspaceId: string,
  memoryId: string,
  revision: number,
): Promise<MemoryDetail> {
  return authenticatedData((accessToken) =>
    apiClient.POST("/api/v1/workspaces/{workspace_id}/memories/{memory_id}/enable", {
      headers: authorization(accessToken),
      params: {
        header: memoryRevisionHeader(revision),
        path: { memory_id: memoryId, workspace_id: workspaceId },
      },
    }),
  );
}

export function deleteMemory(
  workspaceId: string,
  memoryId: string,
  revision: number,
): Promise<void> {
  return authenticatedCommand((accessToken) =>
    apiClient.DELETE("/api/v1/workspaces/{workspace_id}/memories/{memory_id}", {
      headers: authorization(accessToken),
      params: {
        header: memoryRevisionHeader(revision),
        path: { memory_id: memoryId, workspace_id: workspaceId },
      },
    }),
  );
}

export function recordMemoryFeedback(
  workspaceId: string,
  memoryId: string,
  revision: number,
  request: RecordMemoryFeedbackRequest,
): Promise<MemoryFeedback> {
  return authenticatedData((accessToken) =>
    apiClient.POST("/api/v1/workspaces/{workspace_id}/memories/{memory_id}/feedback", {
      body: request,
      headers: authorization(accessToken),
      params: {
        header: memoryRevisionHeader(revision),
        path: { memory_id: memoryId, workspace_id: workspaceId },
      },
    }),
  );
}

export function getFile(workspaceId: string, fileId: string): Promise<FileSnapshot> {
  return authenticatedData((accessToken) =>
    apiClient.GET("/api/v1/workspaces/{workspace_id}/files/{file_id}", {
      headers: authorization(accessToken),
      params: { path: { file_id: fileId, workspace_id: workspaceId } },
    }),
  );
}

function createFileUpload(
  workspaceId: string,
  file: File,
  declaredMediaType: AttachmentMediaType,
  expectedSha256: string,
): Promise<FileUpload> {
  return authenticatedData((accessToken) =>
    apiClient.POST("/api/v1/workspaces/{workspace_id}/files/presign", {
      body: {
        declared_media_type: declaredMediaType,
        expected_sha256: expectedSha256,
        expected_size: file.size,
        original_name: file.name,
      },
      headers: authorization(accessToken),
      params: { path: { workspace_id: workspaceId } },
    }),
  );
}

function completeFileUpload(workspaceId: string, fileId: string): Promise<FileSnapshot> {
  return authenticatedData((accessToken) =>
    apiClient.POST("/api/v1/workspaces/{workspace_id}/files/{file_id}/complete", {
      headers: authorization(accessToken),
      params: { path: { file_id: fileId, workspace_id: workspaceId } },
    }),
  );
}

async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

export function attachmentMediaTypeForFile(file: File): AttachmentMediaType | null {
  const dot = file.name.lastIndexOf(".");
  if (dot < 0) {
    return null;
  }
  return attachmentMediaTypeByExtension[file.name.slice(dot).toLowerCase()] ?? null;
}

function requireUploadBounds(file: File, mediaType: AttachmentMediaType): void {
  const maximum = mediaType.startsWith("text/") ? MAX_TEXT_ATTACHMENT_BYTES : MAX_ATTACHMENT_BYTES;
  if (file.size < 1) {
    throw new ApiProblem(422, {
      code: "EMPTY_FILE",
      detail: "Empty attachments cannot be uploaded.",
    });
  }
  if (file.size > maximum) {
    throw new ApiProblem(422, {
      code: "FILE_TOO_LARGE",
      detail: "The attachment exceeds the allowed size.",
    });
  }
}

async function discardFailedUpload(workspaceId: string, fileId: string): Promise<void> {
  try {
    await deleteFile(workspaceId, fileId);
  } catch {
    // The original upload error remains the actionable result. Server-side expiry
    // and reconciliation retain ownership of any staging object not deleted here.
  }
}

export async function uploadFile(
  workspaceId: string,
  file: File,
  declaredMediaType: AttachmentMediaType | null = attachmentMediaTypeForFile(file),
): Promise<FileSnapshot> {
  if (declaredMediaType === null) {
    throw new ApiProblem(422, {
      code: "UNSUPPORTED_MEDIA_TYPE",
      detail: "This attachment type is not supported.",
    });
  }
  requireUploadBounds(file, declaredMediaType);
  const ticket = await createFileUpload(workspaceId, file, declaredMediaType, await sha256(file));
  const form = new FormData();
  for (const [name, value] of Object.entries(ticket.fields)) {
    form.append(name, value);
  }
  form.append("file", file, file.name);

  try {
    const uploaded = await fetch(ticket.url, {
      body: form,
      credentials: "omit",
      method: ticket.method,
    });
    if (!uploaded.ok) {
      throw new ApiProblem(uploaded.status, {
        code: "FILE_UPLOAD_TRANSPORT_FAILED",
        detail: "The attachment could not be transferred to private storage.",
      });
    }
  } catch (error: unknown) {
    await discardFailedUpload(workspaceId, ticket.file.id);
    if (error instanceof ApiProblem) {
      throw error;
    }
    throw new ApiProblem(503, {
      code: "FILE_UPLOAD_TRANSPORT_FAILED",
      detail: "The attachment could not be transferred to private storage.",
    });
  }
  return completeFileUpload(workspaceId, ticket.file.id);
}

export function deleteFile(workspaceId: string, fileId: string): Promise<FileSnapshot> {
  return authenticatedData((accessToken) =>
    apiClient.DELETE("/api/v1/workspaces/{workspace_id}/files/{file_id}", {
      headers: authorization(accessToken),
      params: { path: { file_id: fileId, workspace_id: workspaceId } },
    }),
  );
}

export function getDownloadUrl(workspaceId: string, fileId: string): Promise<FileDownload> {
  return authenticatedData((accessToken) =>
    apiClient.POST("/api/v1/workspaces/{workspace_id}/files/{file_id}/download-url", {
      headers: authorization(accessToken),
      params: { path: { file_id: fileId, workspace_id: workspaceId } },
    }),
  );
}

export function cancelRun(workspaceId: string, runId: string): Promise<void> {
  return authenticatedCommand((accessToken) =>
    apiClient.POST("/api/v1/workspaces/{workspace_id}/agent-runs/{run_id}/cancel", {
      headers: authorization(accessToken),
      params: { path: { run_id: runId, workspace_id: workspaceId } },
    }),
  );
}

function traceRecord(value: unknown, fieldName: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new AgentTraceContractError(`${fieldName} must be an object.`);
  }
  return value as Record<string, unknown>;
}

function traceArray(value: unknown, fieldName: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new AgentTraceContractError(`${fieldName} must be an array.`);
  }
  return value;
}

function traceString(value: Record<string, unknown>, fieldName: string): string {
  const field = value[fieldName];
  if (typeof field !== "string" || field.length === 0) {
    throw new AgentTraceContractError(`${fieldName} is invalid.`);
  }
  return field;
}

function traceUuid(value: Record<string, unknown>, fieldName: string): string {
  const field = traceString(value, fieldName);
  if (!UUID_PATTERN.test(field)) {
    throw new AgentTraceContractError(`${fieldName} is not a UUID.`);
  }
  return field;
}

function traceInteger(value: Record<string, unknown>, fieldName: string, minimum = 0): number {
  const field = value[fieldName];
  if (typeof field !== "number" || !Number.isSafeInteger(field) || field < minimum) {
    throw new AgentTraceContractError(`${fieldName} is invalid.`);
  }
  return field;
}

function traceSchemaVersion(value: Record<string, unknown>): 1 {
  if (traceInteger(value, "schema_version", 1) !== 1) {
    throw new AgentTraceContractError("schema_version is unsupported.");
  }
  return 1;
}

function traceBoolean(value: Record<string, unknown>, fieldName: string): boolean {
  const field = value[fieldName];
  if (typeof field !== "boolean") {
    throw new AgentTraceContractError(`${fieldName} is invalid.`);
  }
  return field;
}

function traceNullableString(value: Record<string, unknown>, fieldName: string): string | null {
  return value[fieldName] === null ? null : traceString(value, fieldName);
}

function traceNullableRecord(
  value: Record<string, unknown>,
  fieldName: string,
): Record<string, unknown> | null {
  return value[fieldName] === null ? null : traceRecord(value[fieldName], `Trace ${fieldName}`);
}

function traceTimestamp(value: Record<string, unknown>, fieldName: string): string {
  const field = traceString(value, fieldName);
  if (Number.isNaN(Date.parse(field))) {
    throw new AgentTraceContractError(`${fieldName} is not a timestamp.`);
  }
  return field;
}

function traceNullableTimestamp(value: Record<string, unknown>, fieldName: string): string | null {
  return value[fieldName] === null ? null : traceTimestamp(value, fieldName);
}

function traceNullableUuid(value: Record<string, unknown>, fieldName: string): string | null {
  return value[fieldName] === null ? null : traceUuid(value, fieldName);
}

function traceNullableNumber(
  value: Record<string, unknown>,
  fieldName: string,
  minimum: number,
  maximum: number,
): number | null {
  const field = value[fieldName];
  if (field === null) return null;
  if (typeof field !== "number" || !Number.isFinite(field) || field < minimum || field > maximum) {
    throw new AgentTraceContractError(`${fieldName} is invalid.`);
  }
  return field;
}

function traceEnum<const Values extends readonly string[]>(
  value: Record<string, unknown>,
  fieldName: string,
  values: Values,
): Values[number] {
  const field = traceString(value, fieldName);
  if (!values.includes(field)) {
    throw new AgentTraceContractError(`${fieldName} has an unsupported value.`);
  }
  return field;
}

function traceNullableEnum<const Values extends readonly string[]>(
  value: Record<string, unknown>,
  fieldName: string,
  values: Values,
): Values[number] | null {
  return value[fieldName] === null ? null : traceEnum(value, fieldName, values);
}

function parseTraceUsage(value: unknown): TraceUsage {
  const usage = traceRecord(value, "Trace usage");
  const parsed: TraceUsage = {
    cached_input_tokens: traceInteger(usage, "cached_input_tokens"),
    cost_micro_usd: traceInteger(usage, "cost_micro_usd"),
    input_tokens: traceInteger(usage, "input_tokens"),
    output_tokens: traceInteger(usage, "output_tokens"),
  };
  if (parsed.cached_input_tokens > parsed.input_tokens) {
    throw new AgentTraceContractError("Cached input usage exceeds total input usage.");
  }
  return parsed;
}

const runTypes = ["direct_answer", "tool_loop", "research"] as const;
const runStatuses = ["queued", "running", "paused", "completed", "failed", "cancelled"] as const;
const stopReasons = [
  "final",
  "cancelled",
  "provider_timeout",
  "provider_rate_limited",
  "provider_error",
  "invalid_provider_response",
  "incomplete_provider_response",
  "max_steps",
  "deadline_exceeded",
  "token_budget_exceeded",
  "cost_budget_exceeded",
  "tool_denied",
  "tool_error",
  "no_progress",
  "approval_required",
  "runtime_error",
] as const;
const stepKinds = ["model", "tool", "approval", "checkpoint", "final"] as const;
const stepStatuses = ["running", "completed", "failed", "cancelled"] as const;
const sourceKinds = [
  "system_instructions",
  "runtime_context_projection",
  "financial_scope",
  "conversation_summary",
  "attachment",
  "user_question",
  "tool_observation",
  "short_term_memory",
  "long_term_memory",
] as const;
const decisionReasons = [
  "included",
  "not_available",
  "excluded_token_budget",
  "excluded_not_relevant",
  "excluded_stale",
  "excluded_conflicted",
  "excluded_duplicate",
  "excluded_sensitive",
  "excluded_disabled",
  "excluded_expired",
  "excluded_deleted",
  "excluded_negative_feedback",
  "excluded_financial_scope_mismatch",
  "excluded_future_source",
  "excluded_unit_mismatch",
  "excluded_unsupported_financial_source",
] as const;
const messageRoles = ["system", "user", "assistant"] as const;
const memoryScopes = ["user", "workspace"] as const;
const traceEventTypes = [
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
  "agent.tool.requested",
  "agent.tool.approval_required",
  "agent.tool.denied",
  "agent.tool.started",
  "agent.tool.completed",
  "agent.tool.failed",
  "agent.tool.cancelled",
  "agent.artifact.created",
  "agent.checkpoint.saved",
  "agent.approval.requested",
  "agent.approval.decided",
  "agent.research.node_started",
  "agent.research.node_completed",
  "agent.research.node_failed",
  "agent.research.verification_completed",
] as const;

function parseTraceRun(value: unknown): AgentTraceRun {
  const run = traceRecord(value, "Trace Run");
  return {
    conversation_id: traceUuid(run, "conversation_id"),
    created_at: traceTimestamp(run, "created_at"),
    deadline: traceTimestamp(run, "deadline"),
    event_count: traceInteger(run, "event_count"),
    event_stream_id: traceUuid(run, "event_stream_id"),
    harness_version: traceString(run, "harness_version"),
    max_cost_micro_usd: traceInteger(run, "max_cost_micro_usd", 1),
    max_steps: traceInteger(run, "max_steps", 1),
    max_total_tokens: traceInteger(run, "max_total_tokens", 1),
    run_id: traceUuid(run, "run_id"),
    run_type: traceEnum(run, "run_type", runTypes),
    runtime_version: traceString(run, "runtime_version"),
    schema_version: traceSchemaVersion(run),
    started_at: traceNullableTimestamp(run, "started_at"),
    state_revision: traceInteger(run, "state_revision"),
    status: traceEnum(run, "status", runStatuses),
    step_count: traceInteger(run, "step_count"),
    stop_reason: traceNullableEnum(run, "stop_reason", stopReasons),
    terminal_at: traceNullableTimestamp(run, "terminal_at"),
    trace_id: traceString(run, "trace_id"),
    turn_id: traceUuid(run, "turn_id"),
    usage: parseTraceUsage(run.usage),
    workspace_id: traceUuid(run, "workspace_id"),
  };
}

function parseTraceStep(value: unknown): AgentTraceStep {
  const step = traceRecord(value, "Trace Step");
  return {
    completed_at: traceNullableTimestamp(step, "completed_at"),
    error_code: traceNullableString(step, "error_code"),
    kind: traceEnum(step, "kind", stepKinds),
    last_event_sequence: traceInteger(step, "last_event_sequence", 1),
    sequence: traceInteger(step, "sequence", 1),
    started_at: traceTimestamp(step, "started_at"),
    status: traceEnum(step, "status", stepStatuses),
    step_id: traceUuid(step, "step_id"),
    usage: parseTraceUsage(step.usage),
  };
}

function parseContextSource(value: unknown): AgentTraceContextSource {
  const source = traceRecord(value, "Trace Context source");
  const parsed: AgentTraceContextSource = {
    decision_reason: traceEnum(source, "decision_reason", decisionReasons),
    estimated_token_count: traceInteger(source, "estimated_token_count"),
    included: traceBoolean(source, "included"),
    feedback_score:
      source.feedback_score === null ? null : traceInteger(source, "feedback_score", -1),
    message_role: traceNullableEnum(source, "message_role", messageRoles),
    ordinal: traceInteger(source, "ordinal", 1),
    relevance_score: traceNullableNumber(source, "relevance_score", 0, 1),
    source_id: traceString(source, "source_id"),
    source_identity: traceNullableRecord(source, "source_identity"),
    source_kind: traceEnum(source, "source_kind", sourceKinds),
    source_revision_id: traceNullableUuid(source, "source_revision_id"),
    source_scope: traceNullableEnum(source, "source_scope", memoryScopes),
    source_sha256: traceNullableString(source, "source_sha256"),
    source_version: traceString(source, "source_version"),
  };
  const hasCompleteMemoryRankingMetadata =
    parsed.source_revision_id !== null &&
    parsed.source_scope !== null &&
    parsed.relevance_score !== null &&
    parsed.feedback_score !== null;
  if (
    (parsed.included &&
      (parsed.decision_reason !== "included" ||
        parsed.estimated_token_count < 1 ||
        parsed.message_role === null)) ||
    (!parsed.included &&
      (parsed.decision_reason === "included" ||
        parsed.estimated_token_count !== 0 ||
        parsed.message_role !== null)) ||
    ((parsed.source_kind === "attachment" || parsed.source_kind === "tool_observation") &&
      parsed.source_sha256 === null) ||
    (parsed.source_kind === "financial_scope" && parsed.source_identity === null) ||
    (parsed.source_identity !== null &&
      parsed.source_kind !== "financial_scope" &&
      parsed.source_kind !== "tool_observation") ||
    (parsed.source_kind === "long_term_memory") !== hasCompleteMemoryRankingMetadata ||
    (parsed.feedback_score !== null && parsed.feedback_score > 1)
  ) {
    throw new AgentTraceContractError("A Trace Context source decision is inconsistent.");
  }
  return parsed;
}

function parseContextManifest(value: unknown): AgentTraceContextManifest {
  const manifest = traceRecord(value, "Trace Context manifest");
  const budget = traceRecord(manifest.budget, "Trace Context budget");
  return {
    budget: {
      allowed_output_tokens: traceInteger(budget, "allowed_output_tokens", 1),
      estimated_input_tokens: traceInteger(budget, "estimated_input_tokens", 1),
      max_input_tokens: traceInteger(budget, "max_input_tokens", 1),
      run_max_total_tokens: traceInteger(budget, "run_max_total_tokens", 1),
      tokens_used_before_step: traceInteger(budget, "tokens_used_before_step"),
      unreserved_run_tokens: traceInteger(budget, "unreserved_run_tokens"),
    },
    compiler_version: traceString(manifest, "compiler_version"),
    created_at: traceTimestamp(manifest, "created_at"),
    manifest_id: traceUuid(manifest, "manifest_id"),
    prompt_version: traceString(manifest, "prompt_version"),
    run_id: traceUuid(manifest, "run_id"),
    runtime_projection_version: traceString(manifest, "runtime_projection_version"),
    schema_version: traceSchemaVersion(manifest),
    sources: traceArray(manifest.sources, "Trace Context sources").map((source) =>
      parseContextSource(source),
    ),
    step_id: traceUuid(manifest, "step_id"),
    token_counter_version: traceString(manifest, "token_counter_version"),
    workspace_id: traceUuid(manifest, "workspace_id"),
  };
}

function parseTraceEvent(value: unknown): AgentTraceEvent {
  const event = traceRecord(value, "Trace Event");
  const details = traceRecord(event.details, "Trace Event details");
  const safeDetails: Record<string, string | number> = {};
  for (const [name, detail] of Object.entries(details)) {
    if (
      (typeof detail !== "string" && typeof detail !== "number") ||
      (typeof detail === "number" && (!Number.isSafeInteger(detail) || detail < 0))
    ) {
      throw new AgentTraceContractError("Trace Event details contain an unsafe value.");
    }
    safeDetails[name] = detail;
  }
  return {
    details: safeDetails,
    event_type: traceEnum(event, "event_type", traceEventTypes),
    occurred_at: traceTimestamp(event, "occurred_at"),
    schema_version: traceSchemaVersion(event),
    sequence: traceInteger(event, "sequence", 1),
  };
}

function parseAgentTrace(value: unknown): AgentTrace {
  const document = traceRecord(value, "Agent Trace");
  const run = parseTraceRun(document.run);
  const steps = traceArray(document.steps, "Trace Steps").map((step) => parseTraceStep(step));
  const manifests = traceArray(document.context_manifests, "Trace Context manifests").map(
    (manifest) => parseContextManifest(manifest),
  );
  const events = traceArray(document.events, "Trace Events").map((event) => parseTraceEvent(event));
  if (
    steps.length !== run.step_count ||
    steps.some((step, index) => step.sequence !== index + 1) ||
    events.length !== run.event_count ||
    events.some((event, index) => event.sequence !== index + 1)
  ) {
    throw new AgentTraceContractError("Agent Trace sequences do not match the Run summary.");
  }
  const stepIds = new Set(steps.map((step) => step.step_id));
  if (
    manifests.some(
      (manifest) =>
        manifest.run_id !== run.run_id ||
        manifest.workspace_id !== run.workspace_id ||
        !stepIds.has(manifest.step_id),
    )
  ) {
    throw new AgentTraceContractError("A Context manifest does not belong to this Agent Run.");
  }
  return {
    context_manifests: manifests,
    events,
    run,
    schema_version: traceSchemaVersion(document),
    steps,
  };
}

export async function getAgentTrace(
  workspaceId: string,
  runId: string,
  signal?: AbortSignal,
): Promise<AgentTrace> {
  const value = await authenticatedData((accessToken) =>
    apiClient.GET("/api/v1/workspaces/{workspace_id}/agent-runs/{run_id}/trace", {
      headers: authorization(accessToken),
      params: { path: { run_id: runId, workspace_id: workspaceId } },
      ...(signal === undefined ? {} : { signal }),
    }),
  );
  const trace = parseAgentTrace(value);
  if (trace.run.run_id !== runId || trace.run.workspace_id !== workspaceId) {
    throw new AgentTraceContractError("The Agent Trace does not match the requested Run.");
  }
  return trace;
}
