import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Activity, ArrowDownToLine, CalendarClock, ChevronRight, Database, FileCheck2, FolderInput, HardDrive, History, ListChecks, LogOut, RefreshCw, ShieldCheck, X } from "lucide-react";
import { Api, pretty } from "./api";
import type { Evidence, Operation, Page, RecordRow, Resource } from "./api";
import { WorkspaceSettings } from "./WorkspaceSettings";
import { OperationsView } from "./OperationsView";
import { SchedulesView } from "./SchedulesView";
import { parseDatasetDiff, ReviewSummary, ValidationSummary } from "./ReviewSummary";

type Collection = "sources" | "schemas" | "taxonomies" | "drafts" | "recipes" | "runs" | "reviews" | "revisions";
type Route = Collection | "audit" | "operations" | "schedules" | "settings";
const labels: Record<Collection, string> = { sources: "Sources", schemas: "Schemas", taxonomies: "Taxonomies", drafts: "Drafts", recipes: "Recipes", runs: "Runs", reviews: "Review queue", revisions: "Datasets" };
const descriptions: Record<Collection, string> = {
  sources: "Define where records come from. Each source stays inside its allowed domains.",
  schemas: "Describe the records you need using a versioned JSON Schema.",
  taxonomies: "Define your own category tree. Uncalibrated classification always abstains.",
  drafts: "Inspect proposed mappings before creating a validated recipe.",
  recipes: "Recipes connect captured evidence to your schema. Validation and approval come before execution.",
  runs: "Follow extraction, inspect evidence and replay a captured result.",
  reviews: "Dataset removals pause publication until an operator makes a recorded decision.",
  revisions: "Published, immutable dataset revisions. Export includes field-level evidence.",
};
const errorText = (error: unknown) => error instanceof Error ? error.message : "The request failed. Retry after checking the API.";
const stateOf = (row: Resource) => String(row.data.state || (row.kind === "revision" ? "published" : "configured"));
const displayName = (row: Resource) => String(row.data.name || row.data.source_id || row.id);
const stateLabel = (value: string) => value.replaceAll("_", " ");
const isOpen = (value: string) => ["queued", "running"].includes(value);

function Badge({ state }: { state: string }) { return <span className={`badge badge-${state}`}>{stateLabel(state)}</span>; }
function ErrorNotice({ children }: { children: ReactNode }) { return <div className="notice error" role="alert">{children}</div>; }
function Json({ value, title = "Configuration" }: { value: unknown; title?: string }) { return <details className="json-details"><summary>{title}</summary><pre>{pretty(value)}</pre></details>; }

function Connect({ connect }: { connect: (api: Api) => void }) {
  const [token, setToken] = useState(""), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    const api = new Api(token.trim());
    try { await api.get("/v1/sources?limit=1"); setToken(""); connect(api); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }
  return <main className="connect">
    <div className="brand"><FileCheck2 size={26} aria-hidden="true" /> Evidence Pipeline</div>
    <h1>From source to evidence.</h1>
    <p>Configure extraction, inspect every field, and keep a record of each decision.</p>
    <form onSubmit={submit}>
      <h2>Open your workspace</h2>
      <label htmlFor="admin-token">Administrator token</label>
      <input id="admin-token" type="password" autoComplete="off" spellCheck={false} required value={token} onChange={e => setToken(e.target.value)} />
      <p className="hint">Kept in memory only. Refreshing or disconnecting clears this session.</p>
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <button className="primary" disabled={busy || !token.trim()}>{busy ? "Connecting…" : "Connect to workspace"}<ChevronRight size={17} aria-hidden="true" /></button>
    </form>
    <details><summary>First time here?</summary><p>Start the API and worker, then run <code>product-pipeline admin-bootstrap</code> in your local terminal. Paste the token above. This dashboard connects to the same-origin API.</p></details>
    <p className="release-note">Development alpha · single workspace · no model or API key required for offline extraction.</p>
  </main>;
}

