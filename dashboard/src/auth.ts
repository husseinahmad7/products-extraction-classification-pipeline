import { createAuthClient } from "@neondatabase/auth";
import { BetterAuthReactAdapter } from "@neondatabase/auth/react/adapters";
import { Api, ApiError } from "./api";

export const neonAuthUrl = (import.meta.env.VITE_NEON_AUTH_URL || "").trim();

export function createManagedAuthClient(url: string) {
  const parsed = new URL(url);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname);
  if ((parsed.protocol !== "https:" && !(local && parsed.protocol === "http:")) || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("Configure a valid HTTPS Neon Auth URL.");
  }
  return createAuthClient(url, { adapter: BetterAuthReactAdapter() });
}

export type ManagedAuthClient = ReturnType<typeof createManagedAuthClient>;

export function managedApi(client: ManagedAuthClient) {
  return new Api(async () => {
    const { data, error } = await client.token();
    if (error || !data?.token) throw new ApiError("Your session has ended. Sign in again.", 401);
    return data.token;
  });
}
