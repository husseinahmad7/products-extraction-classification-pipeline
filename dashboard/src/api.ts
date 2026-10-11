export type Resource = { id: string; kind: string; version: number; created: string; data: Record<string, unknown> };
export type Page<T> = { items: T[]; next_cursor: string | number | null };
export type Operation = { id: string; kind?: string; state: string; created?: string; attempts?: number; generation?: number; lease_until?: string | null; location?: string; result?: { kind: string; id: string }; error?: { detail?: string; code?: string } | null };
export type Evidence = { path: string; snapshot_hash: string; source_locator: string; locator: { type: string; value: string }; raw_values: unknown[]; transforms: unknown[]; outcome: string; error?: string };
export type RecordRow = { id: string | null; data: Record<string, unknown>; evidence: Evidence[]; errors: string[] };
export type WorkspaceIdentity = { actor: string; workspace: string; is_operator: boolean; auth_mode: string };
export type TokenProvider = () => Promise<string>;
export const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || "").trim().replace(/\/$/, "");

function endpoint(path: string, baseUrl: string) {
  if (!path.startsWith("/v1/")) throw new Error("Only workspace API paths are supported.");
  if (baseUrl) {
    const url = new URL(baseUrl);
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if ((url.protocol !== "https:" && !(local && url.protocol === "http:")) || url.username || url.password || url.search || url.hash) {
      throw new Error("Configure an HTTPS API URL (HTTP is allowed only for local development).");
    }
  }
  return baseUrl + path;
}
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
export class Api {
  private pendingKeys = new Map<string, string>();
  constructor(private token: string | TokenProvider, private baseUrl = apiBaseUrl) {}
  private async authorization() {
    // Managed sessions refresh through the SDK; never persist their bearer JWT.
    const token = typeof this.token === "string" ? this.token : await this.token();
    if (!token) throw new ApiError("Your session has ended. Sign in again.", 401);
    return `Bearer ${token}`;
  }
  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(endpoint(path, this.baseUrl), { ...init, credentials: "omit", cache: "no-store", redirect: "error",
      headers: { ...init.headers, Authorization: await this.authorization() } });
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
    const response = await fetch(endpoint(path, this.baseUrl), { headers: { Authorization: await this.authorization() }, credentials: "omit", cache: "no-store", redirect: "error" });
    if (!response.ok) throw new ApiError("Export failed. Refresh the revision and retry.", response.status);
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a"); link.href = url; link.download = filename; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
export const pretty = (value: unknown) => JSON.stringify(value, null, 2);
