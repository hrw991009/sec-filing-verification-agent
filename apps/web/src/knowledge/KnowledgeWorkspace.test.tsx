import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as KnowledgeApiModule from "./knowledge-api";
import type { KnowledgeAcceptance, KnowledgeBase, KnowledgeDocument } from "./knowledge-api";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const knowledgeBaseId = "22222222-2222-4222-8222-222222222222";

const knowledgeBase: KnowledgeBase = {
  created_at: "2026-08-23T08:00:00Z",
  description: "私有行业资料",
  document_count: 0,
  id: knowledgeBaseId,
  name: "行业资料",
  revision: 1,
  updated_at: "2026-08-23T08:00:00Z",
  workspace_id: workspaceId,
};

const document: KnowledgeDocument = {
  active_version_id: null,
  created_at: "2026-08-23T08:01:00Z",
  id: "33333333-3333-4333-8333-333333333333",
  knowledge_base_id: knowledgeBaseId,
  latest_version: {
    created_at: "2026-08-23T08:01:00Z",
    document_id: "33333333-3333-4333-8333-333333333333",
    error_code: null,
    file_id: "44444444-4444-4444-8444-444444444444",
    id: "55555555-5555-4555-8555-555555555555",
    ingestion_job_id: "66666666-6666-4666-8666-666666666666",
    processing_started_at: null,
    queued_at: "2026-08-23T08:01:00Z",
    ready_at: null,
    revision: 1,
    status: "queued",
    updated_at: "2026-08-23T08:01:00Z",
    uploaded_at: "2026-08-23T08:01:00Z",
    version: 1,
  },
  latest_version_number: 1,
  revision: 1,
  source: {
    actual_size: 6,
    declared_media_type: "text/plain",
    expected_size: 6,
    file_id: "44444444-4444-4444-8444-444444444444",
    original_name: "source.txt",
  },
  title: "Source title",
  updated_at: "2026-08-23T08:01:00Z",
  workspace_id: workspaceId,
};

const acceptance: KnowledgeAcceptance = {
  created: true,
  document,
  job: {
    events_url: "/events",
    id: document.latest_version.ingestion_job_id,
    outbox_event_id: "77777777-7777-4777-8777-777777777777",
    status: "pending",
  },
  version: document.latest_version,
};

const mocks = vi.hoisted(() => ({
  createKnowledgeBase: vi.fn(),
  deleteKnowledgeBase: vi.fn(),
  listKnowledgeBases: vi.fn(),
  listKnowledgeDocuments: vi.fn(),
  listKnowledgeIngestionEvents: vi.fn(),
  updateKnowledgeBase: vi.fn(),
  uploadKnowledgeDocument: vi.fn(),
}));

vi.mock("./knowledge-api", async (importOriginal) => ({
  ...(await importOriginal<typeof KnowledgeApiModule>()),
  ...mocks,
}));

import { KnowledgeWorkspace } from "./KnowledgeWorkspace";

describe("KnowledgeWorkspace", () => {
  beforeEach(() => {
    mocks.listKnowledgeBases.mockResolvedValue([knowledgeBase]);
    mocks.listKnowledgeDocuments.mockResolvedValue([]);
    mocks.listKnowledgeIngestionEvents.mockResolvedValue([]);
    mocks.uploadKnowledgeDocument.mockResolvedValue(acceptance);
  });

  it("uploads a supported source and renders the queued acceptance immediately", async () => {
    const user = userEvent.setup();
    render(<KnowledgeWorkspace canManage workspaceId={workspaceId} />);

    expect(await screen.findByRole("heading", { level: 2, name: "行业资料" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "上传文档" }));
    const file = new File(["source"], "source.txt", { type: "text/plain" });
    await user.upload(screen.getByLabelText("文件"), file);
    expect(screen.getByLabelText("文档标题")).toHaveValue("source");
    const submit = screen.getByRole("button", { name: "上传" });
    const form = submit.closest("form");
    if (form === null) throw new Error("Upload form was not rendered.");
    fireEvent.submit(form);

    await waitFor(() => {
      expect(mocks.uploadKnowledgeDocument).toHaveBeenCalledWith(
        workspaceId,
        knowledgeBaseId,
        file,
        "source",
      );
    });
    expect(await screen.findByText("Source title")).toBeVisible();
    expect(screen.getByText("排队中")).toBeVisible();
    expect(screen.getByText("1 份文档")).toBeVisible();
  });

  it("keeps viewer access read-only", async () => {
    render(<KnowledgeWorkspace canManage={false} workspaceId={workspaceId} />);
    expect(await screen.findByText("私有行业资料")).toBeVisible();
    expect(screen.queryByRole("button", { name: "上传文档" })).not.toBeInTheDocument();
    expect(screen.queryByTitle("新建知识库")).not.toBeInTheDocument();
    expect(screen.queryByTitle("编辑知识库")).not.toBeInTheDocument();
    expect(screen.queryByTitle("删除知识库")).not.toBeInTheDocument();
  });
});