function OperationStatus({ api, operation, refreshed, clear, openStorage }: { api: Api; operation: Operation; refreshed: () => void; clear: () => void; openStorage: () => void }) {
  const [current, setCurrent] = useState(operation), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!isOpen(current.state)) return;
    let alive = true;
    const poll = async () => {
      try {
        const next = await api.get<Operation>(`/v1/operations/${operation.id}`);
        if (!alive) return;
        setCurrent(next); setError("");
        if (!isOpen(next.state)) { clearInterval(timer); refreshed(); }
      } catch (error) { if (alive) setError(errorText(error)); }
    };
    const timer = setInterval(() => void poll(), 1800); void poll();
    return () => { alive = false; clearInterval(timer); };
  }, [api, operation.id, refreshed, current.state]);
  async function change(action: "cancel" | "resume") {
    setBusy(true); setError("");
    try {
      const next = await api.post<Operation>(`/v1/operations/${current.id}/${action}`);
      setCurrent({ ...current, ...next, error: action === "resume" ? undefined : current.error });
      refreshed();
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }
  return <section className="operation" aria-label="Latest operation" aria-live="polite">
    <div><Badge state={current.state} /> <span>Operation <code>{current.id}</code></span>{current.result && <span> → {current.result.kind} <code>{current.result.id}</code></span>}</div>
    {current.error && <p>{current.error.detail}</p>}{error && <ErrorNotice>{error}</ErrorNotice>}
    <div className="actions">{current.state === "paused" && <><button onClick={openStorage}>Storage settings</button><button className="primary" disabled={busy} onClick={() => void change("resume")}>{busy ? "Resuming…" : "Resume operation"}</button></>}
      {(isOpen(current.state) || current.state === "paused") && <button disabled={busy} onClick={() => void change("cancel")}>Cancel operation</button>}
      {!isOpen(current.state) && current.state !== "paused" && <button onClick={clear} aria-label="Dismiss operation"><X size={16} /></button>}</div>
  </section>;
}

const schemaTemplate = { id: "catalog-v1", definition: { type: "object", properties: { sku: { type: "string" }, name: { type: "string" }, description: { type: "string" } }, required: ["sku", "name"], additionalProperties: false } };
const taxonomyTemplate = { id: "categories-v1", nodes: [{ id: "product", label: "Products" }, { id: "tools", label: "Tools", parent_id: "product", aliases: ["drill", "saw"] }] };

