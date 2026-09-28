import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Api } from "./api";
import type { Operation, Page } from "./api";

const active = (state: string) => state === "queued" || state === "running";
const errorText = (error: unknown) => error instanceof Error ? error.message : "Request failed. Try again.";
const label = (value: string) => value.replaceAll("_", " ");
const utc = (value: string) => /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : value + "Z";

export function OperationsView({ api, epoch, openStorage }: { api: Api; epoch: number; openStorage: () => void }) {
  const [page, setPage] = useState<Page<Operation> | null>(null);
  const [cursor, setCursor] = useState("");
  const [cursors, setCursors] = useState<string[]>([]);
  const [loadedCursor, setLoadedCursor] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [error, setError] = useState("");
  const [lookup, setLookup] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [selected, setSelected] = useState<Operation | null>(null);
  const [detailError, setDetailError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const timer = setInterval(() => setTick(value => value + 1), 5000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    let alive = true;
    api.get<Page<Operation>>(`/v1/operations?limit=25&cursor=${encodeURIComponent(cursor)}`)
      .then(result => { if (alive) { setPage(result); setLoadedCursor(cursor); setError(""); } })
      .catch(cause => { if (alive) setError(errorText(cause)); });
    return () => { alive = false; };
  }, [api, cursor, epoch, tick]);
  useEffect(() => {
    if (!selectedId) { setSelected(null); return; }
    let alive = true;
    api.get<Operation>(`/v1/operations/${encodeURIComponent(selectedId)}`)
      .then(result => { if (alive) { setSelected(result); setDetailError(""); } })
      .catch(cause => { if (alive) setDetailError(errorText(cause)); });
    return () => { alive = false; };
  }, [api, selectedId, epoch, tick]);

  function lookupOperation(event: FormEvent) {
    event.preventDefault(); setSelected(null); setDetailError(""); setSelectedId(lookup.trim());
  }
  async function change(action: "cancel" | "resume") {
    if (!selected) return;
    setBusy(true); setActionError("");
    try {
      await api.post(`/v1/operations/${selected.id}/${action}`);
      setSelected(await api.get<Operation>(`/v1/operations/${selected.id}`));
      setTick(value => value + 1);
    } catch (cause) { setActionError(errorText(cause)); }
    finally { setBusy(false); }
  }

  return <>
    <header className="page-heading"><div><h1>Operations</h1><p>Track queued work, inspect failures, and resume jobs paused by the evidence quota.</p></div></header>
    <form className="operation-lookup" onSubmit={lookupOperation}>
      <label htmlFor="operation-id">Find an operation by ID</label>
      <div className="actions"><input id="operation-id" value={lookup} onChange={event => setLookup(event.target.value)} placeholder="Operation ID" required /><button>Find operation</button></div>
    </form>
    {error && <div className="notice error" role="alert">{error} Use Refresh to retry.</div>}
    {!page && !error && <p role="status">Loading operations…</p>}
    {page && <div className={selectedId ? "workspace split" : "workspace"}>
      <section className="list-panel" aria-label="Recent operations">
        {page.items.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Operation</th><th scope="col">State</th><th scope="col">Attempts</th><th scope="col">Created</th></tr></thead><tbody>
          {page.items.map(row => <tr key={row.id} className={selectedId === row.id ? "selected" : ""}><td><button className="row-button" onClick={() => { setSelected(null); setDetailError(""); setSelectedId(row.id); }} aria-pressed={selectedId === row.id}>{row.kind || "operation"}<small>{row.id}</small></button></td><td><span className={`badge badge-${row.state}`}>{label(row.state)}</span></td><td>{row.attempts ?? "—"}</td><td>{row.created ? <time dateTime={utc(row.created)}>{new Date(utc(row.created)).toLocaleString()}</time> : "—"}</td></tr>)}
        </tbody></table></div> : <div className="empty"><h2>No operations yet</h2><p>Compile a sample, validate a recipe, or run extraction to see work here.</p></div>}
        <div className="pagination"><button disabled={!cursors.length || loadedCursor !== cursor} onClick={() => { setCursor(cursors.at(-1) || ""); setCursors(cursors.slice(0, -1)); setPage(null); }}>Previous</button><span aria-live="polite">{loadedCursor === cursor ? `${page.items.length} operations on this page` : "Loading page…"}</span><button disabled={!page.next_cursor || loadedCursor !== cursor} onClick={() => { setCursors([...cursors, cursor]); setCursor(String(page.next_cursor)); setPage(null); }}>Next</button></div>
      </section>
      {selectedId && <section className="resource-panel" aria-label="Selected operation">
        <div className="section-heading"><h2>Operation detail</h2>{selected && <span className={`badge badge-${selected.state}`}>{label(selected.state)}</span>}</div>
        <p className="hint"><code>{selectedId}</code></p>
        {detailError && <div className="notice error" role="alert">{detailError}</div>}
        {!selected && !detailError && <p role="status">Loading operation…</p>}
        {selected && <><dl className="settings-facts"><div><dt>Type</dt><dd>{selected.kind || "Unknown"}</dd></div><div><dt>Attempts</dt><dd>{selected.attempts ?? "Unknown"}</dd></div><div><dt>Generation</dt><dd>{selected.generation ?? "Unknown"}</dd></div></dl>
          {selected.lease_until && <p className="hint">Worker lease until <time dateTime={utc(selected.lease_until)}>{new Date(utc(selected.lease_until)).toLocaleString()}</time></p>}
          {selected.error && <div className="notice error" role="alert">{selected.error.detail || "Operation failed. Check the server logs using this operation ID."}</div>}
          {selected.result && <p>Result: {selected.result.kind} <code>{selected.result.id}</code></p>}
          {selected.state === "paused" && <p>The operation is paused. Expand capacity if needed, then resume it. Existing evidence remains retained.</p>}
          {actionError && <div className="notice error" role="alert">{actionError}</div>}
          <div className="actions">{selected.state === "paused" && <><button onClick={openStorage}>Storage settings</button><button className="primary" disabled={busy} onClick={() => void change("resume")}>{busy ? "Resuming…" : "Resume operation"}</button></>}
            {(active(selected.state) || selected.state === "paused") && <button disabled={busy} onClick={() => void change("cancel")}>Cancel operation</button>}</div>
        </>}
      </section>}
    </div>}
  </>;
}
