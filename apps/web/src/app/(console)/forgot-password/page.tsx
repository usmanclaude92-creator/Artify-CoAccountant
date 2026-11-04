"use client";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { Alert, Button, TextField } from "@artify/ui";
import { AuthShell } from "@/components/AuthShell";
import { useI18n } from "@/i18n";
import { bff, problemMessage } from "@/lib/client";

export default function ForgotPasswordPage() {
  const { t } = useI18n();
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const email = new FormData(e.currentTarget).get("email");
    try {
      await bff("/bff/auth/password-reset/request", { body: { email } });
      setDone(true);
    } catch (err) {
      setError(problemMessage(err, t.errors));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title={t.forgotTitle}>
      {done ? (
        <Alert tone="success">{t.resetSent}</Alert>
      ) : (
        <form onSubmit={onSubmit} className="grid gap-4" noValidate>
          {error ? <Alert tone="danger">{error}</Alert> : null}
          <TextField name="email" type="email" label={t.email} autoComplete="username" required />
          <Button type="submit" disabled={busy}>
            {t.sendReset}
          </Button>
        </form>
      )}
      <p className="a-hint mt-4">
        <Link className="a-link" href="/login">
          {t.signIn}
        </Link>
      </p>
    </AuthShell>
  );
}
