import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  authenticatedFetch: vi.fn<(input: string | URL, init?: RequestInit) => Promise<Response>>(),
}));

vi.mock("../api/api", () => {
  class ApiProblem extends Error {
    readonly status: number;

    constructor(status: number) {
      super("API problem");
      this.status = status;
    }
  }

  return {
    ApiProblem,
    authenticatedFetch: mocks.authenticatedFetch,
  };
});

import {
  AgentStreamContractError,
  followAgentRunEvents,
  type AgentStreamConnectionState,
  type AgentStreamEvent,
} from "./agent-stream";

const streamId = "11111111-1111-4111-8111-111111111111";
const workspaceId = "22222222-2222-4222-8222-222222222222";
const runId = "33333333-3333-4333-8333-333333333333";

afterEach(() => {
  mocks.authenticatedFetch.mockReset();
});

function frame(
  sequence: number,
  type: string,
  payload: Record<string, unknown> = {},
  schemaVersion = 1,
): string {
  return [
    `id: ${String(sequence)}`,
    `event: ${type}`,
    `data: ${JSON.stringify({
      occurred_at: "2026-08-14T06:00:00Z",
      payload,
      schema_version: schemaVersion,
      sequence,
      stream_id: streamId,
      trace_id: "0123456789abcdef0123456789abcdef",
      type,
    })}`,
    "",
    "",
  ].join("\n");
}

function responseFromBytes(chunks: readonly Uint8Array[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk);
      }
      controller.close();
    },
  });
  return new Response(body, { headers: { "content-type": "text/event-stream; charset=utf-8" } });
}

function responseFromText(value: string): Response {
  return responseFromBytes([new TextEncoder().encode(value)]);
}

function indexOfBytes(value: Uint8Array, target: Uint8Array): number {
  outer: for (let index = 0; index <= value.length - target.length; index += 1) {
    for (let offset = 0; offset < target.length; offset += 1) {
      if (value[index + offset] !== target[offset]) {
        continue outer;
      }
    }
    return index;
  }
  return -1;
}