function Creator({ collection, api, saved, close }: { collection: Collection; api: Api; saved: () => void; close: () => void }) {
  const [name, setName] = useState(""), [id, setId] = useState(""), [url, setUrl] = useState(""), [mode, setMode] = useState("html");
  const [navigationKind, setNavigationKind] = useState(""), [navigationValue, setNavigationValue] = useState(""), [navigationAttribute, setNavigationAttribute] = useState("href");
  const [authRef, setAuthRef] = useState(""), [minInterval, setMinInterval] = useState("1");
  const [document, setDocument] = useState(pretty(collection === "schemas" ? schemaTemplate : taxonomyTemplate));
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const body = collection === "sources" ? {
        id, name, url, mode, allowed_domains: [new URL(url).hostname],
        min_interval_seconds: Number(minInterval),
        ...(authRef.trim() ? { auth_ref: authRef.trim() } : {}),
        ...(navigationKind ? { navigation: { kind: navigationKind, value: navigationValue.trim(), ...(navigationKind.startsWith("html") ? { attribute: navigationAttribute.trim() || "href" } : {}) } } : {}),
      } : JSON.parse(document);
      await api.post(`/v1/${collection}`, body); saved(); close();
    } catch (error) { setError(errorText(error)); } finally { setBusy(false); }
  }
  return <form className="editor" onSubmit={submit}>
    <div className="section-heading"><h2>Add {collection === "sources" ? "source" : collection === "schemas" ? "schema" : "taxonomy"}</h2><button type="button" onClick={close} aria-label="Close editor"><X size={18} /></button></div>
    {collection === "sources" ? <>
      <label htmlFor="source-name">Display name</label><input id="source-name" value={name} onChange={e => setName(e.target.value)} required maxLength={200} />
      <label htmlFor="source-id">Stable source ID</label><input id="source-id" value={id} onChange={e => setId(e.target.value)} pattern={"[a-zA-Z0-9_\\-]+"} required maxLength={128} /><p className="hint">Use letters, numbers, hyphens or underscores. This becomes part of record identity.</p>
      <label htmlFor="source-url">Source URL</label><input id="source-url" type="url" value={url} onChange={e => setUrl(e.target.value)} required placeholder="https://example.org/catalog" />
      <label htmlFor="source-mode">Source type</label><select id="source-mode" value={mode} onChange={e => { setMode(e.target.value); setNavigationKind(""); }}><option value="html">Static HTML</option><option value="json">JSON API</option><option value="browser">Rendered HTML — captured artifacts only</option><option value="pdf">PDF blocks — captured artifacts only</option></select>
      <p className="hint">The URL's exact hostname is allowlisted. Rendered HTML and PDF currently require a retained canonical capture.</p>
      {(mode === "html" || mode === "json") && <>
        <label htmlFor="source-navigation">Page navigation (optional)</label><select id="source-navigation" value={navigationKind} onChange={e => setNavigationKind(e.target.value)}><option value="">Single page</option>{mode === "html" ? <><option value="html_next">Follow next link</option><option value="html_links">Follow page links</option></> : <><option value="json_next">Follow next pointer</option><option value="json_links">Follow link pointers</option></>}</select>
        {navigationKind && <><label htmlFor="navigation-value">{mode === "html" ? "Link CSS selector" : "Link JSON Pointer"}</label><input id="navigation-value" value={navigationValue} onChange={e => setNavigationValue(e.target.value)} required placeholder={mode === "html" ? "a.next" : "/next"} />{mode === "html" && <><label htmlFor="navigation-attribute">Link attribute</label><input id="navigation-attribute" value={navigationAttribute} onChange={e => setNavigationAttribute(e.target.value)} required /></>}</>}
      </>}
      <label htmlFor="source-interval">Minimum interval between requests (seconds)</label><input id="source-interval" type="number" min="0.1" max="3600" step="0.1" value={minInterval} onChange={e => setMinInterval(e.target.value)} required />
      <label htmlFor="source-auth-ref">Credential reference (optional)</label><input id="source-auth-ref" value={authRef} onChange={e => setAuthRef(e.target.value)} pattern="env:[A-Z][A-Z0-9_]*" maxLength={132} placeholder="env:CATALOG_TOKEN" autoComplete="off" spellCheck={false} /><p className="hint">Name a server environment variable. Never paste a token or secret here; authenticated sources require HTTPS.</p>
    </> : <><label htmlFor="new-document">{collection === "schemas" ? "Target schema (JSON)" : "Taxonomy (JSON)"}</label><textarea id="new-document" className="code-input" rows={17} value={document} onChange={e => setDocument(e.target.value)} required spellCheck={false} /></>}
    {error && <ErrorNotice>{error}</ErrorNotice>}
    <button className="primary" disabled={busy}>{busy ? "Saving…" : "Save configuration"}</button>
  </form>;
}

function CompileForm({ row, api, queued }: { row: Resource; api: Api; queued: (op: Operation) => void }) {
  const [targetId, setTargetId] = useState(""), [identity, setIdentity] = useState("/sku");
  const [snapshot, setSnapshot] = useState(""), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const artifact = await api.upload(snapshot);
      queued(await api.post<Operation>("/v1/compile", { source_id: row.id, target_id: targetId, identity_fields: identity.split(",").map(p => p.trim()), snapshot_hash: artifact.hash }));
    } catch (error) { setError(errorText(error)); } finally { setBusy(false); }
  }
  return <form onSubmit={submit} className="subform"><h3>Propose a recipe</h3><p>Start with a saved schema and a captured sample. Every proposal requires review.</p>
    <label htmlFor="target-id">Saved schema ID</label><input id="target-id" value={targetId} onChange={e => setTargetId(e.target.value)} required />
    <label htmlFor="identity-fields">Identity fields (JSON Pointers)</label><input id="identity-fields" value={identity} onChange={e => setIdentity(e.target.value)} required /><p className="hint">Separate multiple pointers with commas, for example /sku, /country.</p>
    <label htmlFor="sample">Captured HTML, JSON or canonical PDF blocks</label><textarea id="sample" rows={8} className="code-input" value={snapshot} onChange={e => setSnapshot(e.target.value)} required spellCheck={false} />
    {error && <ErrorNotice>{error}</ErrorNotice>}<button className="primary" disabled={busy}>{busy ? "Submitting…" : "Compile sample"}</button>
  </form>;
}

