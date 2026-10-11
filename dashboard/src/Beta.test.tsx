import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Api } from "./api";
import { managedApi } from "./auth";
import type { ManagedAuthClient } from "./auth";
import { PublicAuth } from "./PublicAuth";
import { BudgetView, DomainsView } from "./BetaViews";
import { LabelReview, LearningView } from "./LearningView";
import { OperationStatus, ResourceView } from "./App";
import { WorkspaceSettings } from "./WorkspaceSettings";

const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
const identity = { actor: "user-1", workspace: "personal-1", is_operator: false, auth_mode: "neon" };
const capabilities = { acquisition: { live: ["html", "json"], captured_only: ["browser", "pdf"] }, navigation: [], scheduler: true, classification: { state: "not_certified", automatic_promotion: false } };
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("managed account access", () => {
  it("obtains the SDK bearer token for every request and export without saving it", async () => {
    const token = vi.fn().mockResolvedValueOnce({ data: { token: "first-jwt" } }).mockResolvedValueOnce({ data: { token: "refreshed-jwt" } }).mockResolvedValueOnce({ data: { token: "export-jwt" } });
    const client = { token } as unknown as ManagedAuthClient;
    const fetcher = vi.fn(async () => json({})); vi.stubGlobal("fetch", fetcher);
    const NativeURL = URL;
    vi.stubGlobal("URL", class extends NativeURL { static createObjectURL = vi.fn(() => "blob:test"); static revokeObjectURL = vi.fn(); });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const api = managedApi(client);
    await api.get("/v1/me"); await api.upload("sample"); await api.download("/v1/datasets/snapshot-1/export", "training.json");
    expect(token).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls.map(call => (call as unknown as [string, RequestInit])[1].headers)).toEqual([
      { Authorization: "Bearer first-jwt" }, { "Content-Type": "text/plain", Authorization: "Bearer refreshed-jwt" }, { Authorization: "Bearer export-jwt" },
    ]);
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  });

  it("restores a managed session and retrieves server-assigned workspace identity", async () => {
    const client = { getSession: vi.fn(async () => ({ data: { session: { id: "session-1" }, user: { id: "user-1" } } })), token: vi.fn(async () => ({ data: { token: "restored" } })) } as unknown as ManagedAuthClient;
    vi.stubGlobal("fetch", vi.fn(async () => json(identity)));
    const connect = vi.fn(); render(<PublicAuth client={client} connect={connect} />);
    await waitFor(() => expect(connect).toHaveBeenCalledWith(expect.any(Api), identity));
    expect(screen.queryByLabelText("Administrator token")).not.toBeInTheDocument();
  });

  it("handles signup, code verification, then sign-in without retaining the password", async () => {
    const session = { data: { session: { id: "session-1" }, user: { id: "user-1" } } };
    const getSession = vi.fn().mockResolvedValue({ data: null });
    const signup = vi.fn(async () => ({ data: { user: { emailVerified: false } } }));
    const signin = vi.fn(async () => { getSession.mockResolvedValue(session); return session; });
    const sendCode = vi.fn(async () => ({ data: {} })), verifyEmail = vi.fn(async () => ({ data: {} }));
    const client = { getSession, signUp: { email: signup }, signIn: { email: signin }, emailOtp: { sendVerificationOtp: sendCode, verifyEmail }, token: vi.fn(async () => ({ data: { token: "signed-in" } })) } as unknown as ManagedAuthClient;
    vi.stubGlobal("fetch", vi.fn(async () => json(identity)));
    const connect = vi.fn(), user = userEvent.setup(); render(<PublicAuth client={client} connect={connect} />);
    await user.click(await screen.findByRole("button", { name: "Create account" }));
    await user.type(screen.getByLabelText("Name"), "Reviewer");
    await user.type(screen.getByLabelText("Email"), "reviewer@example.org");
    await user.type(screen.getByLabelText("Password"), "test-password");
    await user.click(screen.getByRole("button", { name: "Create workspace" }));
    expect(await screen.findByRole("heading", { name: "Verify your email" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send verification code" }));
    expect(sendCode).toHaveBeenCalledWith({ email: "reviewer@example.org", type: "email-verification" });
    await user.type(screen.getByLabelText("Verification code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify email" }));
    expect(await screen.findByLabelText("Password")).toHaveValue("");
    expect(verifyEmail).toHaveBeenCalledWith({ email: "reviewer@example.org", otp: "123456" });
    await user.type(screen.getByLabelText("Password"), "test-password");
    await user.click(screen.getAllByRole("button", { name: "Sign in" }).at(-1)!);
    await waitFor(() => expect(connect).toHaveBeenCalledWith(expect.any(Api), identity));
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  });

  it("uses an explicit HTTPS API base and refuses token exposure to non-local HTTP", async () => {
    const fetcher = vi.fn(async () => json({})); vi.stubGlobal("fetch", fetcher);
    await new Api("secret", "https://api.example.org").get("/v1/me");
    expect(fetcher).toHaveBeenCalledWith("https://api.example.org/v1/me", expect.objectContaining({ credentials: "omit" }));
    await expect(new Api("secret", "http://untrusted.example.org").get("/v1/me")).rejects.toThrow("HTTPS");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("public beta boundaries", () => {
  it("bounds foreground operation checks and stops after two minutes", async () => {
    vi.useFakeTimers(); vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    const fetcher = vi.fn(async () => json({ id: "waiting", state: "queued" })); vi.stubGlobal("fetch", fetcher);
    render(<OperationStatus api={new Api("test")} operation={{ id: "waiting", state: "queued" }} refreshed={vi.fn()} clear={vi.fn()} openStorage={vi.fn()} beta />);
    await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
    const requests = fetcher.mock.calls.length;
    expect(requests).toBeLessThanOrEqual(9);
    expect(screen.getByText(/Automatic checks paused/)).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(600000); });
    expect(fetcher).toHaveBeenCalledTimes(requests);
  });

  it("requests exact-domain approval without exposing operator controls", async () => {
    const sent = vi.fn(async (path: string) => path === "/v1/approved-domains" ? json({ items: [{ domain: "catalog.example.org" }], next_cursor: null }) : json({ items: [], next_cursor: null }));
    vi.stubGlobal("fetch", sent);
    const user = userEvent.setup(); render(<DomainsView api={new Api("test")} epoch={0} />);
    expect(await screen.findByText("catalog.example.org")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Exact hostname"), "new.example.org");
    await user.type(screen.getByLabelText("Purpose and permission"), "Public catalog permitted for research");
    await user.click(screen.getByRole("button", { name: "Request domain approval" }));
    await waitFor(() => expect(sent).toHaveBeenCalledWith("/v1/domain-requests", expect.objectContaining({ method: "POST", body: JSON.stringify({ domain: "new.example.org", reason: "Public catalog permitted for research" }) })));
    expect(screen.queryByRole("button", { name: "Approve domain" })).not.toBeInTheDocument();
  });

  it("sends an audited, version-checked domain approval for an operator", async () => {
    const sent = vi.fn(async (path: string) => path === "/v1/approved-domains" ? json({ items: [], next_cursor: null }) : json({ items: [{ id: "request-1", version: 3, data: { domain: "catalog.example.org", state: "pending", reason: "Public catalog research" } }], next_cursor: null }));
    vi.stubGlobal("fetch", sent); const user = userEvent.setup(); render(<DomainsView api={new Api("test")} epoch={0} isOperator />);
    await user.type(await screen.findByLabelText("Operator decision reason for catalog.example.org"), "Permission and source scope reviewed");
    await user.click(screen.getByRole("button", { name: "Approve domain" }));
    await waitFor(() => expect(sent).toHaveBeenCalledWith("/v1/domain-requests/request-1/decision", expect.objectContaining({ method: "POST", body: JSON.stringify({ decision: "approve", reason: "Permission and source scope reviewed" }), headers: expect.objectContaining({ "If-Match": '"3"', "Idempotency-Key": expect.any(String) }) })));
  });

  it("sends beta source limits and omits credentials", async () => {
    const sent = vi.fn(async (path: string) => path === "/v1/approved-domains" ? json({ items: [{ domain: "catalog.example.org" }], next_cursor: null }) : json({ items: [], next_cursor: null }));
    vi.stubGlobal("fetch", sent);
    const user = userEvent.setup(); render(<ResourceView collection="sources" api={new Api("test")} epoch={0} refresh={vi.fn()} queued={vi.fn()} beta />);
    await user.click(screen.getByRole("button", { name: "Add source" }));
    await user.type(screen.getByLabelText("Display name"), "Public catalog");
    await user.type(screen.getByLabelText("Stable source ID"), "catalog");
    await user.type(screen.getByLabelText("Source URL"), "https://catalog.example.org/products");
    expect(screen.queryByLabelText("Credential reference (optional)")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save configuration" }));
    const post = sent.mock.calls.find(call => call[0] === "/v1/sources");
    expect(post).toBeDefined();
    const body = JSON.parse(String((post as unknown as [string, RequestInit])[1].body));
    expect(body.limits).toEqual({ max_pages: 10, max_records: 1000, max_bytes: 1000000, deadline_seconds: 60, request_timeout_seconds: 20 });
    expect(body).not.toHaveProperty("auth_ref");
  });

  it("rejects an unapproved source hostname before submission", async () => {
    const sent = vi.fn(async (path: string) => path === "/v1/approved-domains" ? json({ items: [{ domain: "catalog.example.org" }], next_cursor: null }) : json({ items: [], next_cursor: null }));
    vi.stubGlobal("fetch", sent); const user = userEvent.setup(); render(<ResourceView collection="sources" api={new Api("test")} epoch={0} refresh={vi.fn()} queued={vi.fn()} beta />);
    await user.click(screen.getByRole("button", { name: "Add source" }));
    await user.type(screen.getByLabelText("Display name"), "Unapproved"); await user.type(screen.getByLabelText("Stable source ID"), "unapproved");
    await user.type(screen.getByLabelText("Source URL"), "https://other.example.org/catalog");
    await user.click(screen.getByRole("button", { name: "Save configuration" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/approv/i);
    expect(sent.mock.calls.some(call => call[0] === "/v1/sources")).toBe(false);
  });

  it("hides capacity expansion and administrator-token rotation for public users", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => path === "/v1/storage" ? json({ used_bytes: 100, limit_bytes: 200 }) : json(capabilities)));
    render(<WorkspaceSettings api={new Api("test")} epoch={0} onTokenRotated={vi.fn()} isOperator={false} managedMode />);
    expect(await screen.findByText("200 bytes")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Expand capacity" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rotate administrator token" })).not.toBeInTheDocument();
  });

  it("renders new budget dimensions without a hard-coded metric list", async () => {
    const scope = { period: "2026-10", limits: { metadata_bytes: 2000 }, reserved: { metadata_bytes: 500 } };
    vi.stubGlobal("fetch", vi.fn(async () => json({ mode: "neon", workspace: scope, global: scope, accounting: "conservative_reservations_not_provider_billing" })));
    render(<BudgetView api={new Api("test")} epoch={0} />);
    const progress = await screen.findByRole("progressbar", { name: "workspace metadata bytes reserved" });
    expect(progress).toHaveAttribute("value", "500"); expect(progress).toHaveAttribute("max", "2000");
  });

  it("registers remote model metadata for a workspace user without fetching weights", async () => {
    const sent = vi.fn(async (path: string) => path === "/v1/models" ? json({ id: "setfit-v1" }) : json({ items: [], next_cursor: null }));
    vi.stubGlobal("fetch", sent); const user = userEvent.setup(); render(<LearningView api={new Api("test")} epoch={0} />);
    await user.click(screen.getByRole("button", { name: "Model registry" }));
    await user.selectOptions(screen.getByLabelText("Classifier family"), "setfit");
    const fields = {
      "Unique model version ID": "setfit-v1", "Saved taxonomy ID": "tools-v1", "Frozen training snapshot ID": "snapshot-1", "Immutable checkpoint revision": "revision-sha",
      "Remote artifact HTTPS URL": "https://huggingface.co/example/model/resolve/revision-sha/model.safetensors", "Artifact SHA-256": "a".repeat(64),
      "Calibration report SHA-256": "b".repeat(64), "Evaluation report SHA-256": "c".repeat(64), "Model license": "MIT",
    };
    for (const [label, value] of Object.entries(fields)) { await user.click(screen.getByLabelText(label)); await user.paste(value); }
    await user.click(screen.getByRole("button", { name: "Register model metadata" }));
    await waitFor(() => expect(sent).toHaveBeenCalledWith("/v1/models", expect.objectContaining({ method: "POST" })));
    const post = sent.mock.calls.find(call => call[0] === "/v1/models") as unknown as [string, RequestInit];
    expect(JSON.parse(String(post[1].body))).toMatchObject({ provider: "setfit", id: "setfit-v1", taxonomy_id: "tools-v1", dataset_id: "snapshot-1", checkpoint_revision: "revision-sha", artifact_hash: "a".repeat(64), calibration_hash: "b".repeat(64), evaluation_hash: "c".repeat(64) });
    expect(sent.mock.calls.every(call => call[0].startsWith("/v1/"))).toBe(true);
    expect(await screen.findByText(/No weights were downloaded or activated/)).toBeInTheDocument();
  });

  it("saves an AI-provenance correction bound to retained evidence and a published revision", async () => {
    const sent = vi.fn(async (path: string) => {
      if (path.startsWith("/v1/taxonomies")) return json({ items: [{ id: "tools-v1", data: { nodes: [{ id: "tools", label: "Tools" }, { id: "drill", label: "Drills", parent_id: "tools" }] } }], next_cursor: null });
      if (path.startsWith("/v1/revisions")) return json({ items: [{ id: "revision-1", data: { source_id: "catalog", run_id: "run-1" } }], next_cursor: null });
      if (path.startsWith("/v1/runs")) return json({ items: [{ id: "record-1", data: { name: "Cordless drill" }, evidence: [{ snapshot_hash: "capture-hash" }], errors: [] }], total: 1 });
      return json({ id: "label-1" });
    });
    vi.stubGlobal("fetch", sent); const user = userEvent.setup(), saved = vi.fn(); render(<LabelReview api={new Api("test")} saved={saved} />);
    await screen.findByRole("option", { name: "tools-v1" });
    await user.selectOptions(screen.getByLabelText("Taxonomy"), "tools-v1");
    await user.selectOptions(screen.getByLabelText("Published revision"), "revision-1");
    await user.selectOptions(await screen.findByLabelText("Retained record"), "record-1");
    expect(screen.getByRole("region", { name: "Label review evidence" })).toHaveTextContent("capture-hash");
    await user.selectOptions(screen.getByLabelText("Reviewed category"), "drill");
    await user.selectOptions(screen.getByLabelText("Review provenance"), "ai");
    await user.type(screen.getByLabelText("Evidence and reason"), "AI assisted; source title describes a drill");
    await user.click(screen.getByRole("button", { name: "Save reviewed label" }));
    await waitFor(() => expect(sent).toHaveBeenCalledWith("/v1/labels", expect.objectContaining({ body: JSON.stringify({ taxonomy_id: "tools-v1", record_id: "record-1", revision_id: "revision-1", label_id: "drill", provenance: "ai", reason: "AI assisted; source title describes a drill" }) })));
    expect(saved).toHaveBeenCalledTimes(1);
  });
});
