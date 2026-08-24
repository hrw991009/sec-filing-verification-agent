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
  createKnowledgeBase,
  deleteKnowledgeBase,
  listKnowledgeBases,
  listKnowledgeDocuments,
  listKnowledgeIngestionEvents,
  updateKnowledgeBase,
  uploadKnowledgeDocument,
  type KnowledgeBase,
  type KnowledgeDocument,
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
  queued: "排队中",
  ready: "可用",
  retrying: "等待重试",
  validating: "正在校验",
  vector_indexing: "正在建立向量索引",
};

const eventNames: Readonly<Record<string, string>> = {
  created: "任务已受理",
};

function titleFromFile(file: File): string {
  const dot = file.name.lastIndexOf(".");
  return (dot > 0 ? file.name.slice(0, dot) : file.name).trim();
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
  const documentRequestRef = useRef(0);

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
                          <button
                            aria-label={`查看 ${document.title} 的受理事件`}
                            className="icon-button icon-button--small"
                            onClick={() => void openEvents(document)}
                            title="查看受理事件"
                            type="button"
                          >
                            <Icon name="bolt" />
                          </button>
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
