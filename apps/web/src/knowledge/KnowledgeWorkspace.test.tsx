import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as KnowledgeApiModule from "./knowledge-api";
import type {
  KnowledgeAcceptance,
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeDocumentDetail,
} from "./knowledge-api";

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
  deletion_error_code: null,
  deletion_job_id: null,
  id: "33333333-3333-4333-8333-333333333333",
  knowledge_base_id: knowledgeBaseId,
  latest_version: {
    chunker_config: { max_characters: 1200, overlap_characters: 120 },
    chunker_name: "bounded-page-chunker",
    chunker_version: "1.0.0",
    created_at: "2026-08-23T08:01:00Z",
    document_id: "33333333-3333-4333-8333-333333333333",
    error_code: null,
    file_id: "44444444-4444-4444-8444-444444444444",
    id: "55555555-5555-4555-8555-555555555555",
    ingestion_job_id: "66666666-6666-4666-8666-666666666666",
    embedding_config: {
      batch_size: 32,
      dimension: 64,
      model: "feature-hash-64",
      normalization: "l2",
      provider: "deterministic-hash",
      timeout_seconds: 30,
      version: "1.0.0",
    },
    index_config: {
      elasticsearch_index: "knowledge_chunks_v1",
      index_version: "knowledge-index-v1",
      milvus_collection: "knowledge_chunks_v1",
    },
    parser_config: { budget: { max_pages: 250 } },
    parser_name: "pdfplumber-rapidocr",
    parser_schema_version: 1,
    parser_version: "1.0.0",
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

const parsedDocument: KnowledgeDocument = {
  ...document,
  latest_version: {
    ...document.latest_version,
    processing_started_at: "2026-08-23T08:02:00Z",
    revision: 6,
    status: "parsed",
    updated_at: "2026-08-23T08:03:00Z",
  },
};

const detail: KnowledgeDocumentDetail = {
  assets: [
    {
      bbox: [60, 180, 540, 348],
      content_hash: "a".repeat(64),
      document_version_id: document.latest_version.id,
      html: "<table><tbody><tr><td>North</td><td>18</td></tr></tbody></table>",
      id: "88888888-8888-4888-8888-888888888888",
      kind: "table",
      ordinal: 1,
      page_number: 1,
      preview_mime_type: "image/png",
      preview_sha256: "b".repeat(64),
      preview_url: "https://minio.example.test/private-table.png?signature=test",
      title_path: ["Semiconductors", "Capacity"],
    },
  ],
  chunks: [
    {
      asset_ids: ["88888888-8888-4888-8888-888888888888"],
      bbox: [0, 0, 612, 792],
      content_hash: "c".repeat(64),
      document_version_id: document.latest_version.id,
      id: "99999999-9999-4999-8999-999999999999",
      ordinal: 1,
      page_number: 1,
      text: "Utilization reached 82 percent.",
      title_path: ["Semiconductors", "Capacity"],
      token_count: 5,
    },
  ],
  document: parsedDocument,
  ingestion_checkpoints: ["validating", "parsing", "extracting_assets", "chunking"].map(
    (stage, index) => ({
      attempt_count: 1,
      completed_at: `2026-08-23T08:02:0${String(index)}Z`,
      document_version_id: document.latest_version.id,
      fencing_token: 1,
      id: `aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa${String(index)}`,
      ingestion_job_id: document.latest_version.ingestion_job_id,
      input_hash: "d".repeat(64),
      output_hash: "e".repeat(64),
      stage: stage as "validating" | "parsing" | "extracting_assets" | "chunking",
      stage_sequence: index + 1,
      stats: {},
    }),
  ),
  indexes: [
    {
      attempt_count: 1,
      chunk_id: "99999999-9999-4999-8999-999999999999",
      document_version_id: document.latest_version.id,
      error_code: null,
      external_id: "99999999-9999-4999-8999-999999999999:knowledge-index-v1",
      id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc1",
      index_version: "knowledge-index-v1",
      indexed_at: "2026-08-23T08:03:00Z",
      kind: "vector",
      status: "succeeded",
    },
    {
      attempt_count: 1,
      chunk_id: "99999999-9999-4999-8999-999999999999",
      document_version_id: document.latest_version.id,
      error_code: null,
      external_id: "99999999-9999-4999-8999-999999999999:knowledge-index-v1",
      id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc2",
      index_version: "knowledge-index-v1",
      indexed_at: "2026-08-23T08:03:01Z",
      kind: "lexical",
      status: "succeeded",
    },
  ],
  pages: [
    {
      bbox: [0, 0, 612, 792],
      content_hash: "f".repeat(64),
      document_version_id: document.latest_version.id,
      height_points: 792,
      id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      page_number: 1,
      text: "Utilization reached 82 percent.",
      text_source: "digital",
      title_path: ["Semiconductors", "Capacity"],
      width_points: 612,
    },
  ],
  versions: [{ source: document.source, version: parsedDocument.latest_version }],
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
  activateKnowledgeDocumentVersion: vi.fn(),
  createKnowledgeDocumentVersion: vi.fn(),
  createKnowledgeBase: vi.fn(),
  deleteKnowledgeBase: vi.fn(),
  deleteKnowledgeDocument: vi.fn(),
  getKnowledgeDocument: vi.fn(),
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
    mocks.getKnowledgeDocument.mockResolvedValue(detail);
    mocks.uploadKnowledgeDocument.mockResolvedValue(acceptance);
    mocks.createKnowledgeDocumentVersion.mockResolvedValue({
      ...acceptance,
      document: parsedDocument,
      version: parsedDocument.latest_version,
    });
    mocks.deleteKnowledgeDocument.mockResolvedValue({
      document_id: document.id,
      job: acceptance.job,
      revision: 2,
      status: "deleting",
    });
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

  it("shows traceable stages, pages, chunks, and private asset previews", async () => {
    const user = userEvent.setup();
    mocks.listKnowledgeDocuments.mockResolvedValue([parsedDocument]);
    render(<KnowledgeWorkspace canManage workspaceId={workspaceId} />);

    await user.click(await screen.findByRole("button", { name: "查看 Source title 的解析详情" }));
    expect(mocks.getKnowledgeDocument).toHaveBeenCalledWith(
      workspaceId,
      knowledgeBaseId,
      document.id,
    );
    expect(await screen.findByRole("heading", { name: "解析详情" })).toBeVisible();
    expect(screen.getByText("源文件校验")).toBeVisible();
    expect(screen.getByText("文本切分")).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "索引" }));
    expect(screen.getByText("向量")).toBeVisible();
    expect(screen.getByText("关键词")).toBeVisible();
    expect(screen.getAllByText("成功")).toHaveLength(2);

    await user.click(screen.getByRole("tab", { name: "页面" }));
    expect(screen.getByText("数字文本")).toBeVisible();
    expect(screen.getByText("Utilization reached 82 percent.")).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "资产" }));
    expect(screen.getByRole("img", { name: "第 1 页表格预览" })).toHaveAttribute(
      "src",
      detail.assets[0]?.preview_url,
    );
    expect(screen.getByRole("cell", { name: "North" })).toBeVisible();
  });

  it("submits reindex and cross-store deletion from document actions", async () => {
    const user = userEvent.setup();
    mocks.listKnowledgeDocuments.mockResolvedValue([parsedDocument]);
    render(<KnowledgeWorkspace canManage workspaceId={workspaceId} />);

    await user.click(await screen.findByRole("button", { name: "重新索引 Source title" }));
    expect(mocks.createKnowledgeDocumentVersion).toHaveBeenCalledWith(workspaceId, parsedDocument);

    mocks.listKnowledgeDocuments.mockResolvedValue([parsedDocument]);
    await user.click(screen.getByRole("button", { name: "删除 Source title" }));
    expect(screen.getByRole("heading", { name: "删除文档" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "删除" }));
    expect(mocks.deleteKnowledgeDocument).toHaveBeenCalledWith(workspaceId, parsedDocument);
  });
});
