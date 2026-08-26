import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type SubmitEvent,
} from "react";

import { Icon } from "../chat/icons";
import { formatBytes, publicError, relativeTime } from "../chat/chat-workbench-model";
import {
  activateKnowledgeDocumentVersion,
  cancelKnowledgeDocumentVersion,
  createKnowledgeDocumentVersion,
  createKnowledgeBase,
  deleteKnowledgeDocument,
  deleteKnowledgeBase,
  getKnowledgeDocument,
  listKnowledgeBases,
  listKnowledgeDocuments,
  listKnowledgeIngestionEvents,
  updateKnowledgeBase,
  uploadKnowledgeDocument,
  type KnowledgeBase,
  type KnowledgeDocument,
  type KnowledgeDocumentDetail,
  type KnowledgeIngestionEvent,
} from "./knowledge-api";
import "./knowledge.css";

interface KnowledgeWorkspaceProps {
  readonly canManage: boolean;
  readonly workspaceId: string;
}

const statusNames: Readonly<Record<string, string>> = {
  cancelled: "已取消",
  chunking: "正在切分",
  deleted: "已删除",
  deleting: "正在删除",
  embedding: "正在生成向量",
  extracting_assets: "正在提取资源",
  failed: "失败",
  lexical_indexing: "正在建立关键词索引",
  parsing: "正在解析",
  parsed: "解析完成",
  queued: "排队中",
  ready: "可用",
  retrying: "等待重试",
  validating: "正在校验",
  vector_indexing: "正在建立向量索引",
};

const eventNames: Readonly<Record<string, string>> = {
  created: "任务已受理",
};

const stageNames: Readonly<Record<string, string>> = {
  chunking: "文本切分",
  embedding: "向量生成",
  extracting_assets: "资源提取",
  lexical_indexing: "关键词索引",
  parsing: "内容解析",
  validating: "源文件校验",
  vector_indexing: "向量索引",
};

const textSourceNames: Readonly<Record<string, string>> = {
  digital: "数字文本",
  markdown: "Markdown",
  ocr: "OCR",
  plain_text: "纯文本",
};

const cancellableStatuses = new Set([
  "queued",
  "validating",
  "parsing",
  "extracting_assets",
  "chunking",
  "embedding",
  "vector_indexing",
  "lexical_indexing",
  "retrying",
]);

const reindexableStatuses = new Set(["parsed", "ready", "failed", "cancelled"]);

type DetailTab = "stages" | "versions" | "indexes" | "pages" | "chunks" | "assets";

function tableRows(html: string): string[][] {
  if (typeof DOMParser === "undefined") return [];
  const parsed = new DOMParser().parseFromString(html, "text/html");
  return [...parsed.querySelectorAll("tr")].map((row) =>
    [...row.querySelectorAll("th,td")].map((cell) => cell.textContent.trim()),
  );
}

