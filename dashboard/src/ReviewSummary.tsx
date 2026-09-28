import { pretty } from "./api";

export type DatasetDiff = {
  added: string[];
  removed: string[];
  changed: { id: string; before: unknown; after: unknown }[];
};

export function parseDatasetDiff(value: unknown): DatasetDiff | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<DatasetDiff>;
  if (!Array.isArray(candidate.added) || !candidate.added.every(item => typeof item === "string")
    || !Array.isArray(candidate.removed) || !candidate.removed.every(item => typeof item === "string")
    || !Array.isArray(candidate.changed) || !candidate.changed.every(item => item && typeof item.id === "string" && "before" in item && "after" in item)) return null;
  return candidate as DatasetDiff;
}

export function ReviewSummary({ diff }: { diff: DatasetDiff }) {
  return <section className="review-summary" aria-label="Dataset changes">
    <h3>Dataset changes</h3>
    <p>Review each removal before deciding. Approval publishes this run as a new immutable revision.</p>
    <dl className="diff-counts"><div><dt>Removed</dt><dd>{diff.removed.length}</dd></div><div><dt>Changed</dt><dd>{diff.changed.length}</dd></div><div><dt>Added</dt><dd>{diff.added.length}</dd></div></dl>
    <h4>Removed record IDs</h4>
    {diff.removed.length ? <ul className="diff-list">{diff.removed.map(id => <li key={id}><code>{id}</code></li>)}</ul> : <p>No records removed.</p>}
    {diff.changed.length > 0 && <details><summary>Changed records ({diff.changed.length})</summary>
      {diff.changed.map(change => <details className="record" key={change.id}><summary><code>{change.id}</code></summary><div className="diff-pair"><div><h4>Before</h4><pre>{pretty(change.before)}</pre></div><div><h4>After</h4><pre>{pretty(change.after)}</pre></div></div></details>)}
    </details>}
    {diff.added.length > 0 && <details><summary>Added record IDs ({diff.added.length})</summary><ul className="diff-list">{diff.added.map(id => <li key={id}><code>{id}</code></li>)}</ul></details>}
  </section>;
}

export function ValidationSummary({ value }: { value: unknown }) {
  const valid = value && typeof value === "object" && (value as { valid?: unknown }).valid === true;
  const report = value && typeof value === "object" ? value as Record<string, unknown> : {};
  return <section className="validation-summary" aria-label="Recipe validation">
    <h3>Validation result</h3>
    <p><span className={`badge badge-${valid ? "approved" : "invalid"}`}>{valid ? "Passed" : "Failed"}</span> {valid ? "This captured sample passed extraction and offline replay." : "This recipe cannot be activated. Revise the draft mapping and validate a new recipe."}</p>
    <dl className="settings-facts"><div><dt>Valid records</dt><dd>{String(report.records ?? "Unknown")}</dd></div><div><dt>Quarantined</dt><dd>{String(report.quarantined ?? "Unknown")}</dd></div></dl>
    {typeof report.snapshot_hash === "string" && <p className="hint">Capture SHA-256 <code>{report.snapshot_hash}</code></p>}
    {typeof report.result_hash === "string" && <p className="hint">Result SHA-256 <code>{report.result_hash}</code></p>}
  </section>;
}
