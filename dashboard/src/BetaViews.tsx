import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Api } from "./api";
import type { Page, Resource } from "./api";

const errorText = (error: unknown) => error instanceof Error ? error.message : "Request failed. Use Refresh to retry.";
const stateOf = (row: Resource) => String(row.data.state || row.data.decision || "pending");

function DomainDecision({ row, api, saved }: { row: Resource; api: Api; saved: () => void }) {
  const [reason, setReason] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  async function decide(decision: "approve" | "reject") {
    setBusy(true); setError("");
    try { await api.post(`/v1/domain-requests/${encodeURIComponent(row.id)}/decision`, { decision, reason: reason.trim() }, row.version); saved(); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }
  return <div className="domain-decision">
    <label htmlFor={`domain-reason-${row.id}`}>Operator decision reason for {String(row.data.domain)}</label>
    <textarea id={`domain-reason-${row.id}`} rows={2} minLength={5} maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} placeholder="Record permission and scope checks." />
    {error && <div className="notice error" role="alert">{error}</div>}
    <div className="actions"><button className="primary" disabled={busy || reason.trim().length < 5} onClick={() => void decide("approve")}>Approve domain</button><button disabled={busy || reason.trim().length < 5} onClick={() => void decide("reject")}>Reject domain</button></div>
  </div>;
}

export function DomainsView({ api, epoch, isOperator = false }: { api: Api; epoch: number; isOperator?: boolean }) {
  const [approved, setApproved] = useState<string[]>([]), [page, setPage] = useState<Page<Resource> | null>(null);
  const [domain, setDomain] = useState(""), [reason, setReason] = useState(""), [error, setError] = useState(""), [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false), [reload, setReload] = useState(0), [cursor, setCursor] = useState("");
  const refresh = () => setReload(value => value + 1);
  useEffect(() => {
    let alive = true; setError(""); setPage(null);
    void Promise.all([
      api.get<Page<{ domain: string }>>("/v1/approved-domains"),
      api.get<Page<Resource>>(`/v1/domain-requests?limit=25&cursor=${encodeURIComponent(cursor)}`),
    ]).then(([domains, requests]) => { if (alive) { setApproved(domains.items.map(item => item.domain)); setPage(requests); } })
      .catch(error => { if (alive) setError(errorText(error)); });
    return () => { alive = false; };
  }, [api, epoch, reload, cursor]);

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setMessage("");
    try {
      await api.post("/v1/domain-requests", { domain: domain.trim().toLowerCase(), reason: reason.trim() });
      setDomain(""); setReason(""); setCursor(""); refresh();
      setMessage("Domain requested. Live extraction remains blocked until an operator approves this exact hostname.");
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }

  return <>
    <header className="page-heading"><div><h1>Approved sources</h1><p>Public beta uses exact approved hostnames, not wildcard access. Approval never bypasses robots rules, unsafe-address checks, or resource limits.</p></div></header>
    {error && <div className="notice error" role="alert">{error} <button onClick={refresh}>Retry domains</button></div>}
    {message && <p className="notice success" role="status">{message}</p>}
    <div className="settings-stack">
      <section className="resource-panel" aria-labelledby="approved-domains-heading"><h2 id="approved-domains-heading">Available hostnames</h2>
        {approved.length ? <ul className="approved-domain-list">{approved.map(value => <li key={value}><code>{value}</code></li>)}</ul> : <p>No approved domains are currently available. Request one below; offline samples remain usable.</p>}
      </section>
      <form className="resource-panel" onSubmit={submit}><h2>Request a public source</h2>
        <label htmlFor="requested-domain">Exact hostname</label><input id="requested-domain" value={domain} onChange={event => setDomain(event.target.value)} required maxLength={253} placeholder="catalog.example.org" autoCapitalize="none" spellCheck={false} pattern={"[^\\/:\\s*]+"} />
        <p className="hint">Enter the hostname only, without https://, paths, or wildcards. Subdomains need their own approval.</p>
        <label htmlFor="domain-request-reason">Purpose and permission</label><textarea id="domain-request-reason" rows={3} minLength={5} maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} required placeholder="What public records do you need, and what permits their use?" />
        <button className="primary" disabled={busy || reason.trim().length < 5}>{busy ? "Submitting…" : "Request domain approval"}</button>
      </form>
      <section className="resource-panel" aria-labelledby="domain-requests-heading"><h2 id="domain-requests-heading">{isOperator ? "Domain approval queue" : "Your requests"}</h2>
        {!page && !error && <p role="status">Loading domain requests…</p>}
        {page && !page.items.length && <p>No domain requests on this page.</p>}
        {page?.items.map(row => <article key={row.id + row.version} className="domain-request"><div className="section-heading"><h3><code>{String(row.data.domain)}</code></h3><span className={`badge badge-${stateOf(row)}`}>{stateOf(row).replaceAll("_", " ")}</span></div><p>{String(row.data.reason || "")}</p>
          {row.data.decision_reason != null && <p><strong>Operator decision:</strong> {String(row.data.decision_reason)}</p>}
          {isOperator && ["pending", "open", "requested"].includes(stateOf(row)) && <DomainDecision row={row} api={api} saved={refresh} />}
        </article>)}
        <div className="pagination"><button disabled={!cursor || !page} onClick={() => setCursor("")}>First requests</button><button disabled={!page?.next_cursor} onClick={() => setCursor(String(page?.next_cursor))}>Next requests</button></div>
      </section>
    </div>
  </>;
}

type ScopeBudget = { period: string; reserved: Record<string, number>; limits: Record<string, number> };
type BetaBudget = { mode: string; workspace: ScopeBudget; global: ScopeBudget; accounting: string };
export function BudgetView({ api, epoch }: { api: Api; epoch: number }) {
  const [budget, setBudget] = useState<BetaBudget | null>(null), [error, setError] = useState("");
  useEffect(() => {
    let alive = true; setError("");
    void api.get<BetaBudget>("/v1/budget").then(value => { if (alive) setBudget(value); }).catch(error => { if (alive) setError(errorText(error)); });
    return () => { alive = false; };
  }, [api, epoch]);
  return <section className="resource-panel beta-budget" aria-labelledby="budget-heading"><h2 id="budget-heading">Beta resource budget</h2><p>Ingestion pauses when workspace or platform budgets are exhausted. Reservations account for queued work, not only completed work. Refresh on demand; this page does not keep the database awake.</p>
    {error && <div className="notice error" role="alert">{error}</div>}
    {!budget && !error && <p role="status">Loading budget…</p>}
    {budget && <><p className="hint">Conservative admission reservations, not provider billing or a guarantee of remaining free-tier credit.</p>{(["workspace", "global"] as const).map(scope => <div key={scope} className="budget-group"><h3>{scope === "workspace" ? "Your workspace" : "Shared platform"}</h3><p className="hint">Monthly period: {budget[scope].period}</p><dl className="budget-metrics">{Object.entries(budget[scope].limits).map(([name, limit]) => {
      const reserved = budget[scope].reserved[name] || 0;
      return <div key={name}><dt>{name.replaceAll("_", " ")}</dt><dd>{new Intl.NumberFormat("en").format(reserved)} / {new Intl.NumberFormat("en").format(limit)} reserved<progress aria-label={`${scope} ${name.replaceAll("_", " ")} reserved`} value={Math.min(reserved, limit)} max={Math.max(limit, 1)} /></dd></div>;
    })}</dl></div>)}</>}
  </section>;
}
