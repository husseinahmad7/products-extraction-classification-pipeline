import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Api, pretty } from "./api";
import type { Page, RecordRow, Resource } from "./api";

const errorText = (error: unknown) => error instanceof Error ? error.message : "Request failed. Refresh and retry.";
type Tab = "labels" | "datasets" | "models";
type TaxonomyNode = { id: string; label: string; parent_id?: string | null };
function nodes(row: Resource | undefined): TaxonomyNode[] {
  const values = row?.data.nodes;
  if (!Array.isArray(values)) return [];
  return values.filter((node): node is TaxonomyNode => !!node && typeof node === "object" && typeof node.id === "string" && typeof node.label === "string");
}

export function LabelReview({ api, saved }: { api: Api; saved: () => void }) {
  const [taxonomies, setTaxonomies] = useState<Resource[]>([]), [revisions, setRevisions] = useState<Resource[]>([]);
  const [taxonomyCursor, setTaxonomyCursor] = useState<string | null>(null), [revisionCursor, setRevisionCursor] = useState<string | null>(null), [loadingMore, setLoadingMore] = useState("");
  const [taxonomyId, setTaxonomyId] = useState(""), [revisionId, setRevisionId] = useState("");
  const [records, setRecords] = useState<{ items: RecordRow[]; total: number } | null>(null), [recordId, setRecordId] = useState("");
  const [offset, setOffset] = useState(0), [labelId, setLabelId] = useState(""), [provenance, setProvenance] = useState("human");
  const [reason, setReason] = useState(""), [error, setError] = useState(""), [recordError, setRecordError] = useState("");
  const [message, setMessage] = useState(""), [busy, setBusy] = useState(false), [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    void Promise.all([api.get<Page<Resource>>("/v1/taxonomies?limit=100"), api.get<Page<Resource>>("/v1/revisions?limit=100")])
      .then(([taxonomies, revisions]) => { if (alive) { setTaxonomies(taxonomies.items); setRevisions(revisions.items); setTaxonomyCursor(taxonomies.next_cursor ? String(taxonomies.next_cursor) : null); setRevisionCursor(revisions.next_cursor ? String(revisions.next_cursor) : null); } })
      .catch(error => { if (alive) setError(errorText(error)); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [api]);
  const revision = revisions.find(row => row.id === revisionId), runId = revision?.data.run_id;
  useEffect(() => {
    let alive = true; setRecords(null); setRecordId(""); setRecordError(""); setLabelId(""); setReason("");
    if (typeof runId === "string") {
      void api.get<{ items: RecordRow[]; total: number }>(`/v1/runs/${encodeURIComponent(runId)}/records?offset=${offset}&limit=20`)
        .then(value => { if (alive) setRecords(value); }).catch(error => { if (alive) setRecordError(errorText(error)); });
    }
    return () => { alive = false; };
  }, [api, runId, offset]);
  const taxonomy = nodes(taxonomies.find(row => row.id === taxonomyId));
  const parents = new Set(taxonomy.map(node => node.parent_id));
  const leaves = taxonomy.filter(node => !parents.has(node.id));
  const record = records?.items.find(row => row.id === recordId);

  async function loadMore(collection: "taxonomies" | "revisions") {
    const cursor = collection === "taxonomies" ? taxonomyCursor : revisionCursor;
    if (!cursor || loadingMore) return;
    setLoadingMore(collection); setError("");
    try {
      const page = await api.get<Page<Resource>>(`/v1/${collection}?limit=100&cursor=${encodeURIComponent(cursor)}`);
      const append = (existing: Resource[]) => [...new Map([...existing, ...page.items].map(row => [row.id, row])).values()];
      if (collection === "taxonomies") { setTaxonomies(append); setTaxonomyCursor(page.next_cursor ? String(page.next_cursor) : null); }
      else { setRevisions(append); setRevisionCursor(page.next_cursor ? String(page.next_cursor) : null); }
    } catch (error) { setError(errorText(error)); }
    finally { setLoadingMore(""); }
  }

  async function submit(event: FormEvent) {
    event.preventDefault(); if (!record || !labelId) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await api.post("/v1/labels", { taxonomy_id: taxonomyId, record_id: recordId, revision_id: revisionId, label_id: labelId === "__abstain__" ? null : labelId, provenance, reason: reason.trim() });
      setReason(""); setLabelId(""); saved();
      setMessage("Review saved as an immutable correction. Earlier decisions remain in the audit trail; this does not activate a model.");
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }

  return <form className="resource-panel" onSubmit={submit}><h2>Review a record label</h2><p>Choose a published record, inspect its evidence, then record a category or an explicit abstention. Human and AI provenance remain separate.</p>
    {loading && <p role="status">Loading taxonomies and published datasets…</p>}
    {!loading && (!taxonomies.length || !revisions.length) && <p className="notice warning">Create a taxonomy and publish an extraction revision before reviewing labels.</p>}
    <label htmlFor="review-taxonomy">Taxonomy</label><select id="review-taxonomy" value={taxonomyId} onChange={event => { setTaxonomyId(event.target.value); setLabelId(""); setReason(""); }} required><option value="">Choose a saved taxonomy</option>{taxonomies.map(row => <option key={row.id} value={row.id}>{String(row.data.name || row.id)}</option>)}</select>
    {taxonomyCursor && <button className="auth-secondary" type="button" disabled={!!loadingMore} onClick={() => void loadMore("taxonomies")}>{loadingMore === "taxonomies" ? "Loading…" : "Load more taxonomies"}</button>}
    <label htmlFor="review-revision">Published revision</label><select id="review-revision" value={revisionId} onChange={event => { setRevisionId(event.target.value); setOffset(0); }} required><option value="">Choose a revision</option>{revisions.map(row => <option key={row.id} value={row.id}>{String(row.data.source_id || "Dataset")} / {row.id}</option>)}</select>
    {revisionCursor && <button className="auth-secondary" type="button" disabled={!!loadingMore} onClick={() => void loadMore("revisions")}>{loadingMore === "revisions" ? "Loading…" : "Load older revisions"}</button>}
    {revisionId && !records && !recordError && <p role="status">Loading retained records…</p>}
    {recordError && <div className="notice error" role="alert">{recordError}</div>}
    {records && <><label htmlFor="review-record">Retained record</label><select id="review-record" value={recordId} onChange={event => { setRecordId(event.target.value); setLabelId(""); setReason(""); }} required><option value="">Choose a record on this page</option>{records.items.filter(row => row.id).map(row => <option key={row.id} value={row.id!}>{String(row.data.name || row.data.title || row.id)}</option>)}</select>
      <div className="pagination"><button type="button" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous record page</button><span>{records.total} retained records</span><button type="button" disabled={offset + 20 >= records.total} onClick={() => setOffset(offset + 20)}>Next record page</button></div></>}
    {record && <section className="reviewed-record" aria-label="Label review evidence"><h3>Evidence for this decision</h3><p><code>{recordId}</code></p><details open><summary>Extracted values</summary><pre>{pretty(record.data)}</pre></details><details><summary>Field-level evidence and captured hashes</summary><pre>{pretty(record.evidence)}</pre></details></section>}
    <label htmlFor="review-label">Reviewed category</label><select id="review-label" value={labelId} onChange={event => setLabelId(event.target.value)} required disabled={!taxonomyId}><option value="">Choose a category or abstain</option><option value="__abstain__">Abstain — evidence does not support a leaf category</option>{leaves.map(node => <option key={node.id} value={node.id}>{node.label} ({node.id})</option>)}</select>
    <label htmlFor="label-provenance">Review provenance</label><select id="label-provenance" value={provenance} onChange={event => setProvenance(event.target.value)}><option value="human">Human — I independently checked the evidence</option><option value="ai">AI — assisted or generated without independent human review</option></select><p className="hint">Only select Human if you personally reviewed the source evidence. AI reviews are never counted as independent certification labels.</p>
    <label htmlFor="label-reason">Evidence and reason</label><textarea id="label-reason" value={reason} onChange={event => setReason(event.target.value)} rows={3} minLength={5} maxLength={2000} required placeholder="Which source facts support this category or abstention?" />
    {error && <div className="notice error" role="alert">{error}</div>}{message && <p className="notice success" role="status">{message}</p>}
    <button className="primary" disabled={busy || !taxonomyId || !record || !labelId || reason.trim().length < 5}>{busy ? "Saving…" : "Save reviewed label"}</button>
  </form>;
}

function SnapshotForm({ api, saved }: { api: Api; saved: () => void }) {
  const [taxonomyId, setTaxonomyId] = useState(""), [provenance, setProvenance] = useState("human");
  const [error, setError] = useState(""), [message, setMessage] = useState(""), [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setMessage("");
    try {
      const snapshot = await api.post<Resource>("/v1/datasets/snapshots", { taxonomy_id: taxonomyId.trim(), provenance });
      setMessage(`Snapshot ${snapshot.id} frozen. Export contains reviewed labels and provenance, not model weights.`); saved();
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }
  return <form className="resource-panel" onSubmit={submit}><h2>Freeze a training dataset</h2><p>Snapshot the current audited corrections for a saved taxonomy. This is a reproducible training export, not a claim of independent evaluation.</p>
    <label htmlFor="snapshot-taxonomy">Saved taxonomy ID</label><input id="snapshot-taxonomy" value={taxonomyId} onChange={event => setTaxonomyId(event.target.value)} required maxLength={128} />
    <label htmlFor="snapshot-provenance">Included review provenance</label><select id="snapshot-provenance" value={provenance} onChange={event => setProvenance(event.target.value)}><option value="human">Human only</option><option value="ai">AI only — exploratory</option><option value="all">Both — provenance preserved</option></select>
    {error && <div className="notice error" role="alert">{error}</div>}{message && <p className="notice success" role="status">{message}</p>}
    <button className="primary" disabled={busy || !taxonomyId.trim()}>{busy ? "Freezing…" : "Freeze dataset snapshot"}</button>
  </form>;
}

const modelFields = [
  { key: "id", label: "Unique model version ID", maxLength: 128, pattern: "[a-zA-Z0-9_-]+" },
  { key: "taxonomy_id", label: "Saved taxonomy ID", maxLength: 128 },
  { key: "dataset_id", label: "Frozen training snapshot ID", maxLength: 128 },
  { key: "checkpoint_revision", label: "Immutable checkpoint revision", maxLength: 128 },
  { key: "artifact_uri", label: "Remote artifact HTTPS URL", maxLength: 2000, type: "url" },
  { key: "artifact_hash", label: "Artifact SHA-256", maxLength: 64, pattern: "[a-f0-9]{64}" },
  { key: "calibration_hash", label: "Calibration report SHA-256", maxLength: 64, pattern: "[a-f0-9]{64}" },
  { key: "evaluation_hash", label: "Evaluation report SHA-256", maxLength: 64, pattern: "[a-f0-9]{64}" },
  { key: "license", label: "Model license", maxLength: 128 },
] as const;

function ModelRegistrationForm({ api, saved }: { api: Api; saved: () => void }) {
  const [fields, setFields] = useState<Record<string, string>>({}), [provider, setProvider] = useState("tfidf");
  const [error, setError] = useState(""), [message, setMessage] = useState(""), [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(""); setMessage("");
    try {
      const url = new URL(fields.artifact_uri?.trim());
      if (url.protocol !== "https:" || url.username || url.password) throw new Error("Use a remote HTTPS artifact reference without embedded credentials.");
      setBusy(true);
      const model = await api.post<Resource>("/v1/models", { ...Object.fromEntries(modelFields.map(field => [field.key, fields[field.key]?.trim()])), provider });
      setFields({}); saved(); setMessage(`Model ${model.id} metadata registered in this workspace. No weights were downloaded or activated; certification and promotion remain blocked.`);
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }
  return <form className="resource-panel" onSubmit={submit}><h2>Register a remote model version</h2><p>Bind a remote checkpoint to your immutable training snapshot and evaluation reports. This stores metadata only; training and weights remain remote.</p>
    <label htmlFor="model-provider">Classifier family</label><select id="model-provider" value={provider} onChange={event => setProvider(event.target.value)}><option value="tfidf">Trained TF-IDF</option><option value="setfit">SetFit</option><option value="gliclass">GLiClass</option><option value="laya">Laya</option></select>
    {modelFields.map(field => <div key={field.key}><label htmlFor={`model-${field.key}`}>{field.label}</label><input id={`model-${field.key}`} value={fields[field.key] || ""} onChange={event => setFields(previous => ({ ...previous, [field.key]: event.target.value }))} required maxLength={field.maxLength} pattern={"pattern" in field ? field.pattern : undefined} type={"type" in field ? field.type : "text"} autoCapitalize="none" spellCheck={false} /></div>)}
    <p className="hint">Use content hashes from the remote experiment. Registration does not prove a report is independent or authorize automatic acceptance.</p>
    {error && <div className="notice error" role="alert">{error}</div>}{message && <p className="notice success" role="status">{message}</p>}
    <button className="primary" disabled={busy || modelFields.some(field => !fields[field.key]?.trim())}>{busy ? "Registering…" : "Register model metadata"}</button>
  </form>;
}

export function LearningView({ api, epoch }: { api: Api; epoch: number }) {
  const [tab, setTab] = useState<Tab>("labels"), [page, setPage] = useState<Page<Resource> | null>(null);
  const [cursor, setCursor] = useState(""), [reload, setReload] = useState(0), [error, setError] = useState("");
  const [exporting, setExporting] = useState("");
  const refresh = () => setReload(value => value + 1);
  useEffect(() => {
    let alive = true; setPage(null); setError("");
    void api.get<Page<Resource>>(`/v1/${tab}?limit=25&cursor=${encodeURIComponent(cursor)}`)
      .then(value => { if (alive) setPage(value); }).catch(error => { if (alive) setError(errorText(error)); });
    return () => { alive = false; };
  }, [api, tab, cursor, epoch, reload]);
  async function exportSnapshot(id: string) {
    setExporting(id); setError("");
    try { await api.download(`/v1/datasets/${encodeURIComponent(id)}/export`, `${id}-training.json`); }
    catch (error) { setError(errorText(error)); }
    finally { setExporting(""); }
  }
  return <>
    <header className="page-heading"><div><h1>Learning & models</h1><p>Turn evidence reviews into versioned training data. Models remain reviewed suggestions; automatic acceptance and promotion stay blocked.</p></div></header>
    <nav className="tabs" aria-label="Learning sections">{([{ key: "labels", label: "Reviewed labels" }, { key: "datasets", label: "Training snapshots" }, { key: "models", label: "Model registry" }] as const).map(item => <button key={item.key} aria-current={tab === item.key ? "page" : undefined} onClick={() => { setTab(item.key); setCursor(""); }}>{item.label}</button>)}</nav>
    {error && <div className="notice error" role="alert">{error} <button onClick={refresh}>Retry learning data</button></div>}
    <div className="learning-stack">
      {tab === "labels" && <LabelReview api={api} saved={refresh} />}
      {tab === "datasets" && <SnapshotForm api={api} saved={refresh} />}
      {tab === "models" && <ModelRegistrationForm api={api} saved={refresh} />}
      <section className="resource-panel" aria-labelledby="learning-list-heading"><h2 id="learning-list-heading">{tab === "labels" ? "Correction history" : tab === "datasets" ? "Immutable snapshots" : "Registered model versions"}</h2>
        {!page && !error && <p role="status">Loading {tab}…</p>}
        {page && !page.items.length && <p>{tab === "models" ? "No model versions registered. Kaggle experiments do not automatically activate a classifier." : `No ${tab === "labels" ? "reviewed labels" : "training snapshots"} on this page.`}</p>}
        {page?.items.map(row => <article key={row.id} className="learning-item"><div className="section-heading"><h3>{String(row.data.name || row.data.record_id || row.data.taxonomy_id || row.id)}</h3><span className="badge">{String(row.data.provenance || row.data.state || (tab === "models" ? "suggestions only" : "immutable"))}</span></div><p className="hint"><code>{row.id}</code> · version {row.version}</p>
          {tab === "labels" && <dl><dt>Reviewed category</dt><dd>{row.data.label_id == null ? "Abstain" : String(row.data.label_id)}</dd><dt>Reason</dt><dd>{String(row.data.reason || "")}</dd><dt>Taxonomy / revision</dt><dd>{String(row.data.taxonomy_id)} / {String(row.data.revision_id)}</dd></dl>}
          <details><summary>{tab === "models" ? "Checkpoint, dataset, calibration and evaluation bindings" : "Saved provenance and metadata"}</summary><pre>{pretty(row.data)}</pre></details>
          {tab === "datasets" && <button disabled={!!exporting} onClick={() => void exportSnapshot(row.id)}>{exporting === row.id ? "Exporting…" : "Export training JSON"}</button>}
        </article>)}
        <div className="pagination"><button disabled={!cursor || !page} onClick={() => setCursor("")}>First page</button><span>{page?.items.length || 0} items on this page</span><button disabled={!page?.next_cursor} onClick={() => setCursor(String(page?.next_cursor))}>Next page</button></div>
      </section>
    </div>
  </>;
}
