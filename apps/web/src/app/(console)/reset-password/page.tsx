"use client";
import Link from "next/link";
import { Suspense, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { Alert, Button, TextField } from "@artify/ui";
import { AuthShell } from "@/components/AuthShell";
import { useI18n } from "@/i18n";
import { ApiProblem, bff, problemMessage } from "@/lib/client";

function ResetForm() {
  const { t } = useI18n();
  const token = useSearchParams().get("token") ?? "";
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const password = new FormData(e.currentTarget).get("new_password");
    try {
      await bff("/bff/auth/password-reset/confirm", { body: { token, new_password: password } });
      setDone(true);
    } catch (err) {
      // 422 covers both a bad/expired link and a rejected password: show the server's explanation.
      setError(err instanceof ApiProblem && err.status === 422 ? (err.problem.detail ?? t.resetFailed) : problemMessage(err, t.errors));
    } finally {
      setBusy(false);
    }
  }

  if (!token) return <Alert tone="danger">{t.resetFailed}</Alert>;
  if (done)
    return (
      <div className="grid gap-4">
        <Alert tone="success">{t.resetDone}</Alert>
        <Link className="a-link" href="/login">
          {t.signIn}
        </Link>
      </div>
    );
  return (
    <form onSubmit={onSubmit} className="grid gap-4" noValidate>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <TextField name="new_password" type="password" label={t.newPassword} hint={t.passwordHint} autoComplete="new-password" required />
      <Button type="submit" disabled={busy}>
        {t.saveNewPassword}
      </Button>
    </form>
  );
}

export default function ResetPasswordPage() {
  const { t } = useI18n();
  return (
    <AuthShell title={t.resetTitle}>
      <Suspense fallback={<p>{t.loading}</p>}>
        <ResetForm />
      </Suspense>
    </AuthShell>
  );
}
