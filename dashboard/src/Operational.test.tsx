import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Api } from "./api";
import { OperationsView } from "./OperationsView";
import { WorkspaceSettings } from "./WorkspaceSettings";
import { ResourceView } from "./App";
import { SchedulesView } from "./SchedulesView";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });

describe("operator controls", () => {
  it("expands retained evidence capacity with an audit reason and switches to a rotated token", async () => {
    const rotated = vi.fn();
    const calls: { path: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
      calls.push({ path, body: init?.body && JSON.parse(String(init.body)) });
      if (path === "/v1/storage") return json({ used_bytes: 100, limit_bytes: 200 });
      if (path === "/v1/capabilities") return json({ acquisition: { live: ["html", "json"], captured_only: ["browser", "pdf"] }, navigation: ["html_next"], scheduler: false, classification: { state: "not_certified", automatic_promotion: false } });
      if (path === "/v1/storage/expand") return json({ used_bytes: 100, limit_bytes: 300 });
      if (path === "/v1/admin-tokens/rotate") return json({ token: "replacement-secret" });
      throw new Error(`Unexpected request: ${path}`);
    }));
    const user = userEvent.setup();
    render(<WorkspaceSettings api={new Api("old-token")} epoch={0} onTokenRotated={rotated} />);
    expect(await screen.findByText("200 bytes")).toBeInTheDocument();
    expect(screen.getByText(/Live acquisition:/)).toHaveTextContent("html, json");
    expect(screen.getByRole("button", { name: "Expand capacity" })).toBeDisabled();
    await user.clear(screen.getByLabelText("New total capacity (bytes)"));
    await user.type(screen.getByLabelText("New total capacity (bytes)"), "300");
    await user.type(screen.getByLabelText("Reason for expansion"), "More retained captures");
    await user.click(screen.getByRole("button", { name: "Expand capacity" }));
    expect(await screen.findByText(/Capacity expanded to 300 bytes/)).toBeInTheDocument();
    expect(calls.find(call => call.path === "/v1/storage/expand")?.body).toEqual({ limit_bytes: 300, reason: "More retained captures" });
    await user.click(screen.getByRole("button", { name: "Rotate administrator token" }));
    expect(screen.getByText(/Other clients using the current token will lose access in five minutes/)).toBeInTheDocument();
    expect(calls.filter(call => call.path === "/v1/admin-tokens/rotate")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Cancel rotation" }));
    expect(calls.filter(call => call.path === "/v1/admin-tokens/rotate")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Rotate administrator token" }));
    await user.click(screen.getByRole("button", { name: "Confirm token rotation" }));
    expect(await screen.findByLabelText("New administrator token")).toHaveValue("replacement-secret");
    expect(rotated).toHaveBeenCalledWith("replacement-secret");
  });

  it("finds a paused operation and resumes it after capacity is available", async () => {
    let resumed = false;
    const sent = vi.fn(async (path: string, init?: RequestInit) => {
      const operation = { id: "op-1", kind: "run", state: resumed ? "queued" : "paused", attempts: 1, generation: 2, created: "2026-09-28T10:00:00Z", error: resumed ? null : { detail: "artifact quota reached" } };
      if (path === "/v1/operations?limit=25&cursor=") return json({ items: [operation], next_cursor: null });
      if (path === "/v1/operations/op-1" && (!init?.method || init.method === "GET")) return json(operation);
      if (path === "/v1/operations/op-1/resume") { resumed = true; return json({ id: "op-1", state: "queued" }); }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", sent);
    const user = userEvent.setup();
    render(<OperationsView api={new Api("test")} epoch={0} openStorage={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /op-1/ }));
    expect(await screen.findByText("artifact quota reached")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Resume operation" }));
    await waitFor(() => expect(sent).toHaveBeenCalledWith("/v1/operations/op-1/resume", expect.objectContaining({ method: "POST" })));
    expect(await screen.findByRole("button", { name: "Cancel operation" })).toBeInTheDocument();
  });

  it("requires a readable saved diff before a removal review can be approved", async () => {
    const row = { id: "review-1", kind: "review", version: 1, created: "2026-09-28T10:00:00", data: { state: "open", run_id: "run-1", diff: { added: [], removed: ["sku-7"], changed: [] } } };
    vi.stubGlobal("fetch", vi.fn(async () => json({ items: [row], next_cursor: null })));
    const user = userEvent.setup();
    render(<ResourceView collection="reviews" api={new Api("test")} epoch={0} refresh={vi.fn()} queued={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /review-1/ }));
    expect(screen.getByRole("region", { name: "Dataset changes" })).toHaveTextContent("sku-7");
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    await user.type(screen.getByLabelText("Decision reason"), "Verified removal against source");
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("creates a schedule and sends its version when pausing it", async () => {
    let schedule: Record<string, unknown> | null = null;
    const sent = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/v1/schedules?limit=25&cursor=") return json({ items: schedule ? [schedule] : [], next_cursor: null });
      if (path === "/v1/schedules" && init?.method === "POST") {
        schedule = { id: "schedule-1", source_id: "source-1", recipe_id: "recipe-1", interval_seconds: 3600, next_due: "2026-09-28T11:00:00Z", state: "active", version: 1, last_run_id: null, created: "2026-09-28T10:00:00Z" };
        return json(schedule);
      }
      if (path === "/v1/schedules/schedule-1" && (!init?.method || init.method === "GET")) return json(schedule);
      if (path === "/v1/schedules/schedule-1/pause") {
        schedule = { ...schedule, state: "paused", version: 2 };
        return json(schedule);
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", sent);
    const user = userEvent.setup();
    render(<SchedulesView api={new Api("test")} epoch={0} />);
    await user.type(screen.getByLabelText("Active recipe ID"), "recipe-1");
    await user.click(screen.getByRole("button", { name: "Create schedule" }));
    expect(await screen.findByText(/Schedule created/)).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Pause schedule" }));
    await waitFor(() => expect(sent).toHaveBeenCalledWith("/v1/schedules/schedule-1/pause", expect.objectContaining({ headers: expect.objectContaining({ "If-Match": '"1"' }) })));
    expect(await screen.findByRole("button", { name: "Resume schedule" })).toBeInTheDocument();
  });

  it("replaces a failed schedule detail load with an error and retries it", async () => {
    const schedule = { id: "schedule-2", source_id: "source-1", recipe_id: "recipe-1", interval_seconds: 3600, next_due: "2026-09-28T11:00:00Z", state: "active", version: 1, last_run_id: null, created: "2026-09-28T10:00:00Z" };
    let detailReads = 0;
    vi.stubGlobal("fetch", vi.fn(async (path: string) => {
      if (path === "/v1/schedules?limit=25&cursor=") return json({ items: [schedule], next_cursor: null });
      if (path === "/v1/schedules/schedule-2") {
        detailReads += 1;
        return detailReads === 1 ? new Response(JSON.stringify({ detail: "temporary outage" }), { status: 503 }) : json(schedule);
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    const user = userEvent.setup();
    render(<SchedulesView api={new Api("test")} epoch={0} />);
    await user.click(await screen.findByRole("button", { name: /schedule-2/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("temporary outage");
    expect(screen.queryByText("Loading schedule…")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause schedule" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry schedule detail" }));
    expect(await screen.findByRole("button", { name: "Pause schedule" })).toBeInTheDocument();
    expect(detailReads).toBe(2);
  });
});