function DecisionForm({ onDecision }: { onDecision: (decision: string, reason: string) => Promise<void> }) {
  const [reason, setReason] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  async function decide(decision: string) { setBusy(true); setError(""); try { await onDecision(decision, reason); } catch (error) { setError(errorText(error)); } finally { setBusy(false); } }
  return <section className="decision"><label htmlFor="decision-reason">Decision reason</label><textarea id="decision-reason" value={reason} onChange={e => setReason(e.target.value)} minLength={5} maxLength={2000} rows={3} placeholder="What did you verify against the evidence?" />
    <p className="hint">Saved with your administrator identity in the audit log. Review the evidence before approving.</p>
    {error && <ErrorNotice>{error}</ErrorNotice>}<div className="actions"><button className="primary" disabled={busy || reason.trim().length < 5} onClick={() => void decide("approve")}>{busy ? "Saving…" : "Approve"}</button><button disabled={busy || reason.trim().length < 5} onClick={() => void decide("reject")}>Reject</button></div>
  </section>;
}

function EvidenceInspector({ api, runId }: { api: Api; runId: string }) {
  const [data, setData] = useState<{ items: RecordRow[]; quarantined: RecordRow[]; total: number } | null>(null);
  const [offset, setOffset] = useState(0), [error, setError] = useState(""), [selected, setSelected] = useState<Evidence | null>(null);
  useEffect(() => { let alive = true; setData(null); setError(""); setSelected(null);
    api.get<{ items: RecordRow[]; quarantined: RecordRow[]; total: number }>(`/v1/runs/${runId}/records?offset=${offset}&limit=20`).then(result => { if (alive) setData(result); }).catch(error => { if (alive) setError(errorText(error)); }); return () => { alive = false; };
  }, [api, runId, offset]);
  if (error) return <ErrorNotice>{error}</ErrorNotice>;
  if (!data) return <p role="status">Loading records…</p>;
  return <section className="records"><h3>Record evidence</h3>
    {!data.items.length && !data.quarantined.length && <p>No retained records for this run yet.</p>}
    {[...data.items, ...data.quarantined].map((record, index) => <details key={record.id || index} className="record" open={index === 0}><summary>{String(record.data.name || record.data.title || record.id || "Unidentified record")}{record.errors.length > 0 && <Badge state="quarantined" />}</summary>
      {record.errors.length > 0 && <ErrorNotice>{record.errors.join("; ")}</ErrorNotice>}
      <Json value={record.data} title="Extracted values" />
      <div className="field-list">{record.evidence.map((field, i) => <button key={field.path + i} onClick={() => setSelected(field)} className={selected === field ? "selected" : ""} aria-pressed={selected === field}><code>{field.path}</code><Badge state={field.error ? "missing" : field.outcome} /></button>)}</div>
    </details>)}
    {selected && <section className="evidence-detail" aria-label="Selected field evidence"><h4>{selected.path}</h4><dl><dt>Outcome</dt><dd>{selected.error || selected.outcome}</dd><dt>Locator</dt><dd><code>{selected.locator.type}: {selected.locator.value}</code></dd><dt>Record scope</dt><dd><code>{selected.source_locator}</code></dd><dt>Capture SHA-256</dt><dd className="hash">{selected.snapshot_hash}</dd></dl><h4>Raw values</h4><pre>{pretty(selected.raw_values)}</pre><h4>Transforms</h4><pre>{pretty(selected.transforms)}</pre></section>}
    <div className="pagination"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous records</button><span>{data.total} valid records</span><button disabled={offset + 20 >= data.total && data.quarantined.length < 20} onClick={() => setOffset(offset + 20)}>Next records</button></div>
  </section>;
}

