import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { FileCheck2 } from "lucide-react";
import type { Api, WorkspaceIdentity } from "./api";
import { managedApi } from "./auth";
import type { ManagedAuthClient } from "./auth";

const errorText = (error: unknown) => error instanceof Error ? error.message : "Authentication failed. Try again.";
type SessionResult = Awaited<ReturnType<ManagedAuthClient["getSession"]>>;

export function PublicAuth({ client, connect }: { client: ManagedAuthClient; connect: (api: Api, identity: WorkspaceIdentity) => void }) {
  const [restoring, setRestoring] = useState(true), [signup, setSignup] = useState(false);
  const [email, setEmail] = useState(""), [password, setPassword] = useState(""), [name, setName] = useState("");
  const [otp, setOtp] = useState(""), [verifying, setVerifying] = useState(false);
  const [error, setError] = useState(""), [message, setMessage] = useState(""), [busy, setBusy] = useState(false);

  async function restore() {
    const result = await client.getSession();
    if (result.error) throw new Error(result.error.message || "Could not restore your session.");
    if (!result.data?.user || !result.data.session) return false;
    const api = managedApi(client);
    const identity = await api.get<WorkspaceIdentity>("/v1/me");
    connect(api, identity);
    return true;
  }

  useEffect(() => {
    let alive = true;
    void client.getSession().then(async (result: SessionResult) => {
      if (result.error) throw new Error(result.error.message || "Could not restore your session.");
      if (!result.data?.session || !result.data.user) return;
      const api = managedApi(client);
      const identity = await api.get<WorkspaceIdentity>("/v1/me");
      if (alive) connect(api, identity);
    }).catch((error: unknown) => { if (alive) setError(errorText(error)); }).finally(() => { if (alive) setRestoring(false); });
    return () => { alive = false; };
  }, [client, connect]);

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setMessage("");
    try {
      const result = signup
        ? await client.signUp.email({ email: email.trim(), password, name: name.trim() })
        : await client.signIn.email({ email: email.trim(), password });
      setPassword("");
      if (result.error) {
        if (result.error.code === "EMAIL_NOT_VERIFIED") setVerifying(true);
        throw new Error(result.error.message || "Could not sign in.");
      }
      if (!await restore()) {
        setVerifying(true);
        setMessage("Check your email for a verification code. If no code arrives, use Send verification code.");
      }
    } catch (error) { setPassword(""); setError(errorText(error)); }
    finally { setBusy(false); }
  }

  async function sendCode() {
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await client.emailOtp.sendVerificationOtp({ email: email.trim(), type: "email-verification" });
      if (result.error) throw new Error(result.error.message || "Could not send a verification code.");
      setMessage("If this account needs verification, a code has been sent. Check your inbox and spam folder.");
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }

  async function verify(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setMessage("");
    try {
      const result = await client.emailOtp.verifyEmail({ email: email.trim(), otp: otp.trim() });
      if (result.error) throw new Error(result.error.message || "The verification code was not accepted.");
      setOtp(""); setVerifying(false); setSignup(false);
      if (!await restore()) setMessage("Email verified. Sign in to open your workspace.");
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }

  return <main className="connect">
    <div className="brand"><FileCheck2 size={26} aria-hidden="true" />Evidence Pipeline</div>
    <h1>Your sources. Your evidence.</h1>
    <p>A personal workspace for declarative extraction and audited review. Public beta runs stay inside approved domains and resource limits.</p>
    {restoring ? <p role="status">Restoring your session…</p> : <>
      <nav className="tabs" aria-label="Account access"><button aria-current={!signup && !verifying ? "page" : undefined} onClick={() => { setSignup(false); setVerifying(false); setError(""); setPassword(""); }}>Sign in</button><button aria-current={signup && !verifying ? "page" : undefined} onClick={() => { setSignup(true); setVerifying(false); setError(""); setPassword(""); }}>Create account</button></nav>
      {verifying ? <form onSubmit={verify}>
        <h2>Verify your email</h2>
        <label htmlFor="verification-email">Email</label><input id="verification-email" type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} required />
        <label htmlFor="verification-code">Verification code</label><input id="verification-code" inputMode="numeric" autoComplete="one-time-code" value={otp} onChange={event => setOtp(event.target.value)} required maxLength={32} />
        <div className="actions"><button className="primary" disabled={busy || !otp.trim()}>{busy ? "Verifying…" : "Verify email"}</button><button type="button" disabled={busy || !email.trim()} onClick={() => void sendCode()}>Send verification code</button></div>
      </form> : <form onSubmit={submit}>
        <h2>{signup ? "Create your workspace" : "Open your workspace"}</h2>
        {signup && <><label htmlFor="account-name">Name</label><input id="account-name" autoComplete="name" value={name} onChange={event => setName(event.target.value)} required maxLength={200} /></>}
        <label htmlFor="account-email">Email</label><input id="account-email" type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} required />
        <label htmlFor="account-password">Password</label><input id="account-password" type="password" autoComplete={signup ? "new-password" : "current-password"} value={password} onChange={event => setPassword(event.target.value)} minLength={8} required />
        <p className="hint">Your password is sent only to Neon Auth. Bearer tokens are obtained for each API request, never saved in browser storage.</p>
        <button className="primary" disabled={busy}>{busy ? "Connecting…" : signup ? "Create workspace" : "Sign in"}</button>
        <button type="button" className="auth-secondary" disabled={busy} onClick={() => { setVerifying(true); setPassword(""); setError(""); }}>Verify email with a code</button>
      </form>}
    </>}
    {error && <div className="notice error" role="alert">{error}</div>}
    {message && <p className="notice success" role="status">{message}</p>}
    <p className="release-note">Public beta · English first · reviewed labels stay separate from AI suggestions · no automatic model promotion.</p>
  </main>;
}
