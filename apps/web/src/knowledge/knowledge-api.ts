import type { components } from "@industry-platform/api-contract";

import { ApiProblem, apiClient, assertNoContent, unwrapData, withAccessToken } from "../api/api";

export type KnowledgeBase = components["schemas"]["KnowledgeBaseResponse"];
export type KnowledgeDocument = components["schemas"]["DocumentResponse"];
export type KnowledgeDocumentDetail = components["schemas"]["DocumentDetailResponse"];
export type KnowledgeAcceptance = components["schemas"]["KnowledgeAcceptanceResponse"];
export type KnowledgeIngestionEvent = components["schemas"]["KnowledgeIngestionEventResponse"];
export type KnowledgeMediaType = "application/pdf" | "text/markdown" | "text/plain";

const MAX_DOCUMENT_BYTES = 25 * 1024 * 1024;
const mediaTypeByExtension: Readonly<Record<string, KnowledgeMediaType>> = {
  ".markdown": "text/markdown",
  ".md": "text/markdown",
  ".pdf": "application/pdf",
  ".txt": "text/plain",
};

function authorization(accessToken: string) {
  return { Authorization: `Bearer ${accessToken}` };
}

function revisionHeader(revision: number) {
  if (!Number.isSafeInteger(revision) || revision < 1) {
    throw new RangeError("The Knowledge revision is invalid.");
  }
  return { "If-Match": `"${String(revision)}"` };
}

function documentMediaType(file: File): KnowledgeMediaType | null {
  const dot = file.name.lastIndexOf(".");
  return dot < 0 ? null : (mediaTypeByExtension[file.name.slice(dot).toLowerCase()] ?? null);
}

function defaultDocumentTitle(file: File): string {
  const dot = file.name.lastIndexOf(".");
  return (dot > 0 ? file.name.slice(0, dot) : file.name).trim();
}

async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

export function listKnowledgeBases(workspaceId: string): Promise<KnowledgeBase[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["KnowledgeBaseCollectionResponse"]>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/knowledge-bases", {
        headers: authorization(accessToken),
        params: { path: { workspace_id: workspaceId }, query: { limit: 100 } },
      }),
    );
    return response.knowledge_bases;
  });
}

export function createKnowledgeBase(
  workspaceId: string,
  request: components["schemas"]["CreateKnowledgeBaseRequest"],
): Promise<KnowledgeBase> {
  return withAccessToken(async (accessToken) =>
    unwrapData<KnowledgeBase>(
      await apiClient.POST("/api/v1/workspaces/{workspace_id}/knowledge-bases", {
        body: request,
        headers: authorization(accessToken),
        params: { path: { workspace_id: workspaceId } },
      }),
    ),
  );
}

export function updateKnowledgeBase(
  workspaceId: string,
  knowledgeBase: KnowledgeBase,
  request: components["schemas"]["UpdateKnowledgeBaseRequest"],
): Promise<KnowledgeBase> {
  return withAccessToken(async (accessToken) =>
    unwrapData<KnowledgeBase>(
      await apiClient.PATCH(
        "/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}",
        {
          body: request,
          headers: authorization(accessToken),
          params: {
            header: revisionHeader(knowledgeBase.revision),
            path: {
              knowledge_base_id: knowledgeBase.id,
              workspace_id: workspaceId,
            },
          },
        },
      ),
    ),
  );
}

export function deleteKnowledgeBase(
  workspaceId: string,
  knowledgeBase: KnowledgeBase,
): Promise<void> {
  return withAccessToken(async (accessToken) => {
    assertNoContent(
      await apiClient.DELETE(
        "/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}",
        {
          headers: authorization(accessToken),
          params: {
            header: revisionHeader(knowledgeBase.revision),
            path: {
              knowledge_base_id: knowledgeBase.id,
              workspace_id: workspaceId,
            },
          },
        },
      ),
    );
  });
}

export function listKnowledgeDocuments(
  workspaceId: string,
  knowledgeBaseId: string,
): Promise<KnowledgeDocument[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["DocumentCollectionResponse"]>(
      await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents",
        {
          headers: authorization(accessToken),
          params: {
            path: { knowledge_base_id: knowledgeBaseId, workspace_id: workspaceId },
            query: { limit: 100 },
          },
        },
      ),
    );
    return response.documents;
  });
}

