import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App, { ResourceView } from "./App";
import { Api } from "./api";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("workspace", () => {
  it("connects, shows real empty state and clears the session on disconnect", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 })));
    // Each call needs an unread response body.
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 })));
    render(<App />); const user = userEvent.setup();
    expect(screen.getByRole("heading", { name: "Open your workspace" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Administrator token"), "test-only-token");
    await user.click(screen.getByRole("button", { name: /Connect to workspace/ }));
    expect(await screen.findByRole("heading", { name: "No sources yet" })).toBeInTheDocument();
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(screen.getByLabelText("Administrator token")).toHaveValue("");
  });
  it("shows authentication failure without claiming to be connected", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Invalid administrator token" }), { status: 401 })));
    render(<App />); const user = userEvent.setup();
    await user.type(screen.getByLabelText("Administrator token"), "invalid");
    await user.click(screen.getByRole("button", { name: /Connect to workspace/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid administrator token");
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });
});
describe("idempotent client", () => {
  it("retains the key after an ambiguous network failure", async () => {
    const send = vi.fn().mockRejectedValueOnce(new TypeError("network")).mockResolvedValueOnce(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", send); const api = new Api("memory-only");
    await expect(api.post("/v1/runs", { recipe_id: "r1" })).rejects.toThrow("network");
    await api.post("/v1/runs", { recipe_id: "r1" });
    expect(send.mock.calls[0][1].headers["Idempotency-Key"]).toBe(send.mock.calls[1][1].headers["Idempotency-Key"]);
    expect(send.mock.calls[1][1].credentials).toBe("omit");
  });
});

it("preserves an unsaved decision reason when the list refreshes", async () => {
  const row = { id: "recipe-1", kind: "recipe", version: 1, created: "2026-09-26T10:00:00", data: { source_id: "sample-source", state: "review_required", validation: { valid: true } } };
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ items: [row], next_cursor: null }), { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  const api = new Api("test"), refresh = vi.fn(), queued = vi.fn(), user = userEvent.setup();
  const view = render(<ResourceView collection="recipes" api={api} epoch={0} refresh={refresh} queued={queued} />);
  await user.click(await screen.findByRole("button", { name: /sample-source/ }));
  await user.type(screen.getByLabelText("Decision reason"), "My evidence review is still in progress");
  view.rerender(<ResourceView collection="recipes" api={api} epoch={1} refresh={refresh} queued={queued} />);
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText("Decision reason")).toHaveValue("My evidence review is still in progress");
});

it("prevents duplicate cursor navigation while a page is loading", async () => {
  let complete!: (response: Response) => void;
  const first = { items: [], next_cursor: "page-two" };
  const second = { items: [], next_cursor: "page-three" };
  const fetcher = vi.fn()
    .mockImplementationOnce(async () => new Response(JSON.stringify(first)))
    .mockImplementationOnce(() => new Promise<Response>(resolve => { complete = resolve; }))
    .mockImplementationOnce(async () => new Response(JSON.stringify(first)));
  vi.stubGlobal("fetch", fetcher);
  const user = userEvent.setup();
  render(<ResourceView collection="sources" api={new Api("test")} epoch={0} refresh={vi.fn()} queued={vi.fn()} />);
  await user.click(await screen.findByRole("button", { name: "Next" }));
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Next" }));
  expect(fetcher).toHaveBeenCalledTimes(2);
  await act(async () => complete(new Response(JSON.stringify(second))));
  await user.click(screen.getByRole("button", { name: "Previous" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  expect(String(fetcher.mock.calls[2][0])).toBe("/v1/sources?limit=25&cursor=");
});