function ResourcePanel({ row, api, refresh, queued }: { row: Resource; api: Api; refresh: () => void; queued: (op: Operation) => void }) {
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [snapshotHash, setSnapshotHash] = useState(""), [editedRecipe, setEditedRecipe] = useState(pretty(row.data.recipe ?? row.data.spec ?? {}));
  async function act(action: () => Promise<unknown>) { setBusy(true); setError(""); try { await action(); refresh(); } catch (error) { setError(errorText(error)); } finally { setBusy(false); } }
  const state = stateOf(row);
  const reviewDiff = row.kind === "review" ? parseDatasetDiff(row.data.diff) : null;
  return <section className="resource-panel" aria-label="Selected item">
    <div className="section-heading"><h2>{displayName(row)}</h2><Badge state={state} /></div>
    <p className="hint">{row.kind} · version {row.version} · <code>{row.id}</code></p>
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {row.data.error != null && <ErrorNotice>{pretty(row.data.error)}</ErrorNotice>}
    <Json value={row.data} title="Saved configuration and state" />
    {row.kind === "source" && <CompileForm row={row} api={api} queued={queued} />}
    {row.kind === "draft" && row.data.recipe != null && <form className="subform" onSubmit={e => { e.preventDefault(); void act(async () => queued(await api.post<Operation>("/v1/recipes", { spec: JSON.parse(editedRecipe), snapshot_hash: row.data.snapshot_hash }))); }}><h3>Review the proposed mapping</h3><p>Check locators and identity rules against the sample. Validation does not activate the recipe.</p><label htmlFor="recipe-editor">Recipe JSON</label><textarea id="recipe-editor" rows={18} className="code-input" value={editedRecipe} onChange={e => setEditedRecipe(e.target.value)} spellCheck={false} /><button className="primary" disabled={busy}>{busy ? "Submitting…" : "Validate recipe"}</button></form>}
    {row.kind === "recipe" && row.data.validation != null && <ValidationSummary value={row.data.validation} />}
    {row.kind === "recipe" && state === "review_required" && row.data.validation != null && (row.data.validation as { valid?: unknown }).valid === true && <DecisionForm onDecision={async (decision, reason) => { await api.post(`/v1/recipes/${row.id}/activate`, { decision, reason }, row.version); refresh(); }} />}
    {row.kind === "recipe" && state === "active" && <form className="subform" onSubmit={e => { e.preventDefault(); void act(async () => queued(await api.post<Operation>("/v1/runs", { recipe_id: row.id, ...(snapshotHash ? { snapshot_hash: snapshotHash } : {}) }))); }}><h3>Run this recipe</h3><label htmlFor="snapshot-hash">Captured artifact hash (optional)</label><input id="snapshot-hash" value={snapshotHash} onChange={e => setSnapshotHash(e.target.value.trim())} pattern="[a-f0-9]{64}" /><p className="hint">Leave blank for live HTML/JSON acquisition. Rendered HTML and PDF require a retained canonical artifact.</p><button className="primary" disabled={busy}>{busy ? "Queueing…" : "Queue run"}</button></form>}
    {row.kind === "review" && reviewDiff && <ReviewSummary diff={reviewDiff} />}
    {row.kind === "review" && !reviewDiff && <ErrorNotice>The saved diff is unavailable. Refresh this review before deciding.</ErrorNotice>}
    {row.kind === "review" && state === "open" && reviewDiff && <DecisionForm onDecision={async (decision, reason) => { await api.post(`/v1/reviews/${row.id}/decision`, { decision, reason }, row.version); refresh(); }} />}
    {row.kind === "run" && <><dl className="run-facts"><dt>Classification</dt><dd>{String(row.data.classification || "NOT_CONFIGURED")} — no certified prediction is implied.</dd></dl>{row.data.result_key != null && <button disabled={busy} onClick={() => void act(async () => queued(await api.post<Operation>(`/v1/runs/${row.id}/replay`)))}>Verify offline replay</button>}<EvidenceInspector api={api} runId={row.id} /></>}
    {row.kind === "revision" && <button className="primary" disabled={busy} onClick={() => void act(() => api.download(`/v1/revisions/${row.id}/export`, `${row.id}.jsonl`))}><ArrowDownToLine size={16} aria-hidden="true" />Export JSONL with evidence</button>}
  </section>;
}

