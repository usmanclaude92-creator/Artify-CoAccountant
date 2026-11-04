"use client";
import { useState, type FormEvent } from "react";
import { Alert, Button, TextField } from "@artify/ui";
import { useI18n } from "@/i18n";
import { bff, problemMessage } from "@/lib/client";
import { authPrefix, type Realm } from "@/lib/realm";

export function MfaEnrollment({ realm, onDone }: { realm: Realm; onDone: () => void }) {
  const { t } = useI18n();
  const [secret, setSecret] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      setSecret(await bff(`${authPrefix(realm)}/mfa/enroll`, { method: "POST", body: {} }));
    } catch (err) {
      setError(problemMessage(err, t.errors));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData(e.currentTarget);
    try {
      const r = await bff<{ recovery_codes: string[] }>(`/session/${realm}/confirm-mfa`, { body: { code: form.get("code") } });
      setCodes(r.recovery_codes);
    } catch (err) {
      setError(problemMessage(err, t.errors));
    } finally {
      setBusy(false);
    }
  }

  if (codes) {
    return (
      <div className="grid gap-4">
        <h2 className="font-semibold">{t.recoveryTitle}</h2>
        <p className="a-hint">{t.recoveryBody}</p>
        <ul className="grid grid-cols-2 gap-2 font-mono text-sm" data-testid="recovery-codes" dir="ltr">
          {codes.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
        <Button onClick={onDone}>{t.continue}</Button>
      </div>
    );
  }
  return (
    <div className="grid gap-4">
      <p className="a-hint">{t.enrollIntro}</p>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {!secret ? (
        <Button onClick={start} disabled={busy}>
          {t.enrollStart}
        </Button>
      ) : (
        <form onSubmit={confirm} className="grid gap-4" noValidate>
          <p className="a-hint">{t.enrollSecret}</p>
          <code className="break-all rounded bg-[var(--color-surface-2)] p-2 text-sm" data-testid="mfa-secret" dir="ltr">
            {secret.secret}
          </code>
          <TextField name="code" label={t.mfaCode} inputMode="numeric" autoComplete="one-time-code" required />
          <Button type="submit" disabled={busy}>
            {t.enrollConfirm}
          </Button>
        </form>
      )}
    </div>
  );
}
