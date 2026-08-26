import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as ApiModule from "../api/api";

const mocks = vi.hoisted(() => ({
  DELETE: vi.fn(),
  GET: vi.fn(),
  PATCH: vi.fn(),
  POST: vi.fn(),
  randomUUID: vi.fn(() => "44444444-4444-4444-8444-444444444444"),
  withAccessToken: vi.fn((request: (accessToken: string) => Promise<unknown>) =>
    request("access-token"),
  ),
}));

vi.mock("../api/api", async (importOriginal) => {
  const original = await importOriginal<typeof ApiModule>();
  return {
    ...original,
    apiClient: mocks,
    withAccessToken: mocks.withAccessToken,
  };
});

import { uploadKnowledgeDocument } from "./knowledge-api";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const knowledgeBaseId = "22222222-2222-4222-8222-222222222222";
const fileId = "33333333-3333-4333-8333-333333333333";

describe("knowledge-api", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", {
      randomUUID: mocks.randomUUID,
      subtle: {
        digest: vi.fn(() => Promise.resolve(new Uint8Array(32).buffer)),
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(null, { status: 204 }))),
    );
  });

  it("uses the presign, private transfer, and idempotent completion sequence", async () => {
    const accepted = {
      created: true,
      document: { id: "document-1" },
      job: { id: "job-1", status: "pending" },
      version: { id: "version-1", status: "queued" },
    };
    mocks.POST.mockResolvedValueOnce({
      data: {
        expires_at: "2026-08-23T08:10:00Z",
        fields: { key: "private-key", policy: "signed-policy" },
        file: {
          declared_media_type: "text/plain",
          expected_size: 6,
          id: fileId,
          original_name: "source.txt",
          status: "uploaded",
        },
        method: "POST",
        url: "https://storage.example.test/upload",
      },
      response: new Response(null, { status: 201 }),
    })
      .mockResolvedValueOnce({
        data: accepted,
        response: new Response(null, { status: 202 }),
      })
      .mockResolvedValueOnce({
        data: accepted,
        response: new Response(null, { status: 202 }),
      });
    mocks.withAccessToken
      .mockImplementationOnce((request) => request("access-token"))
      .mockImplementationOnce(async (request) => {
        await request("expired-token");
        return request("fresh-token");
      });

    const file = new File(["source"], "source.txt", { type: "text/plain" });
    Object.defineProperty(file, "arrayBuffer", {
      value: () => Promise.resolve(new TextEncoder().encode("source").buffer),
    });
    const result = await uploadKnowledgeDocument(
      workspaceId,
      knowledgeBaseId,
      file,
      "Source title",
    );

    expect(result).toBe(accepted);
    expect(mocks.POST).toHaveBeenCalledTimes(3);
    expect(mocks.POST.mock.calls[0]?.[0]).toBe(
      "/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/uploads/presign",
    );
    expect(mocks.POST.mock.calls[0]?.[1]).toMatchObject({
      body: {
        declared_media_type: "text/plain",
        expected_sha256: "0".repeat(64),
        expected_size: 6,
        original_name: "source.txt",
      },
      headers: { Authorization: "Bearer access-token" },
    });
    expect(fetch).toHaveBeenCalledWith(
      "https://storage.example.test/upload",
      expect.objectContaining({ credentials: "omit", method: "POST" }),
    );
    expect(mocks.POST.mock.calls[1]?.[0]).toContain("/uploads/{file_id}/complete");
    expect(mocks.POST.mock.calls[1]?.[1]).toMatchObject({
      body: { title: "Source title" },
      params: {
        header: {
          "Idempotency-Key": "knowledge-upload:44444444-4444-4444-8444-444444444444",
        },
        path: { file_id: fileId, knowledge_base_id: knowledgeBaseId, workspace_id: workspaceId },
      },
    });
    expect(mocks.POST.mock.calls[2]?.[1]).toMatchObject({
      params: {
        header: {
          "Idempotency-Key": "knowledge-upload:44444444-4444-4444-8444-444444444444",
        },
      },
    });
    expect(mocks.randomUUID).toHaveBeenCalledOnce();
  });

  it("rejects unsupported files before requesting a presigned upload", async () => {
    await expect(
      uploadKnowledgeDocument(
        workspaceId,
        knowledgeBaseId,
        new File(["image"], "image.png", { type: "image/png" }),
      ),
    ).rejects.toMatchObject({ code: "UNSUPPORTED_MEDIA_TYPE", status: 422 });
    expect(mocks.POST).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });
});