export function ResourceView({ collection, api, epoch, refresh, queued }: { collection: Collection; api: Api; epoch: number; refresh: () => void; queued: (op: Operation) => void }) {
  const [page, setPage] = useState<Page<Resource> | null>(null), [cursor, setCursor] = useState(""), [cursors, setCursors] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null), [creating, setCreating] = useState(false), [error, setError] = useState("");
  const [loadedCursor, setLoadedCursor] = useState<string | null>(null);
  useEffect(() => { let alive = true; setError("");
    api.get<Page<Resource>>(`/v1/${collection}?limit=25&cursor=${encodeURIComponent(cursor)}`).then(p => { if (alive) { setPage(p); setLoadedCursor(cursor); } }).catch(e => { if (alive) setError(errorText(e)); });
    return () => { alive = false; };
  }, [api, collection, cursor, epoch]);
  const selected = page?.items.find(item => item.id === selectedId);
  const canCreate = ["sources", "schemas", "taxonomies"].includes(collection);
  return <><header className="page-heading"><div><h1>{labels[collection]}</h1><p>{descriptions[collection]}</p></div>{canCreate && <button className="primary" onClick={() => setCreating(!creating)}>Add {collection === "sources" ? "source" : collection === "schemas" ? "schema" : "taxonomy"}</button>}</header>
    {error && <ErrorNotice>{error} <button onClick={refresh}>Retry</button></ErrorNotice>}
    {creating && <Creator key={collection} collection={collection} api={api} saved={refresh} close={() => setCreating(false)} />}
    {!page && !error && <p role="status">Loading {labels[collection].toLowerCase()}…</p>}
    {page && <div className={selected ? "workspace split" : "workspace"}><section className="list-panel" aria-label={labels[collection]}>
      {page.items.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Item</th><th scope="col">State</th><th scope="col">Created</th></tr></thead><tbody>{page.items.map(row => <tr key={row.id} className={selectedId === row.id ? "selected" : ""}><td><button className="row-button" onClick={() => setSelectedId(row.id)} aria-pressed={selectedId === row.id}>{displayName(row)}<small>{row.id}</small></button></td><td><Badge state={stateOf(row)} /></td><td><time dateTime={row.created}>{new Date(row.created + (row.created.endsWith("Z") ? "" : "Z")).toLocaleString()}</time></td></tr>)}</tbody></table></div> : <div className="empty"><FolderInput size={30} aria-hidden="true" /><h2>No {labels[collection].toLowerCase()} yet</h2><p>{collection === "sources" ? "Add a source, define a schema, then compile a captured sample." : collection === "reviews" ? "No publication decisions are waiting on this page." : "Items will appear here as you work through the evidence lifecycle."}</p></div>}
      <div className="pagination"><button disabled={!cursors.length || loadedCursor !== cursor} onClick={() => { setCursor(cursors.at(-1) || ""); setCursors(cursors.slice(0, -1)); setSelectedId(null); }}>Previous</button><span aria-live="polite">{loadedCursor !== cursor ? "Loading page…" : `${page.items.length} items on this page`}</span><button disabled={!page.next_cursor || loadedCursor !== cursor} onClick={() => { setCursors([...cursors, cursor]); setCursor(String(page.next_cursor)); setSelectedId(null); }}>Next</button></div>
    </section>{selected && <ResourcePanel key={selected.id + selected.version} row={selected} api={api} refresh={refresh} queued={queued} />}</div>}
  </>;
}

type AuditRow = { sequence: number; actor: string; action: string; target: string; details: unknown; created: string };
function AuditView({ api, epoch }: { api: Api; epoch: number }) {
  const [after, setAfter] = useState(0), [page, setPage] = useState<Page<AuditRow> | null>(null), [error, setError] = useState("");
  useEffect(() => { let alive = true; setError(""); setPage(null); api.get<Page<AuditRow>>(`/v1/audit?after=${after}`).then(p => { if (alive) setPage(p); }).catch(e => { if (alive) setError(errorText(e)); }); return () => { alive = false; }; }, [api, epoch, after]);
  return <><header className="page-heading"><div><h1>Audit trail</h1><p>Who changed what, and why. Decisions and job events are ordered by durable sequence.</p></div></header>{error && <ErrorNotice>{error}</ErrorNotice>}{!page && !error && <p role="status">Loading audit events…</p>}{page && <><div className="table-scroll"><table><thead><tr><th>Sequence</th><th>Event</th><th>Actor / target</th><th>Details</th></tr></thead><tbody>{page.items.map(row => <tr key={row.sequence}><td>{row.sequence}</td><td>{row.action}<small>{row.created} UTC</small></td><td><code>{row.actor}</code><small>{row.target}</small></td><td><Json value={row.details} title="Event details" /></td></tr>)}</tbody></table></div>{!page.items.length && <p>No audit events yet.</p>}<div className="pagination"><button disabled={!after} onClick={() => setAfter(0)}>First page</button><button disabled={!page.next_cursor} onClick={() => setAfter(Number(page.next_cursor))}>Next events</button></div></>}</>;
}

