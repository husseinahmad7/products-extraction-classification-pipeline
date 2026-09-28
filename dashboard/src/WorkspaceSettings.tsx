import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Api } from "./api";

type StorageQuota = { used_bytes: number; limit_bytes: number };
type TokenRotation = { token: string };
type Capabilities = { acquisition: { live: string[]; captured_only: string[] }; navigation: string[]; scheduler: boolean; classification: { state: string; automatic_promotion: boolean } };

const bytes = (value: number) => `${new Intl.NumberFormat().format(value)} bytes`;
const errorText = (error: unknown) => error instanceof Error ? error.message : "Request failed. Try again.";

export function WorkspaceSettings({ api, epoch, onTokenRotated }: { api: Api; epoch: number; onTokenRotated: (token: string) => void }) {
  const [quota, setQuota] = useState<StorageQuota | null>(null);
  const [loadError, setLoadError] = useState("");
  const [limit, setLimit] = useState("");
  const [reason, setReason] = useState("");
  const [storageError, setStorageError] = useState("");
  const [storageMessage, setStorageMessage] = useState("");
  const [storageBusy, setStorageBusy] = useState(false);
  const [tokenBusy, setTokenBusy] = useState(false);
  const [tokenError, setTokenError] = useState("");
  const [newToken, setNewToken] = useState("");
  const [confirmRotation, setConfirmRotation] = useState(false);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [capabilityError, setCapabilityError] = useState("");

  useEffect(() => {
    let alive = true;
    api.get<StorageQuota>("/v1/storage").then(value => {
      if (!alive) return;
      setQuota(value);
      setLoadError("");
      setLimit(current => current || String(value.limit_bytes));
    }).catch(error => { if (alive) setLoadError(errorText(error)); });
    return () => { alive = false; };
  }, [api, epoch]);
  useEffect(() => {
    let alive = true;
    api.get<Capabilities>("/v1/capabilities").then(value => { if (alive) { setCapabilities(value); setCapabilityError(""); } })
      .catch(error => { if (alive) setCapabilityError(errorText(error)); });
    return () => { alive = false; };
  }, [api, epoch]);

  const proposed = /^\d+$/.test(limit) ? Number(limit) : NaN;
  const validLimit = quota && Number.isSafeInteger(proposed) && proposed > quota.limit_bytes;

  async function expand(event: FormEvent) {
    event.preventDefault();
    if (!validLimit || reason.trim().length < 5) return;
    setStorageBusy(true); setStorageError(""); setStorageMessage("");
    try {
      const result = await api.post<StorageQuota>("/v1/storage/expand", { limit_bytes: proposed, reason: reason.trim() });
      setQuota(result); setLimit(String(result.limit_bytes)); setReason("");
      setStorageMessage(`Capacity expanded to ${bytes(result.limit_bytes)}. Resume paused operations from Operations.`);
    } catch (error) { setStorageError(errorText(error)); }
    finally { setStorageBusy(false); }
  }

  async function rotateToken() {
    if (!confirmRotation) return;
    setTokenBusy(true); setTokenError(""); setNewToken("");
    try {
      const result = await api.post<TokenRotation>("/v1/admin-tokens/rotate", { overlap_seconds: 300 });
      setNewToken(result.token);
      setConfirmRotation(false);
      onTokenRotated(result.token);
    } catch (error) { setTokenError(errorText(error)); }
    finally { setTokenBusy(false); }
  }

  return <>
    <header className="page-heading"><div><h1>Workspace settings</h1><p>Manage retained evidence capacity and your administrator session.</p></div></header>
    <div className="settings-stack">
      <section className="resource-panel" aria-labelledby="storage-heading">
        <h2 id="storage-heading">Evidence storage</h2>
        <p>Captured artifacts count against the workspace quota. Reaching it pauses affected operations and retains existing evidence.</p>
        {loadError && <div className="notice error" role="alert">{loadError} Use Refresh to retry.</div>}
        {!quota && !loadError && <p role="status">Loading storage capacity…</p>}
        {quota && <>
          <dl className="settings-facts"><div><dt>Used</dt><dd>{bytes(quota.used_bytes)}</dd></div><div><dt>Capacity</dt><dd>{bytes(quota.limit_bytes)}</dd></div><div><dt>Available</dt><dd>{bytes(Math.max(0, quota.limit_bytes - quota.used_bytes))}</dd></div></dl>
          <progress aria-label="Storage used" value={Math.min(quota.used_bytes, quota.limit_bytes)} max={quota.limit_bytes} />
          <form onSubmit={expand} className="subform">
            <h3>Expand capacity</h3>
            <p>Enter the new total in bytes. Capacity can only increase; expansion never removes saved artifacts.</p>
            <label htmlFor="storage-limit">New total capacity (bytes)</label>
            <input id="storage-limit" inputMode="numeric" pattern="[0-9]+" value={limit} onChange={event => setLimit(event.target.value)} required aria-describedby="storage-limit-hint" />
            <p id="storage-limit-hint" className="hint">Enter a whole number greater than {bytes(quota.limit_bytes)}.</p>
            <label htmlFor="storage-reason">Reason for expansion</label>
            <textarea id="storage-reason" rows={3} minLength={5} maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} required placeholder="Why does this workspace need more capacity?" />
            {storageError && <div className="notice error" role="alert">{storageError}</div>}
            {storageMessage && <p className="notice success" role="status">{storageMessage}</p>}
            <button className="primary" disabled={storageBusy || !validLimit || reason.trim().length < 5}>{storageBusy ? "Expanding…" : "Expand capacity"}</button>
          </form>
        </>}
      </section>
      <section className="resource-panel" aria-labelledby="access-heading">
        <h2 id="access-heading">Administrator access</h2>
        <p>Rotate this workspace token when an operator changes. The current token remains valid for five minutes so clients can switch over.</p>
        {tokenError && <div className="notice error" role="alert">{tokenError}</div>}
        {!confirmRotation && <button onClick={() => { setNewToken(""); setTokenError(""); setConfirmRotation(true); }}>Rotate administrator token</button>}
        {confirmRotation && <div className="notice warning" role="group" aria-label="Confirm token rotation">
          <p>Other clients using the current token will lose access in five minutes. Be ready to update them with the new token, which is shown only once.</p>
          <div className="actions"><button className="primary" disabled={tokenBusy} onClick={() => void rotateToken()}>{tokenBusy ? "Rotating…" : "Confirm token rotation"}</button><button disabled={tokenBusy} onClick={() => setConfirmRotation(false)}>Cancel rotation</button></div>
        </div>}
        {newToken && <div className="token-result" role="status"><p>Copy this new token now. It is shown only on this page, and this dashboard has switched to it.</p><label htmlFor="rotated-token">New administrator token</label><textarea id="rotated-token" className="code-input" readOnly value={newToken} rows={2} onFocus={event => event.currentTarget.select()} /></div>}
      </section>
      <section className="resource-panel" aria-labelledby="capabilities-heading">
        <h2 id="capabilities-heading">Capability boundaries</h2>
        {capabilityError && <div className="notice error" role="alert">{capabilityError} Use Refresh to retry.</div>}
        {!capabilities && !capabilityError && <p role="status">Loading capabilities…</p>}
        {capabilities && <ul className="capability-list">
          <li><strong>Live acquisition:</strong> {capabilities.acquisition.live.join(", ") || "None"}.</li>
          <li><strong>Captured artifact required:</strong> {capabilities.acquisition.captured_only.join(", ") || "None"}.</li>
          <li><strong>Page navigation:</strong> {capabilities.navigation.map(value => value.replaceAll("_", " ")).join(", ") || "Unavailable"}.</li>
          <li><strong>Scheduler:</strong> {capabilities.scheduler ? "Available" : "Unavailable; queue runs manually"}.</li>
          <li><strong>Classification:</strong> {capabilities.classification.state.replaceAll("_", " ")}; automatic promotion {capabilities.classification.automatic_promotion ? "enabled" : "disabled"}.</li>
          <li><strong>Publication:</strong> invalid records fail the quality gate. Removed records require a recorded review decision.</li>
          <li><strong>Workspace:</strong> one self-hosted workspace; the administrator token stays in browser memory for this session.</li>
        </ul>}
      </section>
    </div>
  </>;
}
