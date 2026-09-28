export type Resource = { id: string; kind: string; version: number; created: string; data: Record<string, unknown> };
export type Page<T> = { items: T[]; next_cursor: string | number | null };
export type Operation = { id: string; kind?: string; state: string; created?: string; attempts?: number; generation?: number; lease_until?: string | null; location?: string; result?: { kind: string; id: string }; error?: { detail?: string; code?: string } | null };
export type Evidence = { path: string; snapshot_hash: string; source_locator: string; locator: { type: string; value: string }; raw_values: unknown[]; transforms: unknown[]; outcome: string; error?: string };
export type RecordRow = { id: string | null; data: Record<string, unknown>; evidence: Evidence[]; errors: string[] };
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
export class Api {
  private pendingKeys = new Map<string, string>();
  constructor(private token: string) {}
  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(path, { ...init, credentials: "omit", cache: "no-store",
      headers: { Authorization: `Bearer ${this.token}`, ...init.headers } });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body.title || response.statusText);
      throw new ApiError(response.status === 412 ? "This item changed. Refresh before deciding again." : detail, response.status);
    }
    return response.json() as Promise<T>;
  }
  get<T>(path: string) { return this.request<T>(path); }
  async post<T>(path: string, body: unknown = {}, version?: number): Promise<T> {
    const encoded = JSON.stringify(body), identity = path + encoded + (version ?? "");
    const key = this.pendingKeys.get(identity) || crypto.randomUUID();
    this.pendingKeys.set(identity, key);
    try {
      const result = await this.request<T>(path, { method: "POST", body: encoded,
        headers: { "Content-Type": "application/json", "Idempotency-Key": key, ...(version === undefined ? {} : { "If-Match": `"${version}"` }) } });
      this.pendingKeys.delete(identity); return result;
    } catch (error) {
      // Retain the same key after ambiguous transport/server failure.
      if (error instanceof ApiError && error.status < 500) this.pendingKeys.delete(identity);
      throw error;
    }
  }
  upload(text: string) { return this.request<{hash: string}>("/v1/artifacts", { method: "PUT", body: text, headers: { "Content-Type": "text/plain" } }); }
  async download(path: string, filename: string) {
    const response = await fetch(path, { headers: { Authorization: `Bearer ${this.token}` }, credentials: "omit", cache: "no-store" });
    if (!response.ok) throw new ApiError("Export failed. Refresh the revision and retry.", response.status);
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a"); link.href = url; link.download = filename; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
export const pretty = (value: unknown) => JSON.stringify(value, null, 2);