function AssetTable({ html }: { readonly html: string }) {
  const rows = tableRows(html);
  if (rows.length === 0) return <pre className="knowledge-asset-html">{html}</pre>;
  return (
    <div className="knowledge-asset-table-wrap">
      <table className="knowledge-asset-table">
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`row-${String(rowIndex)}`}>
              {row.map((cell, cellIndex) => (
                <td key={`cell-${String(rowIndex)}-${String(cellIndex)}`}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function titleFromFile(file: File): string {
  const dot = file.name.lastIndexOf(".");
  return (dot > 0 ? file.name.slice(0, dot) : file.name).trim();
}

function runtimeConfigValue(value: unknown): string {
  return typeof value === "string" && value.trim() ? value : "-";
}

export function KnowledgeWorkspace({ canManage, workspaceId }: KnowledgeWorkspaceProps) {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [loadingBases, setLoadingBases] = useState(true);
  const [loadingDocuments, setLoadingDocuments] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [documentsError, setDocumentsError] = useState<string | null>(null);
  const [editorMode, setEditorMode] = useState<"create" | "edit" | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [savingBase, setSavingBase] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploading, setUploading] = useState(false);
  const [eventsFor, setEventsFor] = useState<KnowledgeDocument | null>(null);
  const [events, setEvents] = useState<KnowledgeIngestionEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [detailFor, setDetailFor] = useState<KnowledgeDocument | null>(null);
  const [detail, setDetail] = useState<KnowledgeDocumentDetail | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("stages");
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [documentAction, setDocumentAction] = useState<string | null>(null);
  const [deleteDocumentFor, setDeleteDocumentFor] = useState<KnowledgeDocument | null>(null);
  const documentRequestRef = useRef(0);
  const detailRequestRef = useRef(0);

  const selectedKnowledgeBase = useMemo(
    () => knowledgeBases.find((item) => item.id === selectedId) ?? null,
    [knowledgeBases, selectedId],
  );

  useEffect(() => {
    let active = true;
    void listKnowledgeBases(workspaceId)
      .then((loaded) => {
        if (!active) return;
        setKnowledgeBases(loaded);
        setDocuments([]);
        setDocumentsError(null);
        setLoadingDocuments(loaded.length > 0);
        setSelectedId(loaded[0]?.id ?? null);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setError(publicError(caught));
        setKnowledgeBases([]);
        setDocuments([]);
        setSelectedId(null);
      })
      .finally(() => {
        if (active) setLoadingBases(false);
      });
    return () => {
      active = false;
    };
  }, [workspaceId]);

  const loadDocuments = useCallback(
    async (knowledgeBaseId: string): Promise<void> => {
      const requestNumber = documentRequestRef.current + 1;
      documentRequestRef.current = requestNumber;
      setLoadingDocuments(true);
      setDocumentsError(null);
      try {
        const loaded = await listKnowledgeDocuments(workspaceId, knowledgeBaseId);
        if (documentRequestRef.current === requestNumber) setDocuments(loaded);
      } catch (caught: unknown) {
        if (documentRequestRef.current === requestNumber) {
          setDocuments([]);
          setDocumentsError(publicError(caught));
        }
      } finally {
        if (documentRequestRef.current === requestNumber) setLoadingDocuments(false);
      }
    },
    [workspaceId],
  );

  useEffect(() => {
    if (selectedId === null) {
      documentRequestRef.current += 1;
      return;
    }
    const requestNumber = documentRequestRef.current + 1;
    documentRequestRef.current = requestNumber;
    void listKnowledgeDocuments(workspaceId, selectedId)
      .then((loaded) => {
        if (documentRequestRef.current === requestNumber) setDocuments(loaded);
      })
      .catch((caught: unknown) => {
        if (documentRequestRef.current === requestNumber) {
          setDocuments([]);
          setDocumentsError(publicError(caught));
        }
      })
      .finally(() => {
        if (documentRequestRef.current === requestNumber) setLoadingDocuments(false);
      });
  }, [selectedId, workspaceId]);

  function openCreate(): void {
    setDraftName("");
    setDraftDescription("");
    setEditorMode("create");
  }

  function openEdit(): void {
    if (selectedKnowledgeBase === null) return;
    setDraftName(selectedKnowledgeBase.name);
    setDraftDescription(selectedKnowledgeBase.description ?? "");
    setEditorMode("edit");
  }

  async function saveKnowledgeBase(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const name = draftName.trim();
    if (!name || editorMode === null) return;
    setSavingBase(true);
    setError(null);
    try {
      const description = draftDescription.trim();
      const request = { description: description ? description : null, name };
      if (editorMode === "create") {
        const created = await createKnowledgeBase(workspaceId, request);
        setKnowledgeBases((current) => [created, ...current]);
        setDocuments([]);
        setLoadingDocuments(true);
        setSelectedId(created.id);
      } else if (selectedKnowledgeBase !== null) {
        const updated = await updateKnowledgeBase(workspaceId, selectedKnowledgeBase, request);
        setKnowledgeBases((current) =>
          current.map((item) => (item.id === updated.id ? updated : item)),
        );
      }
      setEditorMode(null);
    } catch (caught: unknown) {
      setError(publicError(caught));
    } finally {
      setSavingBase(false);
    }
  }

  async function confirmDelete(): Promise<void> {
    if (selectedKnowledgeBase === null) return;
    setSavingBase(true);
    setError(null);
    try {
      await deleteKnowledgeBase(workspaceId, selectedKnowledgeBase);
      const remaining = knowledgeBases.filter((item) => item.id !== selectedKnowledgeBase.id);
      setKnowledgeBases(remaining);
      setDocuments([]);
      setLoadingDocuments(remaining.length > 0);
      setSelectedId(remaining[0]?.id ?? null);
      setDeleteOpen(false);
    } catch (caught: unknown) {
      setError(publicError(caught));
      setDeleteOpen(false);
    } finally {
      setSavingBase(false);
    }
  }

  function chooseUploadFile(event: ChangeEvent<HTMLInputElement>): void {
    const file = event.currentTarget.files?.[0] ?? null;
    setUploadFile(file);
    if (file !== null) setUploadTitle(titleFromFile(file));
  }

  async function submitUpload(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (selectedKnowledgeBase === null || uploadFile === null) return;
    setUploading(true);
    setDocumentsError(null);
    try {
      const accepted = await uploadKnowledgeDocument(
        workspaceId,
        selectedKnowledgeBase.id,
        uploadFile,
        uploadTitle,
      );
      setDocuments((current) => [
        accepted.document,
        ...current.filter((item) => item.id !== accepted.document.id),
      ]);
      setKnowledgeBases((current) =>
        current.map((item) =>
          item.id === selectedKnowledgeBase.id
            ? { ...item, document_count: item.document_count + (accepted.created ? 1 : 0) }
            : item,
        ),
      );
      setUploadOpen(false);
      setUploadFile(null);
      setUploadTitle("");
    } catch (caught: unknown) {
      setDocumentsError(publicError(caught));
    } finally {
      setUploading(false);
    }
  }

  async function openEvents(document: KnowledgeDocument): Promise<void> {
    setEventsFor(document);
    setEvents([]);
    setEventsError(null);
    setEventsLoading(true);
    try {
      setEvents(
        await listKnowledgeIngestionEvents(workspaceId, document.knowledge_base_id, document),
      );
    } catch (caught: unknown) {
      setEventsError(publicError(caught));
    } finally {
      setEventsLoading(false);
    }
  }

  async function openDetail(document: KnowledgeDocument): Promise<void> {
    const requestNumber = detailRequestRef.current + 1;
    detailRequestRef.current = requestNumber;
    setDetailFor(document);
    setDetail(null);
    setDetailTab("stages");
    setDetailError(null);
    setDetailLoading(true);
    try {
      const loaded = await getKnowledgeDocument(
        workspaceId,
        document.knowledge_base_id,
        document.id,
      );
      if (detailRequestRef.current === requestNumber) setDetail(loaded);
    } catch (caught: unknown) {
      if (detailRequestRef.current === requestNumber) setDetailError(publicError(caught));
    } finally {
      if (detailRequestRef.current === requestNumber) setDetailLoading(false);
    }
  }

  function closeDetail(): void {
    detailRequestRef.current += 1;
    setDetailFor(null);
    setDetail(null);
  }

  async function reindexDocument(document: KnowledgeDocument): Promise<void> {
    setDocumentAction(document.id);
    setDocumentsError(null);
    try {
      const accepted = await createKnowledgeDocumentVersion(workspaceId, document);
      setDocuments((current) =>
        current.map((item) => (item.id === document.id ? accepted.document : item)),
      );
      closeDetail();
    } catch (caught: unknown) {
      setDocumentsError(publicError(caught));
    } finally {
      setDocumentAction(null);
    }
  }

  async function activateVersion(versionId: string): Promise<void> {
    if (detail === null || detailFor === null) return;
    setDocumentAction(detailFor.id);
    setDetailError(null);
    try {
      await activateKnowledgeDocumentVersion(workspaceId, detail.document, versionId);
      await loadDocuments(detail.document.knowledge_base_id);
      setDetail(
        await getKnowledgeDocument(
          workspaceId,
          detail.document.knowledge_base_id,
          detail.document.id,
        ),
      );
    } catch (caught: unknown) {
      setDetailError(publicError(caught));
    } finally {
      setDocumentAction(null);
    }
  }

  async function confirmDocumentDelete(): Promise<void> {
    if (deleteDocumentFor === null) return;
    const document = deleteDocumentFor;
    setDocumentAction(document.id);
    setDocumentsError(null);
    try {
      await deleteKnowledgeDocument(workspaceId, document);
      setDeleteDocumentFor(null);
      closeDetail();
      await loadDocuments(document.knowledge_base_id);
    } catch (caught: unknown) {
      setDocumentsError(publicError(caught));
      setDeleteDocumentFor(null);
    } finally {
      setDocumentAction(null);
    }
  }

  async function cancelIngestion(document: KnowledgeDocument): Promise<void> {
    setDocumentAction(document.id);
    setDocumentsError(null);
    try {
      await cancelKnowledgeDocumentVersion(workspaceId, document);
      await loadDocuments(document.knowledge_base_id);
    } catch (caught: unknown) {
      setDocumentsError(publicError(caught));
    } finally {
      setDocumentAction(null);
    }
  }

  return (
    <section className="knowledge-workspace" aria-labelledby="knowledge-title">
      <aside className="knowledge-sidebar">
        <div className="knowledge-sidebar__header">
          <div>
            <h1 id="knowledge-title">知识库</h1>
            <span>{knowledgeBases.length} 个知识库</span>
          </div>
          {canManage ? (
            <button
              aria-label="新建知识库"
              className="icon-button"
              onClick={openCreate}
              title="新建知识库"
              type="button"
            >
              <Icon name="new" />
            </button>
          ) : null}
        </div>

        {loadingBases ? <p className="knowledge-state">正在加载...</p> : null}
        {error === null ? null : <p className="knowledge-error">{error}</p>}
        {!loadingBases && knowledgeBases.length === 0 ? (
          <div className="knowledge-empty knowledge-empty--sidebar">
            <Icon name="document" />
            <strong>暂无知识库</strong>
          </div>
        ) : null}
        <div className="knowledge-base-list">
          {knowledgeBases.map((item) => (
            <button
              aria-current={item.id === selectedId ? "page" : undefined}
              className={
                item.id === selectedId
                  ? "knowledge-base-item knowledge-base-item--active"
                  : "knowledge-base-item"
              }
              key={item.id}
              onClick={() => {
                if (item.id === selectedId) return;
                setDocuments([]);
                setDocumentsError(null);
                setLoadingDocuments(true);
                closeDetail();
                setSelectedId(item.id);
              }}
              type="button"
            >
              <span>{item.name}</span>
              <small>{item.document_count} 份文档</small>
            </button>
          ))}
        </div>
      </aside>

      <div className="knowledge-main">
        {selectedKnowledgeBase === null ? (
          <div className="knowledge-empty knowledge-empty--main">
            <Icon name="document" />
            <strong>选择一个知识库</strong>
          </div>
        ) : (
          <>
            <header className="knowledge-header">
              <div>
                <h2>{selectedKnowledgeBase.name}</h2>
                <p>{selectedKnowledgeBase.description ?? "未填写描述"}</p>
              </div>
              <div className="knowledge-header__actions">
                <button
                  aria-label="刷新文档状态"
                  className="icon-button"
                  disabled={loadingDocuments}
                  onClick={() => void loadDocuments(selectedKnowledgeBase.id)}
                  title="刷新文档状态"
                  type="button"
                >
                  <Icon name="refresh" />
                </button>
                {canManage ? (
                  <>
                    <button
                      aria-label="编辑知识库"
                      className="icon-button"
                      onClick={openEdit}
                      title="编辑知识库"
                      type="button"
                    >
                      <Icon name="edit" />
                    </button>
                    <button
                      aria-label="删除知识库"
                      className="icon-button icon-button--danger"
                      onClick={() => {
                        setDeleteOpen(true);
                      }}
                      title="删除知识库"
                      type="button"
                    >
                      <Icon name="trash" />
                    </button>
                    <button
                      className="knowledge-primary-button"
                      onClick={() => {
                        setUploadOpen(true);
                      }}
                      type="button"
                    >
                      <Icon name="attachment" />
                      上传文档
                    </button>
                  </>
                ) : null}
              </div>
            </header>

            <div className="knowledge-table-header">
              <div>
                <strong>文档</strong>
                <span>{documents.length} 份</span>
              </div>
              <span>PDF / TXT / Markdown · 最大 25 MB</span>
            </div>
            {documentsError === null ? null : <p className="knowledge-error">{documentsError}</p>}
            {loadingDocuments ? <p className="knowledge-state">正在刷新文档...</p> : null}
            {!loadingDocuments && documents.length === 0 ? (
              <div className="knowledge-empty knowledge-empty--main">
                <Icon name="document" />
                <strong>暂无文档</strong>
              </div>
            ) : null}
            {documents.length > 0 ? (
              <div className="knowledge-table-wrap">
                <table className="knowledge-table">
                  <thead>
                    <tr>
                      <th>名称</th>
                      <th>来源文件</th>
                      <th>版本</th>
                      <th>状态</th>
                      <th>提交时间</th>
                      <th aria-label="操作" />
                    </tr>
                  </thead>
                  <tbody>
                    {documents.map((document) => (
                      <tr key={document.id}>
                        <td>
                          <strong>{document.title}</strong>
                          <small>{formatBytes(document.source.actual_size)}</small>
                        </td>
                        <td data-label="来源">{document.source.original_name}</td>
                        <td data-label="版本">v{document.latest_version_number}</td>
                        <td data-label="状态">
                          <span
                            className={`knowledge-status knowledge-status--${document.latest_version.status}`}
                          >
                            {statusNames[document.latest_version.status] ??
                              document.latest_version.status}
                          </span>
                        </td>
                        <td data-label="提交">{relativeTime(document.latest_version.queued_at)}</td>
                        <td>
                          <div className="knowledge-row-actions">
                            <button
                              aria-label={`查看 ${document.title} 的解析详情`}
                              className="icon-button icon-button--small"
                              onClick={() => void openDetail(document)}
                              title="查看解析详情"
                              type="button"
                            >
                              <Icon name="document" />
                            </button>
                            <button
                              aria-label={`查看 ${document.title} 的受理事件`}
                              className="icon-button icon-button--small"
                              onClick={() => void openEvents(document)}
                              title="查看受理事件"
                              type="button"
                            >
                              <Icon name="bolt" />
                            </button>
                            {canManage ? (
                              <>
                                <button
                                  aria-label={`重新索引 ${document.title}`}
                                  className="icon-button icon-button--small"
                                  disabled={
                                    documentAction === document.id ||
                                    !reindexableStatuses.has(document.latest_version.status)
                                  }
                                  onClick={() => void reindexDocument(document)}
                                  title="重新索引"
                                  type="button"
                                >
                                  <Icon name="refresh" />
                                </button>
                                <button
                                  aria-label={`取消 ${document.title} 的入库任务`}
                                  className="icon-button icon-button--small"
                                  disabled={
                                    documentAction === document.id ||
                                    !cancellableStatuses.has(document.latest_version.status)
                                  }
                                  onClick={() => void cancelIngestion(document)}
                                  title="取消入库"
                                  type="button"
                                >
                                  <Icon name="stop" />
                                </button>
                                <button
                                  aria-label={`删除 ${document.title}`}
                                  className="icon-button icon-button--small icon-button--danger"
                                  disabled={
                                    documentAction === document.id ||
                                    document.latest_version.status === "deleting"
                                  }
                                  onClick={() => {
                                    setDeleteDocumentFor(document);
                                  }}
                                  title="删除文档"
                                  type="button"
                                >
                                  <Icon name="trash" />
                                </button>
                              </>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </>
        )}
      </div>

      {editorMode === null ? null : (
        <div className="knowledge-dialog-backdrop" role="presentation">
          <form className="knowledge-dialog" onSubmit={(event) => void saveKnowledgeBase(event)}>
            <header>
              <h2>{editorMode === "create" ? "新建知识库" : "编辑知识库"}</h2>
              <button
                aria-label="关闭"
                className="icon-button"
                onClick={() => {
                  setEditorMode(null);
                }}
                type="button"
              >
                <Icon name="close" />
              </button>
            </header>
            <label>
              名称
              <input
                maxLength={120}
                onChange={(event) => {
                  setDraftName(event.currentTarget.value);
                }}
                required
                value={draftName}
              />
            </label>
            <label>
              描述
              <textarea
                maxLength={1_000}
                onChange={(event) => {
                  setDraftDescription(event.currentTarget.value);
                }}
                rows={4}
                value={draftDescription}
              />
            </label>
            <footer>
              <button
                className="knowledge-secondary-button"
                onClick={() => {
                  setEditorMode(null);
                }}
                type="button"
              >
                取消
              </button>
              <button className="knowledge-primary-button" disabled={savingBase} type="submit">
                {savingBase ? "保存中..." : "保存"}
              </button>
            </footer>
          </form>
        </div>
      )}

      {!deleteOpen || selectedKnowledgeBase === null ? null : (
        <div className="knowledge-dialog-backdrop" role="presentation">
          <section
            aria-modal="true"
            className="knowledge-dialog knowledge-dialog--compact"
            role="dialog"
          >
            <header>
              <h2>删除知识库</h2>
              <button
                aria-label="关闭"
                className="icon-button"
                onClick={() => {
                  setDeleteOpen(false);
                }}
                type="button"
              >
                <Icon name="close" />
              </button>
            </header>
            <p>确认删除“{selectedKnowledgeBase.name}”？只有空知识库可以删除。</p>
            <footer>
              <button
                className="knowledge-secondary-button"
                onClick={() => {
                  setDeleteOpen(false);
                }}
                type="button"
              >
                取消
              </button>
              <button
                className="knowledge-danger-button"
                disabled={savingBase}
                onClick={() => void confirmDelete()}
                type="button"
              >
                删除
              </button>
            </footer>
          </section>
        </div>
      )}

      {!uploadOpen || selectedKnowledgeBase === null ? null : (
        <div className="knowledge-dialog-backdrop" role="presentation">
          <form className="knowledge-dialog" onSubmit={(event) => void submitUpload(event)}>
            <header>
              <h2>上传文档</h2>
              <button
                aria-label="关闭"
                className="icon-button"
                disabled={uploading}
                onClick={() => {
                  setUploadOpen(false);
                }}
                type="button"
              >
                <Icon name="close" />
              </button>
            </header>
            <label>
              文件
              <input
                accept=".pdf,.txt,.md,.markdown,application/pdf,text/plain,text/markdown"
                disabled={uploading}
                onChange={chooseUploadFile}
                required
                type="file"
              />
            </label>
            <label>
              文档标题
              <input
                disabled={uploading}
                maxLength={240}
                onChange={(event) => {
                  setUploadTitle(event.currentTarget.value);
                }}
                required
                value={uploadTitle}
              />
            </label>
            <footer>
              <button
                className="knowledge-secondary-button"
                disabled={uploading}
                onClick={() => {
                  setUploadOpen(false);
                }}
                type="button"
              >
                取消
              </button>
              <button
                className="knowledge-primary-button"
                disabled={uploading || uploadFile === null}
                type="submit"
              >
                {uploading ? "正在受理..." : "上传"}
              </button>
            </footer>
          </form>
        </div>
      )}

      {deleteDocumentFor === null ? null : (
        <div className="knowledge-dialog-backdrop" role="presentation">
          <section
            aria-modal="true"
            className="knowledge-dialog knowledge-dialog--compact"
            role="dialog"
          >
            <header>
              <h2>删除文档</h2>
              <button
                aria-label="关闭"
                className="icon-button"
                disabled={documentAction === deleteDocumentFor.id}
                onClick={() => {
                  setDeleteDocumentFor(null);
                }}
                type="button"
              >
                <Icon name="close" />
              </button>
            </header>
            <p>确认删除“{deleteDocumentFor.title}”？删除任务会清理私有对象和两类索引。</p>
            <footer>
              <button
                className="knowledge-secondary-button"
                disabled={documentAction === deleteDocumentFor.id}
                onClick={() => {
                  setDeleteDocumentFor(null);
                }}
                type="button"
              >
                取消
              </button>
              <button
                className="knowledge-danger-button"
                disabled={documentAction === deleteDocumentFor.id}
                onClick={() => void confirmDocumentDelete()}
                type="button"
              >
                {documentAction === deleteDocumentFor.id ? "正在受理..." : "删除"}
              </button>
            </footer>
          </section>
        </div>
      )}

      {detailFor === null ? null : (
        <div className="knowledge-dialog-backdrop" role="presentation">
          <section
            aria-labelledby="knowledge-detail-title"
            aria-modal="true"
            className="knowledge-dialog knowledge-dialog--detail"
            role="dialog"
          >
            <header>
              <div>
                <h2 id="knowledge-detail-title">解析详情</h2>
                <small>{detailFor.title}</small>
              </div>
              <button aria-label="关闭" className="icon-button" onClick={closeDetail} type="button">
                <Icon name="close" />
              </button>
            </header>
            {detailLoading ? <p className="knowledge-state">正在加载解析结果...</p> : null}
            {detailError === null ? null : <p className="knowledge-error">{detailError}</p>}
            {detail === null ? null : (
              <>
                <div className="knowledge-detail-summary" aria-label="解析结果统计">
                  <span>
                    <strong>{detail.ingestion_checkpoints.length}</strong> 阶段
                  </span>
                  <span>
                    <strong>{detail.pages.length}</strong> 页
                  </span>
                  <span>
                    <strong>{detail.chunks.length}</strong> 块
                  </span>
                  <span>
                    <strong>{detail.assets.length}</strong> 个资产
                  </span>
                  <span>
                    <strong>{detail.indexes.length}</strong> 条索引
                  </span>
                  <span
                    className={`knowledge-status knowledge-status--${detail.document.latest_version.status}`}
                  >
                    {statusNames[detail.document.latest_version.status] ??
                      detail.document.latest_version.status}
                  </span>
                </div>
                <div className="knowledge-detail-runtime">
                  <span>
                    {detail.document.latest_version.parser_name} / v
                    {detail.document.latest_version.parser_version}
                  </span>
                  <span>
                    {detail.document.latest_version.chunker_name} / v
                    {detail.document.latest_version.chunker_version}
                  </span>
                  <span>
                    {runtimeConfigValue(detail.document.latest_version.embedding_config.provider)} /{" "}
                    {runtimeConfigValue(detail.document.latest_version.embedding_config.model)}
                  </span>
                  <span>
                    {runtimeConfigValue(detail.document.latest_version.index_config.index_version)}
                  </span>
                </div>
                {detail.document.latest_version.error_code === null ? null : (
                  <p className="knowledge-error">{detail.document.latest_version.error_code}</p>
                )}
                <div aria-label="解析详情视图" className="knowledge-detail-tabs" role="tablist">
                  {(
                    [
                      ["stages", "阶段"],
                      ["versions", "版本"],
                      ["indexes", "索引"],
                      ["pages", "页面"],
                      ["chunks", "分块"],
                      ["assets", "资产"],
                    ] as const
                  ).map(([tab, label]) => (
                    <button
                      aria-selected={detailTab === tab}
                      key={tab}
                      onClick={() => {
                        setDetailTab(tab);
                      }}
                      role="tab"
                      type="button"
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div className="knowledge-detail-panel" role="tabpanel">
                  {detailTab === "stages" ? (
                    detail.ingestion_checkpoints.length === 0 ? (
                      <p className="knowledge-detail-empty">尚未完成解析阶段</p>
                    ) : (
                      <ol className="knowledge-stage-list">
                        {detail.ingestion_checkpoints.map((checkpoint) => (
                          <li key={checkpoint.id}>
                            <span className="knowledge-stage-index">
                              {checkpoint.stage_sequence}
                            </span>
                            <div>
                              <strong>{stageNames[checkpoint.stage] ?? checkpoint.stage}</strong>
                              <small>
                                第 {checkpoint.attempt_count} 次尝试 · fencing{" "}
                                {checkpoint.fencing_token}
                              </small>
                            </div>
                            <time dateTime={checkpoint.completed_at}>
                              {new Date(checkpoint.completed_at).toLocaleString("zh-CN")}
                            </time>
                          </li>
                        ))}
                      </ol>
                    )
                  ) : null}
                  {detailTab === "versions" ? (
                    <div className="knowledge-version-list">
                      {detail.versions.map(({ source, version }) => {
                        const active = detail.document.active_version_id === version.id;
                        return (
                          <article key={version.id}>
                            <div>
                              <strong>v{version.version}</strong>
                              <small>{source.original_name}</small>
                            </div>
                            <span
                              className={`knowledge-status knowledge-status--${version.status}`}
                            >
                              {active
                                ? "当前使用"
                                : (statusNames[version.status] ?? version.status)}
                            </span>
                            {canManage && version.status === "ready" && !active ? (
                              <button
                                aria-label={`切换到版本 ${String(version.version)}`}
                                className="icon-button icon-button--small"
                                disabled={documentAction === detail.document.id}
                                onClick={() => void activateVersion(version.id)}
                                title="切换到此版本"
                                type="button"
                              >
                                <Icon name="refresh" />
                              </button>
                            ) : null}
                          </article>
                        );
                      })}
                    </div>
                  ) : null}
                  {detailTab === "indexes" ? (
                    detail.indexes.length === 0 ? (
                      <p className="knowledge-detail-empty">尚未生成索引记录</p>
                    ) : (
                      <div className="knowledge-index-list">
                        {detail.indexes.map((index) => (
                          <article key={index.id}>
                            <div>
                              <strong>{index.kind === "vector" ? "向量" : "关键词"}</strong>
                              <small>{index.external_id}</small>
                            </div>
                            <span className={`knowledge-status knowledge-status--${index.status}`}>
                              {index.status === "succeeded" ? "成功" : "失败"}
                            </span>
                            <small>第 {index.attempt_count} 次尝试</small>
                            {index.error_code === null ? null : <code>{index.error_code}</code>}
                          </article>
                        ))}
                      </div>
                    )
                  ) : null}
                  {detailTab === "pages" ? (
                    detail.pages.length === 0 ? (
                      <p className="knowledge-detail-empty">尚未生成页面记录</p>
                    ) : (
                      <div className="knowledge-page-list">
                        {detail.pages.map((page) => (
                          <article key={page.id}>
                            <header>
                              <strong>第 {page.page_number} 页</strong>
                              <span>{textSourceNames[page.text_source] ?? page.text_source}</span>
                            </header>
                            {page.title_path.length === 0 ? null : (
                              <small>{page.title_path.join(" / ")}</small>
                            )}
                            <p>{page.text}</p>
                          </article>
                        ))}
                      </div>
                    )
                  ) : null}
                  {detailTab === "chunks" ? (
                    detail.chunks.length === 0 ? (
                      <p className="knowledge-detail-empty">尚未生成文本分块</p>
                    ) : (
                      <div className="knowledge-chunk-list">
                        {detail.chunks.map((chunk) => (
                          <article key={chunk.id}>
                            <header>
                              <strong>分块 {chunk.ordinal}</strong>
                              <span>
                                第 {chunk.page_number} 页 · {chunk.token_count} tokens
                              </span>
                            </header>
                            {chunk.title_path.length === 0 ? null : (
                              <small>{chunk.title_path.join(" / ")}</small>
                            )}
                            <p>{chunk.text}</p>
                          </article>
                        ))}
                      </div>
                    )
                  ) : null}
                  {detailTab === "assets" ? (
                    detail.assets.length === 0 ? (
                      <p className="knowledge-detail-empty">暂无图像或表格资产</p>
                    ) : (
                      <div className="knowledge-asset-list">
                        {detail.assets.map((asset) => (
                          <article key={asset.id}>
                            <header>
                              <strong>{asset.kind === "table" ? "表格" : "图像"}</strong>
                              <span>
                                第 {asset.page_number} 页 · 资产 {asset.ordinal}
                              </span>
                            </header>
                            <img
                              alt={`第 ${String(asset.page_number)} 页${asset.kind === "table" ? "表格" : "图像"}预览`}
                              loading="lazy"
                              src={asset.preview_url}
                            />
                            {asset.html === null ? null : <AssetTable html={asset.html} />}
                          </article>
                        ))}
                      </div>
                    )
                  ) : null}
                </div>
              </>
            )}
          </section>
        </div>
      )}

      {eventsFor === null ? null : (
        <div className="knowledge-dialog-backdrop" role="presentation">
          <section aria-modal="true" className="knowledge-dialog" role="dialog">
            <header>
              <div>
                <h2>受理事件</h2>
                <small>{eventsFor.title}</small>
              </div>
              <button
                aria-label="关闭"
                className="icon-button"
                onClick={() => {
                  setEventsFor(null);
                }}
                type="button"
              >
                <Icon name="close" />
              </button>
            </header>
            {eventsLoading ? <p className="knowledge-state">正在加载...</p> : null}
            {eventsError === null ? null : <p className="knowledge-error">{eventsError}</p>}
            <ol className="knowledge-event-list">
              {events.map((event) => (
                <li key={event.id}>
                  <span>{eventNames[event.event_type] ?? event.event_type}</span>
                  <small>{new Date(event.occurred_at).toLocaleString("zh-CN")}</small>
                </li>
              ))}
            </ol>
          </section>
        </div>
      )}
    </section>
  );
}
