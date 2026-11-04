"use client";
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Alert, Button, TextField } from "@artify/ui";
import { useI18n } from "@/i18n";
import { bff, problemMessage } from "@/lib/client";
import { MfaEnrollment } from "./MfaEnrollment";
import type { Realm } from "@/lib/realm";

type Step = "credentials" | "mfa" | "enroll";

export function LoginForm({ realm }: { realm: Realm }) {
  const { t } = useI18n();
  const router = useRouter();
  const [step, setStep] = useState<Step>("credentials");
  const [mfaToken, setMfaToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const home = realm === "platform" ? "/platform" : "/dashboard";

  async function onCredentials(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData(e.currentTarget);
    try {
      const r = await bff<{ status: string; mfa_token?: string; mfa_enrollment_required: boolean }>(
        `/session/${realm}/login`,
        { body: { email: form.get("email"), password: form.get("password") } },
      );
      if (r.status === "mfa_required") {
        setMfaToken(r.mfa_token ?? "");
        setStep("mfa");
      } else if (r.mfa_enrollment_required) {
        setStep("enroll");
      } else {
        router.push(home);
      }
    } catch (err) {
      setError(problemMessage(err, t.errors));
    } finally {
      setBusy(false);
    }
  }

  async function onMfa(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData(e.currentTarget);
    try {
      await bff(`/session/${realm}/mfa`, { body: { mfa_token: mfaToken, code: form.get("code") } });
      router.push(home);
    } catch (err) {
      setError(problemMessage(err, t.errors));
    } finally {
      setBusy(false);
    }
  }

  if (step === "enroll") return <MfaEnrollment realm={realm} onDone={() => router.push(home)} />;

  return (
    <div className="grid gap-4">
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {step === "credentials" ? (
        <form onSubmit={onCredentials} className="grid gap-4" noValidate>
          <TextField name="email" type="email" label={t.email} autoComplete="username" required />
          <TextField name="password" type="password" label={t.password} autoComplete="current-password" required />
          <Button type="submit" disabled={busy}>
            {busy ? t.signingIn : t.signIn}
          </Button>
        </form>
      ) : (
        <form onSubmit={onMfa} className="grid gap-4" noValidate>
          <p className="a-hint">{t.mfaPrompt}</p>
          <TextField name="code" label={t.mfaCode} inputMode="text" autoComplete="one-time-code" required />
          <Button type="submit" disabled={busy}>
            {t.verify}
          </Button>
        </form>
      )}
    </div>
  );
}
