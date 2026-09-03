import { afterEach, describe, expect, it, vi } from "vitest";

const accessToken = "chat-access-token";
const workspaceId = "22222222-2222-4222-8222-222222222222";
const conversationId = "33333333-3333-4333-8333-333333333333";
const turnId = "44444444-4444-4444-8444-444444444444";
const runId = "55555555-5555-4555-8555-555555555555";
const stepId = "66666666-6666-4666-8666-666666666666";
const fileId = "77777777-7777-4777-8777-777777777777";
const candidateId = "88888888-1111-4111-8111-888888888888";
const memoryId = "99999999-1111-4111-8111-999999999999";

afterEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    headers: { "content-type": "application/json" },
    status,
  });
}

function asRequest(input: RequestInfo | URL, init?: RequestInit): Request {
  return input instanceof Request ? input : new Request(input, init);
}

async function loadChatApi(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>) {
  vi.stubGlobal("fetch", fetchMock);
  const session = await import("../auth/session");
  session.setAccessSession({
    expiresAt: "2026-08-14T07:00:00Z",
    token: accessToken,
  });
  return import("./chat-api");
}

const summary = {
  created_at: "2026-08-14T06:00:00Z",
  id: conversationId,
  title: "行业趋势",
  updated_at: "2026-08-14T06:01:00Z",
};

const fileSnapshot = {
  actual_size: 5,
  declared_media_type: "text/plain",
  detected_media_type: "text/plain",
  error_code: null,
  expected_size: 5,
  height: null,
  id: fileId,
  kind: "text",
  original_name: "brief.txt",
  ready_at: "2026-08-14T06:02:00Z",
  status: "ready",
  upload_expires_at: "2026-08-14T06:10:00Z",
  width: null,
};

