import { useState, type FormEvent } from "react";
import { Alert, Button, Panel } from "@artify/ui";
import type { AppInfo, Bridge, Identity } from "../bridge/types";
import { describe } from "../lib/errors";

export function Brand() {
  return (
    <div className="d-brand" aria-label="Artify Co-Accountant">
      <span className="d-brand__mark" aria-hidden="true">A</span>
      <span><strong>Artify</strong> Co-Accountant</span>
    </div>
  );
}

export function Login({ bridge, info, notice, onSignedIn }: { bridge: Bridge; info: AppInfo | null; notice: string | null; onSignedIn: (i: Identity) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfa, setMfa] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mfa === null) {
        const r = await bridge.authLogin(email.trim(), password);
        setPassword("");
        if (r.status === "ok") onSignedIn(r.identity);
        else if (r.status === "mfa_required") setMfa(r.mfa_token);
        else setError("Two-factor authentication must be set up before you can use the desktop app. Sign in to the Control Center in your browser to enrol an authenticator, then return here.");
      } else {
        onSignedIn(await bridge.authMfa(mfa, code.trim()));
      }
    } catch (ex) {
      setError(describe(ex));
      if (mfa !== null) setCode("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="d-login" id="main">
      <Panel className="d-login__card">
        <Brand />
        <h1>{mfa === null ? "Sign in" : "Two-factor authentication"}</h1>
        {notice ? <Alert tone="info">{notice}</Alert> : null}
        {info && !info.api_configured ? <Alert tone="warning">This build has no server address configured, so sign-in is not possible. Ask your administrator for a configured installer.</Alert> : null}
        <form onSubmit={submit} className="d-form" noValidate>
          {mfa === null ? (
            <>
              <div className="a-field"><label htmlFor="email">Email address</label><input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus /></div>
              <div className="a-field"><label htmlFor="password">Password</label><input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></div>
            </>
          ) : (
            <div className="a-field">
              <label htmlFor="code">Authenticator code</label>
              <input id="code" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} maxLength={32} required autoFocus />
              <span className="a-hint">Enter the 6-digit code from your authenticator app (or a recovery code).</span>
            </div>
          )}
          {error ? <Alert tone="danger">{error}</Alert> : null}
          <Button type="submit" disabled={busy || (mfa === null ? !email || !password : code.trim().length < 6) || (info !== null && !info.api_configured)}>{busy ? "Signing in…" : mfa === null ? "Sign in" : "Verify"}</Button>
          {mfa !== null ? <Button type="button" variant="secondary" onClick={() => { setMfa(null); setCode(""); setError(null); }}>Back</Button> : null}
        </form>
        <p className="a-hint">Your session is kept in the Windows credential store, never as a password on disk.</p>
      </Panel>
    </main>
  );
}

export function Booting({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  return (
    <main className="d-login" id="main">
      <Panel className="d-login__card">
        <Brand />
        {error ? (
          <>
            <Alert tone="warning">{error}</Alert>
            <p className="a-hint">Your saved session was kept. Check your connection and try again.</p>
            <Button onClick={onRetry}>Try again</Button>
          </>
        ) : <p role="status">Starting…</p>}
      </Panel>
    </main>
  );
}
