import { useCallback, useEffect, useMemo, useState, type SubmitEvent } from "react";

import { publicError } from "../chat/chat-workbench-model";
import { listKnowledgeBases, type KnowledgeBase } from "../knowledge/knowledge-api";
import {
  importSecFiling,
  listSecFilingImports,
  listSecFilings,
  readSecFilingSection,
  searchSecFiling,
  type SecFiling,
  type SecFilingImport,
  type SecFilingSearch,
  type SecFilingSection,
} from "./sec-api";
import "./sec-workbench.css";

interface SecWorkbenchProps {
  readonly canManage: boolean;
  readonly workspaceId: string;
}

const importStatusNames: Readonly<Record<string, string>> = {
  cancelled: "已取消",
  failed: "失败",
  queued: "正在处理",
  ready: "可检索",
};

function localDateTime(value: Date): string {
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(0, 16);
}

function asIso(value: string): string {
  return new Date(value).toISOString();
}

export function SecWorkbench({ canManage, workspaceId }: SecWorkbenchProps) {
  const now = useMemo(() => new Date(), []);
  const [cik, setCik] = useState("0000320193");
  const [forms, setForms] = useState<readonly ("10-K" | "10-Q")[]>(["10-K", "10-Q"]);
  const [periodStart, setPeriodStart] = useState(`${String(now.getFullYear() - 3)}-01-01`);
  const [periodEnd, setPeriodEnd] = useState(now.toISOString().slice(0, 10));
  const [asOf, setAsOf] = useState(localDateTime(now));
  const [filings, setFilings] = useState<SecFiling[]>([]);
  const [selectedAccession, setSelectedAccession] = useState<string | null>(null);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState("");
  const [imports, setImports] = useState<SecFilingImport[]>([]);
  const [query, setQuery] = useState("");
  const [searchResult, setSearchResult] = useState<SecFilingSearch | null>(null);
  const [section, setSection] = useState<SecFilingSection | null>(null);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reloadImports = useCallback(async (): Promise<void> => {
    const values = await listSecFilingImports(workspaceId);
    setImports(values);
  }, [workspaceId]);

  useEffect(() => {
    let active = true;
    void Promise.all([listKnowledgeBases(workspaceId), listSecFilingImports(workspaceId)])
      .then(([bases, imported]) => {
        if (!active) return;
        setKnowledgeBases(bases);
        setKnowledgeBaseId((current) => (current === "" ? (bases[0]?.id ?? "") : current));
        setImports(imported);
      })
      .catch((caught: unknown) => {
        if (active) setError(publicError(caught));
      });
    return () => {
      active = false;
    };
  }, [workspaceId]);

  useEffect(() => {
    if (!imports.some((item) => item.status === "queued")) return;
    const timer = window.setInterval(() => {
      void reloadImports().catch((caught: unknown) => {
        setError(publicError(caught));
      });
    }, 3_000);
    return () => {
      window.clearInterval(timer);
    };
  }, [imports, reloadImports]);

  const selectedFiling = filings.find((item) => item.accession === selectedAccession) ?? null;
  const selectedImport =
    imports.find(
      (item) => item.accession === selectedAccession && item.knowledge_base_id === knowledgeBaseId,
    ) ?? null;

  async function submitFilingSearch(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (forms.length === 0) return;
    setLoading(true);
    setError(null);
    setSearchResult(null);
    setSection(null);
    try {
      const values = await listSecFilings(workspaceId, {
        asOf: asIso(asOf),
        cik,
        forms,
        reportPeriodEnd: periodEnd,
        reportPeriodStart: periodStart,
      });
      setFilings(values);
      setSelectedAccession(values[0]?.accession ?? null);
    } catch (caught: unknown) {
      setError(publicError(caught));
    } finally {
      setLoading(false);
    }
  }

  async function submitImport(): Promise<void> {
    if (selectedAccession === null || knowledgeBaseId === "") return;
    setImporting(true);
    setError(null);
    try {
      await importSecFiling(workspaceId, selectedAccession, knowledgeBaseId, asIso(asOf));
      await reloadImports();
    } catch (caught: unknown) {
      setError(publicError(caught));
    } finally {
      setImporting(false);
    }
  }

  async function submitContentSearch(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (selectedImport?.status !== "ready" || !query.trim()) return;
    setSearching(true);
    setError(null);
    setSection(null);
    try {
      setSearchResult(
        await searchSecFiling(
          workspaceId,
          selectedImport.accession,
          selectedImport.knowledge_base_id,
          asIso(asOf),
          query.trim(),
        ),
      );
    } catch (caught: unknown) {
      setError(publicError(caught));
    } finally {
      setSearching(false);
    }
  }

  async function openSection(chunkId: string, documentVersionId: string): Promise<void> {
    if (selectedImport === null) return;
    setError(null);
    try {
      setSection(
        await readSecFilingSection(
          workspaceId,
          selectedImport.accession,
          selectedImport.knowledge_base_id,
          asIso(asOf),
          documentVersionId,
          chunkId,
        ),
      );
    } catch (caught: unknown) {
      setError(publicError(caught));
    }
  }

  function toggleForm(form: "10-K" | "10-Q"): void {
    setForms((current) =>
      current.includes(form) ? current.filter((item) => item !== form) : [...current, form],
    );
  }

  return (
    <section className="sec-workbench" aria-labelledby="sec-workbench-title">
      <header className="sec-workbench__header">
        <div>
          <p className="eyebrow">SEC Filing Pipeline</p>
          <h1 id="sec-workbench-title">SEC 申报审查</h1>
        </div>
        <span className="sec-workbench__profile">Dense v1</span>
      </header>

      {error === null ? null : (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      <div className="sec-workbench__layout">
        <aside className="sec-workbench__scope">
          <form onSubmit={(event) => void submitFilingSearch(event)}>
            <h2>申报范围</h2>
            <label>
              CIK
              <input
                inputMode="numeric"
                maxLength={10}
                minLength={1}
                onChange={(event) => {
                  setCik(event.currentTarget.value);
                }}
                pattern="[0-9]{1,10}"
                required
                value={cik}
              />
            </label>
            <fieldset>
              <legend>Form</legend>
              {(["10-K", "10-Q"] as const).map((form) => (
                <label key={form}>
                  <input
                    checked={forms.includes(form)}
                    onChange={() => {
                      toggleForm(form);
                    }}
                    type="checkbox"
                  />
                  {form}
                </label>
              ))}
            </fieldset>
            <label>
              报告期开始
              <input
                onChange={(event) => {
                  setPeriodStart(event.currentTarget.value);
                }}
                required
                type="date"
                value={periodStart}
              />
            </label>
            <label>
              报告期结束
              <input
                onChange={(event) => {
                  setPeriodEnd(event.currentTarget.value);
                }}
                required
                type="date"
                value={periodEnd}
              />
            </label>
            <label>
              截止时间
              <input
                onChange={(event) => {
                  setAsOf(event.currentTarget.value);
                }}
                required
                type="datetime-local"
                value={asOf}
              />
            </label>
            <button
              className="primary-button"
              disabled={loading || forms.length === 0}
              type="submit"
            >
              {loading ? "正在查询…" : "查询申报"}
            </button>
          </form>

          <div className="sec-workbench__filings" aria-label="申报列表">
            <div className="sec-workbench__pane-title">
              <h2>Accession</h2>
              <span>{filings.length}</span>
            </div>
            {filings.length === 0 ? (
              <p className="sec-workbench__empty">暂无匹配申报</p>
            ) : (
              filings.map((filing) => (
                <button
                  aria-pressed={selectedAccession === filing.accession}
                  className="sec-filing-row"
                  key={filing.accession}
                  onClick={() => {
                    setSelectedAccession(filing.accession);
                    setSearchResult(null);
                    setSection(null);
                  }}
                  type="button"
                >
                  <strong>{filing.form}</strong>
                  <span>{filing.accession}</span>
                  <small>{filing.report_date}</small>
                </button>
              ))
            )}
          </div>
        </aside>

        <div className="sec-workbench__main">
          <section className="sec-workbench__import-band">
            <div>
              <p className="eyebrow">Locked snapshot</p>
              <h2>{selectedFiling?.accession ?? "选择一个 Accession"}</h2>
              <p>
                {selectedFiling === null
                  ? ""
                  : `${selectedFiling.form} · ${selectedFiling.report_date}`}
              </p>
            </div>
            <label>
              Knowledge Base
              <select
                onChange={(event) => {
                  setKnowledgeBaseId(event.currentTarget.value);
                }}
                value={knowledgeBaseId}
              >
                {knowledgeBases.map((base) => (
                  <option key={base.id} value={base.id}>
                    {base.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="sec-workbench__import-action">
              <span
                className={`sec-import-status sec-import-status--${selectedImport?.status ?? "none"}`}
              >
                {selectedImport === null
                  ? "未导入"
                  : (importStatusNames[selectedImport.status] ?? selectedImport.status)}
              </span>
              <button
                className="primary-button"
                disabled={
                  !canManage ||
                  importing ||
                  selectedFiling === null ||
                  knowledgeBaseId === "" ||
                  selectedImport !== null
                }
                onClick={() => void submitImport()}
                type="button"
              >
                {importing ? "正在锁定…" : "锁定并导入"}
              </button>
            </div>
          </section>

          <section className="sec-workbench__content">
            <form
              className="sec-content-search"
              onSubmit={(event) => void submitContentSearch(event)}
            >
              <label htmlFor="sec-content-query">申报内容检索</label>
              <div>
                <input
                  id="sec-content-query"
                  maxLength={2_000}
                  onChange={(event) => {
                    setQuery(event.currentTarget.value);
                  }}
                  placeholder="收入变化、风险因素、现金流…"
                  value={query}
                />
                <button
                  className="primary-button"
                  disabled={selectedImport?.status !== "ready" || searching || !query.trim()}
                  type="submit"
                >
                  {searching ? "检索中…" : "Dense 检索"}
                </button>
              </div>
            </form>

            <div className="sec-workbench__results">
              <div className="sec-hit-list" aria-label="检索结果">
                <div className="sec-workbench__pane-title">
                  <h2>Chunks</h2>
                  <span>{searchResult?.hits.length ?? 0}</span>
                </div>
                {searchResult?.status === "not_ready" ? (
                  <p className="sec-workbench__empty">文档仍在建立索引</p>
                ) : searchResult?.hits.length ? (
                  searchResult.hits.map((hit) => (
                    <button
                      className="sec-hit-row"
                      key={hit.chunk_id}
                      onClick={() => void openSection(hit.chunk_id, hit.document_version_id)}
                      type="button"
                    >
                      <span>{hit.section}</span>
                      <strong>{hit.excerpt}</strong>
                      <small>
                        相似度 {hit.score.toFixed(3)} · p.{hit.page_number}
                      </small>
                    </button>
                  ))
                ) : (
                  <p className="sec-workbench__empty">暂无检索结果</p>
                )}
              </div>

              <article className="sec-section-reader" aria-live="polite">
                <div className="sec-workbench__pane-title">
                  <h2>{section?.section ?? "原文定位"}</h2>
                  <span>{section === null ? "" : `p.${String(section.page_number)}`}</span>
                </div>
                {section === null ? (
                  <p className="sec-workbench__empty">选择一个 Chunk 查看锁定快照原文</p>
                ) : (
                  <>
                    <pre>{section.text}</pre>
                    <footer>
                      <span>Snapshot {section.snapshot_id.slice(0, 8)}</span>
                      <span>Source {section.source_content_sha256.slice(0, 12)}</span>
                    </footer>
                  </>
                )}
              </article>
            </div>
          </section>
        </div>
      </div>
    </section>
  );
}