describe("chat REST API", () => {
  it("uses the generated contract for conversation operations and preserves Turn idempotency", async () => {
    const captured: Request[] = [];
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const request = asRequest(input, init);
      captured.push(request.clone());
      const url = new URL(request.url);
      const conversationPath = `/api/v1/workspaces/${workspaceId}/conversations`;

      if (request.method === "GET" && url.pathname === conversationPath) {
        return Promise.resolve(jsonResponse({ conversations: [summary], next_cursor: null }));
      }
      if (request.method === "GET" && url.pathname === `${conversationPath}/${conversationId}`) {
        return Promise.resolve(jsonResponse({ ...summary, turn_count: 1 }));
      }
      if (
        request.method === "GET" &&
        url.pathname === `${conversationPath}/${conversationId}/messages`
      ) {
        return Promise.resolve(
          jsonResponse({
            messages: [
              {
                agent_run_id: runId,
                attachments: [],
                content_markdown: "请总结",
                created_at: "2026-08-14T06:00:30Z",
                id: "88888888-8888-4888-8888-888888888888",
                role: "user",
                status: "committed",
                turn_id: turnId,
              },
            ],
            next_cursor: null,
          }),
        );
      }
      if (request.method === "POST" && url.pathname === conversationPath) {
        return Promise.resolve(
          jsonResponse(
            {
              agent_run_id: runId,
              conversation_id: conversationId,
              created: true,
              job_id: "99999999-9999-4999-8999-999999999999",
              turn_id: turnId,
              user_message_id: "88888888-8888-4888-8888-888888888888",
            },
            202,
          ),
        );
      }
      if (request.method === "PATCH" && url.pathname === `${conversationPath}/${conversationId}`) {
        return Promise.resolve(jsonResponse({ ...summary, title: "新标题" }));
      }
      if (request.method === "DELETE" && url.pathname === `${conversationPath}/${conversationId}`) {
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      if (
        request.method === "POST" &&
        url.pathname === `/api/v1/workspaces/${workspaceId}/agent-runs/${runId}/cancel`
      ) {
        return Promise.resolve(new Response(null, { status: 202 }));
      }
      return Promise.resolve(new Response(null, { status: 404 }));
    });
    const api = await loadChatApi(fetchMock);

    await expect(api.listConversations(workspaceId, { limit: 25 })).resolves.toMatchObject({
      conversations: [{ id: conversationId }],
    });
    await expect(api.getConversation(workspaceId, conversationId)).resolves.toMatchObject({
      turn_count: 1,
    });
    await expect(api.listMessages(workspaceId, conversationId)).resolves.toMatchObject({
      messages: [{ content_markdown: "请总结" }],
    });
    await expect(
      api.startTurn(
        workspaceId,
        { attachment_ids: [], mode: "none", question: "请总结" },
        "turn-submit-1",
      ),
    ).resolves.toMatchObject({ agent_run_id: runId });
    await expect(
      api.renameConversation(workspaceId, conversationId, "新标题"),
    ).resolves.toMatchObject({ title: "新标题" });
    await expect(api.deleteConversation(workspaceId, conversationId)).resolves.toBeUndefined();
    await expect(api.cancelRun(workspaceId, runId)).resolves.toBeUndefined();

    for (const request of captured) {
      expect(request.headers.get("authorization")).toBe(`Bearer ${accessToken}`);
    }
    const turnRequest = captured.find(
      (request) =>
        request.method === "POST" && new URL(request.url).pathname.endsWith("/conversations"),
    );
    expect(turnRequest?.headers.get("idempotency-key")).toBe("turn-submit-1");
    await expect(turnRequest?.json()).resolves.toMatchObject({ mode: "none", question: "请总结" });
    const listRequest = captured[0];
    expect(
      listRequest === undefined ? null : new URL(listRequest.url).searchParams.get("limit"),
    ).toBe("25");
  });

  it("uses generated Memory contracts with idempotency and revision preconditions", async () => {
    const captured: Request[] = [];
    const candidate = {
      confidence: 0.95,
      conversation_id: conversationId,
      created_at: "2026-08-20T08:00:00Z",
      id: candidateId,
      policy_decision: "allowed",
      policy_reason: "user_authored",
      resolved_memory_id: null,
      revision: 1,
      source_message_ids: [fileId],
      status: "candidate",
      suggested_content: "默认使用中文回答。",
      suggested_expires_at: null,
      suggested_scope: "user",
      updated_at: "2026-08-20T08:00:00Z",
      write_reason: "user_selected_conversation_messages",
    };
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const request = asRequest(input, init);
      captured.push(request.clone());
      const path = new URL(request.url).pathname;
      const root = `/api/v1/workspaces/${workspaceId}/memories`;
      if (request.method === "POST" && path === `${root}/candidates`) {
        return Promise.resolve(jsonResponse({ ...candidate, created: true }, 201));
      }
      if (request.method === "GET" && path === `${root}/candidates`) {
        return Promise.resolve(jsonResponse({ candidates: [candidate] }));
      }
      if (request.method === "POST" && path.endsWith(`/${candidateId}/confirm`)) {
        return Promise.resolve(
          jsonResponse({
            action: "create",
            created: true,
            memory: {
              current_revision: {
                content: "默认使用中文回答。",
                created_at: "2026-08-20T08:01:00Z",
                editor_user_id: fileId,
                expires_at: null,
                id: fileId,
                kind: "preference",
                policy_decision: "allowed",
                scope: "user",
                source_message_ids: [fileId],
                validity: "valid",
                version: 1,
                write_action: "create",
                write_reason: "user_selected_conversation_messages",
              },
              memory: {
                confidence: 0.95,
                created_at: "2026-08-20T08:01:00Z",
                current_revision_id: fileId,
                current_version: 1,
                expires_at: null,
                id: memoryId,
                kind: "preference",
                owner_user_id: fileId,
                revision: 1,
                scope: "user",
                source_conversation_id: conversationId,
                status: "confirmed",
                updated_at: "2026-08-20T08:01:00Z",
              },
              revisions: [],
            },
          }),
        );
      }
      if (request.method === "POST" && path.endsWith(`/${candidateId}/reject`)) {
        return Promise.resolve(jsonResponse({ ...candidate, revision: 2, status: "rejected" }));
      }
      return Promise.resolve(new Response(null, { status: 404 }));
    });
    const api = await loadChatApi(fetchMock);

    await expect(
      api.createMemoryCandidate(
        workspaceId,
        {
          conversation_id: conversationId,
          message_ids: [fileId],
          scope: "user",
        },
        "memory-create-1",
      ),
    ).resolves.toMatchObject({ id: candidateId });
    await expect(
      api.listMemoryCandidates(workspaceId, { conversationId, limit: 10 }),
    ).resolves.toMatchObject([{ id: candidateId }]);
    await expect(
      api.confirmMemoryCandidate(workspaceId, candidateId, 1, {
        action: "create",
        content: "默认使用中文回答。",
        expires_at: null,
        kind: "preference",
        scope: "user",
        target_memory_id: null,
        target_revision: null,
      }),
    ).resolves.toMatchObject({ memory: { memory: { id: memoryId } } });
    await expect(api.rejectMemoryCandidate(workspaceId, candidateId, 1)).resolves.toMatchObject({
      status: "rejected",
    });

    const createRequest = captured.find(
      (request) =>
        request.method === "POST" && new URL(request.url).pathname.endsWith("/candidates"),
    );
    expect(createRequest?.headers.get("idempotency-key")).toBe("memory-create-1");
    const confirmRequest = captured.find((request) =>
      new URL(request.url).pathname.endsWith(`/${candidateId}/confirm`),
    );
    const rejectRequest = captured.find((request) =>
      new URL(request.url).pathname.endsWith(`/${candidateId}/reject`),
    );
    expect(confirmRequest?.headers.get("if-match")).toBe('"1"');
    expect(rejectRequest?.headers.get("if-match")).toBe('"1"');
    const listRequest = captured.find(
      (request) =>
        request.method === "GET" && new URL(request.url).pathname.endsWith("/candidates"),
    );
    expect(
      listRequest === undefined
        ? null
        : new URL(listRequest.url).searchParams.get("conversation_id"),
    ).toBe(conversationId);
  });

  it("uses resource revisions for Memory search, governance, deletion, and feedback", async () => {
    const captured: Request[] = [];
    const snapshot = {
      confidence: 0.95,
      created_at: "2026-08-20T08:01:00Z",
      current_revision_id: fileId,
      current_version: 2,
      expires_at: null,
      id: memoryId,
      kind: "preference",
      owner_user_id: fileId,
      revision: 4,
      scope: "user",
      source_conversation_id: conversationId,
      status: "confirmed",
      updated_at: "2026-08-20T08:02:00Z",
    } as const;
    const revision = {
      content: "钢铁报告默认使用中文回答。",
      created_at: "2026-08-20T08:02:00Z",
      editor_user_id: fileId,
      expires_at: null,
      id: fileId,
      kind: "preference",
      policy_decision: "allowed",
      scope: "user",
      source_message_ids: [fileId],
      validity: "valid",
      version: 2,
      write_action: "update",
      write_reason: "user_governance_update",
    } as const;
    const detail = { current_revision: revision, memory: snapshot, revisions: [revision] };
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const request = asRequest(input, init);
      captured.push(request.clone());
      const path = new URL(request.url).pathname;
      const root = `/api/v1/workspaces/${workspaceId}/memories`;
      if (request.method === "GET" && path === root) {
        return Promise.resolve(jsonResponse({ memories: [snapshot] }));
      }
      if (request.method === "DELETE" && path === `${root}/${memoryId}`) {
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      if (request.method === "POST" && path.endsWith("/feedback")) {
        return Promise.resolve(
          jsonResponse({
            actor_user_id: fileId,
            created_at: snapshot.updated_at,
            id: candidateId,
            memory_id: memoryId,
            memory_revision_id: fileId,
            reason: null,
            updated_at: snapshot.updated_at,
            value: "helpful",
          }),
        );
      }
      return Promise.resolve(jsonResponse(detail));
    });
    const api = await loadChatApi(fetchMock);

    await expect(
      api.listMemories(workspaceId, {
        kind: "preference",
        query: "钢铁",
        scope: "user",
        status: "confirmed",
      }),
    ).resolves.toMatchObject([{ id: memoryId, revision: 4 }]);
    await expect(api.getMemory(workspaceId, memoryId)).resolves.toMatchObject({
      memory: { id: memoryId },
    });
    await expect(
      api.updateMemory(workspaceId, memoryId, 4, {
        content: "钢铁报告默认使用中文回答。",
        expires_at: null,
        kind: "preference",
        scope: "user",
      }),
    ).resolves.toMatchObject({ memory: { revision: 4 } });
    await expect(api.disableMemory(workspaceId, memoryId, 4)).resolves.toBeDefined();
    await expect(api.enableMemory(workspaceId, memoryId, 4)).resolves.toBeDefined();
    await expect(
      api.recordMemoryFeedback(workspaceId, memoryId, 4, {
        memory_revision_id: fileId,
        reason: null,
        value: "helpful",
      }),
    ).resolves.toMatchObject({ value: "helpful" });
    await expect(api.deleteMemory(workspaceId, memoryId, 4)).resolves.toBeUndefined();

    const listRequest = captured[0];
    const search = new URL(listRequest?.url ?? "https://invalid.example").searchParams;
    expect(search.get("query")).toBe("钢铁");
    expect(search.get("status")).toBe("confirmed");
    for (const request of captured.filter((item) => item.method !== "GET")) {
      expect(request.headers.get("if-match")).toBe('"4"');
    }
  });

  it("hashes once, posts the presigned multipart without credentials, then completes parsing", async () => {
    const captured: Request[] = [];
    const storageCapture: {
      authorization: string | null;
      form: FormData | null;
    } = { authorization: "not-observed", form: null };
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const rawUrl =
        typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const url = new URL(rawUrl);
      if (url.origin === "https://storage.example") {
        storageCapture.authorization = new Headers(init?.headers).get("authorization");
        storageCapture.form = init?.body instanceof FormData ? init.body : null;
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      const request = asRequest(input, init);
      captured.push(request.clone());
      if (url.pathname.endsWith("/files/presign")) {
        return Promise.resolve(
          jsonResponse(
            {
              expires_at: "2026-08-14T06:10:00Z",
              fields: { key: "staging/object", policy: "signed-policy" },
              file: {
                ...fileSnapshot,
                actual_size: null,
                detected_media_type: null,
                kind: null,
                ready_at: null,
                status: "staging",
              },
              method: "POST",
              url: "https://storage.example/upload",
            },
            201,
          ),
        );
      }
      if (url.pathname.endsWith(`/files/${fileId}/complete`)) {
        return Promise.resolve(jsonResponse(fileSnapshot));
      }
      return Promise.resolve(new Response(null, { status: 404 }));
    });
    const api = await loadChatApi(fetchMock);
    const file = new File(["hello"], "brief.txt", { type: "text/plain" });

    await expect(api.uploadFile(workspaceId, file)).resolves.toMatchObject({
      id: fileId,
      status: "ready",
    });

    const presignRequest = captured.find((request) => request.url.endsWith("/files/presign"));
    await expect(presignRequest?.json()).resolves.toMatchObject({
      declared_media_type: "text/plain",
      expected_sha256: "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
      expected_size: 5,
      original_name: "brief.txt",
    });
    expect(storageCapture.authorization).toBeNull();
    expect(storageCapture.form?.get("policy")).toBe("signed-policy");
    const uploadedFile = storageCapture.form?.get("file");
    expect(uploadedFile).toBeInstanceOf(File);
    expect(uploadedFile instanceof File ? uploadedFile.name : null).toBe("brief.txt");
  });

  it("returns only a validated safe Trace projection", async () => {
    const trace = {
      context_manifests: [
        {
          budget: {
            allowed_output_tokens: 1000,
            estimated_input_tokens: 20,
            max_input_tokens: 2000,
            run_max_total_tokens: 3000,
            tokens_used_before_step: 0,
            unreserved_run_tokens: 2000,
          },
          compiler_version: "context-v0",
          created_at: "2026-08-14T06:00:01Z",
          manifest_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          prompt_version: "direct-answer-v0",
          run_id: runId,
          runtime_projection_version: "runtime-context-projection-v0",
          schema_version: 1,
          sources: [
            {
              decision_reason: "included",
              estimated_token_count: 5,
              feedback_score: null,
              included: true,
              message_role: "user",
              ordinal: 1,
              relevance_score: null,
              source_id: "question",
              source_identity: null,
              source_kind: "user_question",
              source_revision_id: null,
              source_scope: null,
              source_sha256: null,
              source_version: "v1",
            },
            {
              decision_reason: "not_available",
              estimated_token_count: 0,
              feedback_score: null,
              included: false,
              message_role: null,
              ordinal: 2,
              relevance_score: null,
              source_id: "conversation-summary",
              source_identity: null,
              source_kind: "conversation_summary",
              source_revision_id: null,
              source_scope: null,
              source_sha256: null,
              source_version: "v1",
            },
            {
              decision_reason: "included",
              estimated_token_count: 8,
              feedback_score: null,
              included: true,
              message_role: "user",
              ordinal: 3,
              relevance_score: null,
              source_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
              source_identity: { sources: ["sec://filing-chunks/example"] },
              source_kind: "tool_observation",
              source_revision_id: null,
              source_scope: null,
              source_sha256: "a".repeat(64),
              source_version: "tool-observation-v1",
            },
          ],
          step_id: stepId,
          token_counter_version: "utf8-upper-bound-v0", // gitleaks:allow
          workspace_id: workspaceId,
        },
      ],
      events: [
        {
          details: { runtime_version: "runtime-v0" },
          event_type: "agent.run.queued",
          occurred_at: "2026-08-14T06:00:00Z",
          schema_version: 1,
          sequence: 1,
        },
        {
          details: {
            graph_version: "research-l3-v1",
            node: "scope_validation",
            research_state_schema_version: "research-state-v1",
            state_revision: 2,
          },
          event_type: "agent.research.node_completed",
          occurred_at: "2026-08-14T06:00:01Z",
          schema_version: 1,
          sequence: 2,
        },
        {
          details: { approval_request_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd" },
          event_type: "agent.approval.requested",
          occurred_at: "2026-08-14T06:00:02Z",
          schema_version: 1,
          sequence: 3,
        },
        {
          details: { outcome: "allow" },
          event_type: "agent.approval.decided",
          occurred_at: "2026-08-14T06:00:03Z",
          schema_version: 1,
          sequence: 4,
        },
        {
          details: { verification_status: "verified" },
          event_type: "agent.research.verification_completed",
          occurred_at: "2026-08-14T06:00:04Z",
          schema_version: 1,
          sequence: 5,
        },
      ],
      run: {
        conversation_id: conversationId,
        created_at: "2026-08-14T06:00:00Z",
        deadline: "2026-08-14T06:01:00Z",
        event_count: 5,
        event_stream_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        harness_version: "harness-v0",
        max_cost_micro_usd: 1000,
        max_steps: 2,
        max_total_tokens: 3000,
        run_id: runId,
        run_type: "research",
        runtime_version: "runtime-v0",
        schema_version: 1,
        started_at: "2026-08-14T06:00:01Z",
        state_revision: 1,
        status: "running",
        step_count: 1,
        stop_reason: null,
        terminal_at: null,
        trace_id: "0123456789abcdef0123456789abcdef",
        turn_id: turnId,
        usage: {
          cached_input_tokens: 0,
          cost_micro_usd: 0,
          input_tokens: 0,
          output_tokens: 0,
        },
        workspace_id: workspaceId,
      },
      schema_version: 1,
      steps: [
        {
          completed_at: null,
          error_code: null,
          kind: "model",
          last_event_sequence: 5,
          sequence: 1,
          started_at: "2026-08-14T06:00:01Z",
          status: "running",
          step_id: stepId,
          usage: {
            cached_input_tokens: 0,
            cost_micro_usd: 0,
            input_tokens: 0,
            output_tokens: 0,
          },
        },
      ],
    };
    const fetchMock = vi.fn<typeof fetch>(() => Promise.resolve(jsonResponse(trace)));
    const api = await loadChatApi(fetchMock);

    const result = await api.getAgentTrace(workspaceId, runId);

    expect(result.run.run_id).toBe(runId);
    expect(result.context_manifests[0]?.sources[0]).toMatchObject({
      included: true,
      source_kind: "user_question",
    });
    expect(result.context_manifests[0]?.sources[1]).toMatchObject({
      estimated_token_count: 0,
      included: false,
    });
    expect(result.context_manifests[0]?.sources[2]).toMatchObject({
      source_identity: { sources: ["sec://filing-chunks/example"] },
      source_kind: "tool_observation",
      source_sha256: "a".repeat(64),
    });
    expect(result.events[1]).toMatchObject({
      details: { node: "scope_validation", state_revision: 2 },
      event_type: "agent.research.node_completed",
    });
    const firstCall = fetchMock.mock.calls[0];
    const request = asRequest(firstCall?.[0] ?? "https://invalid.example", firstCall?.[1]);
    expect(request.headers.get("authorization")).toBe(`Bearer ${accessToken}`);
    expect(new URL(request.url).pathname).toBe(
      `/api/v1/workspaces/${workspaceId}/agent-runs/${runId}/trace`,
    );

    const observation = trace.context_manifests[0]?.sources[2];
    if (observation === undefined) throw new Error("Tool Observation fixture is missing.");
    observation.source_sha256 = null;

    await expect(api.getAgentTrace(workspaceId, runId)).rejects.toThrow(
      "A Trace Context source decision is inconsistent.",
    );
  });
});
