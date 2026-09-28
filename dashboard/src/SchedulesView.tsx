import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Api } from "./api";
import type { Page } from "./api";

type Schedule = {
  id: string; source_id: string; recipe_id: string; interval_seconds: number;
  next_due: string | null; state: "active" | "paused"; version: number;
  last_run_id: string | null; created: string | null;
};
const errorText = (error: unknown) => error instanceof Error ? error.message : "Request failed. Try again.";
const utc = (value: string) => /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : value + "Z";
const date = (value: string | null) => value ? new Date(utc(value)).toLocaleString() : "—";

export function SchedulesView({ api, epoch }: { api: Api; epoch: number }) {
  const [page, setPage] = useState<Page<Schedule> | null>(null);
  const [cursor, setCursor] = useState("");
  const [cursors, setCursors] = useState<string[]>([]);
  const [loadedCursor, setLoadedCursor] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [selected, setSelected] = useState<Schedule | null>(null);
  const [recipeId, setRecipeId] = useState("");
  const [interval, setIntervalValue] = useState("3600");
  const [tick, setTick] = useState(0);
  const [detailTick, setDetailTick] = useState(0);
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [actionError, setActionError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api.get<Page<Schedule>>(`/v1/schedules?limit=25&cursor=${encodeURIComponent(cursor)}`)
      .then(result => { if (alive) { setPage(result); setLoadedCursor(cursor); setError(""); } })
      .catch(cause => { if (alive) setError(errorText(cause)); });
    return () => { alive = false; };
  }, [api, cursor, epoch, tick]);
  useEffect(() => {
    if (!selectedId) { setSelected(null); setDetailError(""); return; }
    let alive = true;
    setSelected(null); setDetailError("");
    api.get<Schedule>(`/v1/schedules/${encodeURIComponent(selectedId)}`)
      .then(result => { if (alive) { setSelected(result); setDetailError(""); } })
      .catch(cause => { if (alive) { setSelected(null); setDetailError(errorText(cause)); } });
    return () => { alive = false; };
  }, [api, selectedId, epoch, tick, detailTick]);

  async function create(event: FormEvent) {
    event.preventDefault(); setBusy(true); setActionError(""); setMessage("");
    try {
      const result = await api.post<Schedule>("/v1/schedules", { recipe_id: recipeId.trim(), interval_seconds: Number(interval) });
      setSelectedId(result.id); setSelected(result); setRecipeId(""); setTick(value => value + 1);
      setMessage("Schedule created. Its first run is due after the interval.");
    } catch (cause) { setActionError(errorText(cause)); }
    finally { setBusy(false); }
  }
  async function change(action: "pause" | "resume") {
    if (!selected) return;
    setBusy(true); setActionError(""); setMessage("");
    try {
      const result = await api.post<Schedule>(`/v1/schedules/${selected.id}/${action}`, {}, selected.version);
      setSelected(result); setTick(value => value + 1);
      setMessage(action === "pause" ? "Schedule paused." : "Schedule resumed. The next run is due after its interval.");
    } catch (cause) { setActionError(errorText(cause)); }
    finally { setBusy(false); }
  }

  return <>
    <header className="page-heading"><div><h1>Schedules</h1><p>Run an approved live HTML or JSON recipe on a fixed interval.</p></div></header>
    <form className="editor" onSubmit={create}>
      <h2>Create schedule</h2>
      <p>The recipe must already be active. A schedule starts after one full interval; a worker must be running to dispatch it.</p>
      <label htmlFor="schedule-recipe">Active recipe ID</label><input id="schedule-recipe" value={recipeId} onChange={event => setRecipeId(event.target.value)} required />
      <label htmlFor="schedule-interval">Interval (seconds)</label><input id="schedule-interval" type="number" min="60" max="2592000" step="1" value={interval} onChange={event => setIntervalValue(event.target.value)} required />
      <p className="hint">Minimum 60 seconds; maximum 30 days. Pausing stops future dispatches.</p>
      <button className="primary" disabled={busy || !recipeId.trim()}>{busy ? "Creating…" : "Create schedule"}</button>
    </form>
    {message && <p className="notice success" role="status">{message}</p>}
    {actionError && <div className="notice error" role="alert">{actionError}</div>}
    {error && <div className="notice error" role="alert">{error} Use Refresh to retry.</div>}
    {!page && !error && <p role="status">Loading schedules…</p>}
    {page && <div className={selectedId ? "workspace split" : "workspace"}>
      <section className="list-panel" aria-label="Saved schedules">
        {page.items.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Recipe</th><th scope="col">State</th><th scope="col">Next due</th></tr></thead><tbody>
          {page.items.map(row => <tr key={row.id} className={selectedId === row.id ? "selected" : ""}><td><button className="row-button" aria-pressed={selectedId === row.id} onClick={() => { setSelectedId(row.id); setSelected(null); setDetailError(""); setActionError(""); }}>{row.recipe_id}<small>{row.id}</small></button></td><td><span className={`badge badge-${row.state}`}>{row.state}</span></td><td><time dateTime={row.next_due ? utc(row.next_due) : undefined}>{date(row.next_due)}</time></td></tr>)}
        </tbody></table></div> : <div className="empty"><h2>No schedules yet</h2><p>Create one for an active HTML or JSON recipe after validating its sample.</p></div>}
        <div className="pagination"><button disabled={!cursors.length || loadedCursor !== cursor} onClick={() => { setCursor(cursors.at(-1) || ""); setCursors(cursors.slice(0, -1)); setPage(null); }}>Previous</button><span>{loadedCursor === cursor ? `${page.items.length} schedules on this page` : "Loading page…"}</span><button disabled={!page.next_cursor || loadedCursor !== cursor} onClick={() => { setCursors([...cursors, cursor]); setCursor(String(page.next_cursor)); setPage(null); }}>Next</button></div>
      </section>
      {selectedId && <section className="resource-panel" aria-label="Selected schedule">
        <div className="section-heading"><h2>Schedule detail</h2>{selected && <span className={`badge badge-${selected.state}`}>{selected.state}</span>}</div>
        <p className="hint"><code>{selectedId}</code></p>
        {detailError && <div className="notice error" role="alert"><p>Could not load this schedule: {detailError}</p><button onClick={() => setDetailTick(value => value + 1)}>Retry schedule detail</button></div>}
        {!selected && !detailError && <p role="status">Loading schedule…</p>}
        {selected && <><dl className="settings-facts"><div><dt>Source</dt><dd><code>{selected.source_id}</code></dd></div><div><dt>Recipe</dt><dd><code>{selected.recipe_id}</code></dd></div><div><dt>Interval</dt><dd>{selected.interval_seconds} seconds</dd></div><div><dt>Next due</dt><dd>{date(selected.next_due)}</dd></div><div><dt>Last run</dt><dd>{selected.last_run_id ? <code>{selected.last_run_id}</code> : "None yet"}</dd></div></dl>
          <div className="actions"><button disabled={busy} onClick={() => void change(selected.state === "active" ? "pause" : "resume")}>{busy ? "Saving…" : selected.state === "active" ? "Pause schedule" : "Resume schedule"}</button></div>
        </>}
      </section>}
    </div>}
  </>;
}
