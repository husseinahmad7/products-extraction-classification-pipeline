import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const managed = vi.hoisted(() => ({ getSession: vi.fn(), token: vi.fn(), signOut: vi.fn() }));
vi.mock("./auth", async importOriginal => ({
  ...await importOriginal<typeof import("./auth")>(),
  neonAuthUrl: "https://example.neonauth.example/auth",
  createManagedAuthClient: () => managed,
}));
import App from "./App";

const json = (value: unknown) => new Response(JSON.stringify(value));
beforeEach(() => {
  managed.getSession.mockReset().mockResolvedValue({ data: { session: { id: "session" }, user: { id: "user" } } });
  managed.token.mockReset().mockResolvedValue({ data: { token: "managed-jwt" } });
  managed.signOut.mockReset().mockImplementation(async () => { managed.getSession.mockResolvedValue({ data: null }); return { data: {} }; });
  vi.stubGlobal("fetch", vi.fn(async (path: string) => {
    if (path === "/v1/me") return json({ actor: "user", workspace: "personal-user", is_operator: false, auth_mode: "neon" });
    return json({ items: [], next_cursor: null });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("signs out through Managed Auth and returns to public sign-in without an admin bypass", async () => {
  const user = userEvent.setup(); render(<App />);
  expect(await screen.findByRole("heading", { name: "No sources yet" })).toBeInTheDocument();
  expect(screen.getByText(/personal-user \/ Sources/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Sign out" }));
  expect(await screen.findByLabelText("Email")).toBeInTheDocument();
  expect(managed.signOut).toHaveBeenCalledTimes(1);
  expect(screen.queryByLabelText("Administrator token")).not.toBeInTheDocument();
});

it("reports sign-out errors instead of falsely claiming the managed session ended", async () => {
  managed.signOut.mockResolvedValue({ error: { message: "Auth endpoint temporarily unavailable" } });
  const user = userEvent.setup(); render(<App />);
  await user.click(await screen.findByRole("button", { name: "Sign out" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Auth endpoint temporarily unavailable");
  expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
});