describe("Agent fetch-SSE follower", () => {
  it("decodes split UTF-8, ignores heartbeats/unknown Events, deduplicates, and closes on terminal", async () => {
    const wire = [
      frame(1, "agent.run.queued", { run_type: "direct_answer" }),
      ": heartbeat last_sequence=1\n\n",
      frame(2, "agent.future.metadata", { version: 2 }),
      frame(2, "agent.future.metadata", { version: 2 }),
      frame(3, "agent.model.delta", { delta: "行业", model_sequence: 1 }),
      frame(4, "agent.run.completed", { stop_reason: "final" }),
    ].join("");
    const encoded = new TextEncoder().encode(wire);
    const markerIndex = indexOfBytes(encoded, new TextEncoder().encode("行业"));
    expect(markerIndex).toBeGreaterThan(0);
    mocks.authenticatedFetch.mockResolvedValueOnce(
      responseFromBytes([
        encoded.slice(0, markerIndex + 1),
        encoded.slice(markerIndex + 1, markerIndex + 4),
        encoded.slice(markerIndex + 4),
      ]),
    );
    const events: AgentStreamEvent[] = [];
    const states: AgentStreamConnectionState[] = [];

    const cursor = await followAgentRunEvents({
      onConnectionState: (state) => states.push(state),
      onEvent: (event) => {
        events.push(event);
      },
      retryDelayMs: 0,
      runId,
      workspaceId,
    });

    expect(cursor).toBe(4);
    expect(events.map((event) => event.type)).toEqual([
      "agent.run.queued",
      "agent.model.delta",
      "agent.run.completed",
    ]);
    expect(events[1]?.payload).toMatchObject({ delta: "行业" });
    expect(states).toEqual(["connecting", "open", "closed"]);
    expect(mocks.authenticatedFetch).toHaveBeenCalledOnce();
  });

  it("reconnects from the last committed Event ID after a clean non-terminal close", async () => {
    mocks.authenticatedFetch
      .mockResolvedValueOnce(responseFromText(frame(1, "agent.run.queued")))
      .mockResolvedValueOnce(responseFromText(frame(2, "agent.run.cancelled")));
    const states: AgentStreamConnectionState[] = [];

    const cursor = await followAgentRunEvents({
      onConnectionState: (state) => states.push(state),
      onEvent: vi.fn(),
      retryDelayMs: 0,
      runId,
      workspaceId,
    });

    expect(cursor).toBe(2);
    expect(mocks.authenticatedFetch).toHaveBeenCalledTimes(2);
    const secondInit = mocks.authenticatedFetch.mock.calls[1]?.[1];
    expect(new Headers(secondInit?.headers).get("Last-Event-ID")).toBe("1");
    expect(states).toEqual(["connecting", "open", "reconnecting", "open", "closed"]);
  });

  it("delivers an authoritative snapshot even when it is aligned to cursor zero", async () => {
    mocks.authenticatedFetch.mockResolvedValueOnce(
      responseFromText(
        frame(0, "stream.snapshot", { content_markdown: "已恢复" }) +
          frame(1, "agent.run.completed", { stop_reason: "final" }),
      ),
    );
    const events: AgentStreamEvent[] = [];

    await followAgentRunEvents({
      onConnectionState: vi.fn(),
      onEvent: (event) => {
        events.push(event);
      },
      retryDelayMs: 0,
      runId,
      workspaceId,
    });

    expect(events[0]).toMatchObject({ sequence: 0, type: "stream.snapshot" });
  });

  it("rejects a sequence gap instead of guessing or reconnecting past it", async () => {
    mocks.authenticatedFetch.mockResolvedValueOnce(
      responseFromText(frame(2, "agent.model.delta", { delta: "gap" })),
    );

    await expect(
      followAgentRunEvents({
        onConnectionState: vi.fn(),
        onEvent: vi.fn(),
        retryDelayMs: 0,
        runId,
        workspaceId,
      }),
    ).rejects.toBeInstanceOf(AgentStreamContractError);
    expect(mocks.authenticatedFetch).toHaveBeenCalledOnce();
  });

  it("rejects a future Event schema until the client has an explicit decoder for it", async () => {
    mocks.authenticatedFetch.mockResolvedValueOnce(
      responseFromText(frame(1, "agent.run.queued", {}, 2)),
    );

    await expect(
      followAgentRunEvents({
        onConnectionState: vi.fn(),
        onEvent: vi.fn(),
        retryDelayMs: 0,
        runId,
        workspaceId,
      }),
    ).rejects.toThrow("schema version is unsupported");
  });

  it("honors an already-aborted caller without opening a network stream", async () => {
    const controller = new AbortController();
    controller.abort();
    const states: AgentStreamConnectionState[] = [];

    await expect(
      followAgentRunEvents({
        cursor: 7,
        onConnectionState: (state) => {
          states.push(state);
        },
        onEvent: vi.fn(),
        runId,
        signal: controller.signal,
        workspaceId,
      }),
    ).resolves.toBe(7);
    expect(states).toEqual(["closed"]);
    expect(mocks.authenticatedFetch).not.toHaveBeenCalled();
  });

  it("cancels a long reconnect wait immediately after an external terminal check", async () => {
    mocks.authenticatedFetch.mockResolvedValueOnce(responseFromText(frame(1, "agent.run.queued")));
    const controller = new AbortController();
    const states: AgentStreamConnectionState[] = [];

    const cursor = await followAgentRunEvents({
      onConnectionState: (state) => {
        states.push(state);
        if (state === "reconnecting") controller.abort();
      },
      onEvent: vi.fn(),
      retryDelayMs: 60_000,
      runId,
      signal: controller.signal,
      workspaceId,
    });

    expect(cursor).toBe(1);
    expect(states).toEqual(["connecting", "open", "reconnecting", "closed"]);
    expect(mocks.authenticatedFetch).toHaveBeenCalledOnce();
  });
});