export default function App() {
  const [api, setApi] = useState<Api | null>(null), [route, setRoute] = useState<Route>("sources"), [epoch, setEpoch] = useState(0), [operation, setOperation] = useState<Operation | null>(null);
  // Stable callback keeps operation polling independent of unrelated renders.
  const [refresh] = useState(() => () => setEpoch(value => value + 1));
  if (!api) return <Connect connect={setApi} />;
  const setup = ["sources", "schemas", "taxonomies", "drafts", "recipes"].includes(route);
  return <div className="app-shell"><a className="skip-link" href="#main">Skip to workspace</a><aside className="sidebar"><div className="brand"><FileCheck2 size={24} aria-hidden="true" /><span>Evidence<br />Pipeline</span></div><nav aria-label="Evidence lifecycle">
    {([{ key: "sources", label: "Setup", icon: FolderInput }, { key: "operations", label: "Operations", icon: ListChecks }, { key: "runs", label: "Runs & evidence", icon: Activity }, { key: "schedules", label: "Schedules", icon: CalendarClock }, { key: "reviews", label: "Review queue", icon: ShieldCheck }, { key: "revisions", label: "Datasets", icon: Database }, { key: "audit", label: "Audit trail", icon: History }, { key: "settings", label: "Settings", icon: HardDrive }] as const).map(item => <button key={item.key} onClick={() => setRoute(item.key)} aria-current={route === item.key || item.key === "sources" && setup ? "page" : undefined}><item.icon size={18} aria-hidden="true" />{item.label}</button>)}
  </nav><div className="sidebar-foot"><span>Development alpha</span><p>Single-workspace control plane</p><button onClick={() => { setApi(null); setOperation(null); }}><LogOut size={16} aria-hidden="true" />Disconnect</button></div></aside>
  <div className="main-shell"><div className="topbar"><span>Workspace / {route === "audit" ? "Audit trail" : route === "operations" ? "Operations" : route === "schedules" ? "Schedules" : route === "settings" ? "Settings" : labels[route]}</span><button onClick={refresh}><RefreshCw size={15} aria-hidden="true" />Refresh</button></div>
  <main id="main" tabIndex={-1}><div className="capability-note">Live: HTML & JSON · Captured replay: all four modes · Classification: abstains until certified</div>
    {operation && <OperationStatus key={operation.id} api={api} operation={operation} refreshed={refresh} clear={() => setOperation(null)} openStorage={() => setRoute("settings")} />}
    {setup && <nav className="tabs" aria-label="Setup sections">{(["sources", "schemas", "taxonomies", "drafts", "recipes"] as Collection[]).map(key => <button key={key} aria-current={route === key ? "page" : undefined} onClick={() => setRoute(key)}>{labels[key]}</button>)}</nav>}
    {route === "audit" ? <AuditView api={api} epoch={epoch} /> : route === "operations" ? <OperationsView api={api} epoch={epoch} openStorage={() => setRoute("settings")} /> : route === "schedules" ? <SchedulesView api={api} epoch={epoch} /> : route === "settings" ? <WorkspaceSettings api={api} epoch={epoch} onTokenRotated={token => setApi(new Api(token))} /> : <ResourceView key={route} collection={route} api={api} epoch={epoch} refresh={refresh} queued={op => { setOperation(op); refresh(); }} />}
  </main><footer>Evidence is retained. Unverified model scores are not published as classifications.</footer></div></div>;
}