export function getKnowledgeDocument(
  workspaceId: string,
  knowledgeBaseId: string,
  documentId: string,
): Promise<KnowledgeDocumentDetail> {
  return withAccessToken(async (accessToken) =>
    unwrapData<KnowledgeDocumentDetail>(
      await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents/{document_id}",
        {
          headers: authorization(accessToken),
          params: {
            path: {
              document_id: documentId,
              knowledge_base_id: knowledgeBaseId,
              workspace_id: workspaceId,
            },
          },
        },
      ),
    ),
  );
}

export function listKnowledgeIngestionEvents(
  workspaceId: string,
  knowledgeBaseId: string,
  document: KnowledgeDocument,
): Promise<KnowledgeIngestionEvent[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["KnowledgeIngestionEventCollectionResponse"]>(
      await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents/{document_id}/versions/{version_id}/events",
        {
          headers: authorization(accessToken),
          params: {
            path: {
              document_id: document.id,
              knowledge_base_id: knowledgeBaseId,
              version_id: document.latest_version.id,
              workspace_id: workspaceId,
            },
          },
        },
      ),
    );
    return response.events;
  });
}

export async function uploadKnowledgeDocument(
  workspaceId: string,
  knowledgeBaseId: string,
  file: File,
  title = defaultDocumentTitle(file),
): Promise<KnowledgeAcceptance> {
  const mediaType = documentMediaType(file);
  if (mediaType === null) {
    throw new ApiProblem(422, {
      code: "UNSUPPORTED_MEDIA_TYPE",
      detail: "仅支持 PDF、TXT、Markdown 文件。",
    });
  }
  if (file.size < 1 || file.size > MAX_DOCUMENT_BYTES) {
    throw new ApiProblem(422, {
      code: file.size < 1 ? "EMPTY_FILE" : "FILE_TOO_LARGE",
      detail: file.size < 1 ? "不能上传空文件。" : "文件不能超过 25 MB。",
    });
  }
  const normalizedTitle = title.trim();
  if (!normalizedTitle) {
    throw new ApiProblem(422, { code: "INVALID_TITLE", detail: "文档标题不能为空。" });
  }

  const expectedSha256 = await sha256(file);
  const ticket = await withAccessToken(async (accessToken) =>
    unwrapData<components["schemas"]["KnowledgeUploadResponse"]>(
      await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/uploads/presign",
        {
          body: {
            declared_media_type: mediaType,
            expected_sha256: expectedSha256,
            expected_size: file.size,
            original_name: file.name,
          },
          headers: authorization(accessToken),
          params: {
            path: { knowledge_base_id: knowledgeBaseId, workspace_id: workspaceId },
          },
        },
      ),
    ),
  );

  const form = new FormData();
  for (const [name, value] of Object.entries(ticket.fields)) form.append(name, value);
  form.append("file", file, file.name);
  let uploaded: Response;
  try {
    uploaded = await fetch(ticket.url, { body: form, credentials: "omit", method: ticket.method });
  } catch {
    throw new ApiProblem(503, {
      code: "FILE_UPLOAD_TRANSPORT_FAILED",
      detail: "文件未能传输到私有存储。",
    });
  }
  if (!uploaded.ok) {
    throw new ApiProblem(uploaded.status, {
      code: "FILE_UPLOAD_TRANSPORT_FAILED",
      detail: "文件未能传输到私有存储。",
    });
  }

  const idempotencyKey = `knowledge-upload:${crypto.randomUUID()}`;
  return withAccessToken(async (accessToken) =>
    unwrapData<KnowledgeAcceptance>(
      await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/uploads/{file_id}/complete",
        {
          body: { title: normalizedTitle },
          headers: authorization(accessToken),
          params: {
            header: { "Idempotency-Key": idempotencyKey },
            path: {
              file_id: ticket.file.id,
              knowledge_base_id: knowledgeBaseId,
              workspace_id: workspaceId,
            },
          },
        },
      ),
    ),
  );
}
