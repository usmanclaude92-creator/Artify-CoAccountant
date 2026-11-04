"use client";
import Link from "next/link";
import { Suspense, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { Alert, Button, TextField } from "@artify/ui";
import { AuthShell } from "@/components/AuthShell";
import { useI18n } from "@/i18n";
import { ApiProblem, bff, problemMessage } from "@/lib/client";

function AcceptForm() {
  const { t } = useI18n();
  const token = useSearchParams().get("token") ?? "";
  const [done, setDone] = useState<"accepted" | "declined" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const f = new FormData(e.currentTarget);
    try {
      // The BFF attaches the session cookie when signed in, which existing users need.
      await bff("/bff/auth/invitations/accept", {
        body: { token, display_name: f.get("display_name") || null, password: f.get("password") || null },
      });
      setDone("accepted");
    } catch (err) {
      setError(err instanceof ApiProblem && [401, 402, 422].includes(err.status) ? (err.problem.detail ?? t.errors.generic ?? "") : problemMessage(err, t.errors));
    } finally {
      setBusy(false);
    }
  }

  if (!token) return <Alert tone="danger">{t.resetFailed}</Alert>;
  async function decline() {
    setBusy(true);
    setError(null);
    try {
      await bff("/bff/auth/invitations/decline", { body: { token } });
      setDone("declined");
    } catch (err) {
      setError(problemMessage(err, t.errors));
    } finally {
      setBusy(false);
    }
  }

  if (done)
    return (
      <div className="grid gap-4">
        <Alert tone="success">{done === "declined" ? t.declineDone : t.acceptDone}</Alert>
        <Link className="a-link" href="/login">
          {t.signIn}
        </Link>
      </div>
    );
  return (
    <form onSubmit={onSubmit} className="grid gap-4" noValidate>
      <p className="a-hint">{t.acceptIntro}</p>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <TextField name="display_name" label={t.displayName} autoComplete="name" />
      <TextField name="password" type="password" label={t.password} hint={t.passwordHint} autoComplete="new-password" />
      <Button type="submit" disabled={busy}>
        {t.acceptBtn}
      </Button>
      <Button type="button" variant="secondary" disabled={busy} onClick={decline}>
        {t.declineBtn}
      </Button>
      <Link className="a-link" href="/login">
        {t.signIn}
      </Link>
    </form>
  );
}

export default function AcceptInvitationPage() {
  const { t } = useI18n();
  return (
    <AuthShell title={t.acceptTitle}>
      <Suspense fallback={<p>{t.loading}</p>}>
        <AcceptForm />
      </Suspense>
    </AuthShell>
  );
}
