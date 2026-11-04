"use client";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { Alert, Button, TextField } from "@artify/ui";
import { useI18n } from "@/i18n";
import { ApiProblem, bff, problemMessage } from "@/lib/client";

export function SignupForm() {
  const { t } = useI18n();
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setFieldErrors({});
    const f = new FormData(e.currentTarget);
    try {
      await bff("/bff/auth/signup", {
        body: {
          email: f.get("email"),
          password: f.get("password"),
          display_name: f.get("display_name"),
          organization_name: f.get("organization_name"),
          organization_type: f.get("organization_type"),
          country: String(f.get("country") ?? "").toUpperCase(),
          default_currency: String(f.get("default_currency") ?? "").toUpperCase(),
          locale: document.documentElement.lang === "ar" ? "ar" : "en",
        },
      });
      setDone(true);
    } catch (err) {
      if (err instanceof ApiProblem && err.problem.errors) {
        setFieldErrors(Object.fromEntries(err.problem.errors.map((x) => [String(x.loc.at(-1)), x.msg])));
      }
      setError(problemMessage(err, t.errors));
    } finally {
      setBusy(false);
    }
  }

  if (done) return <Alert tone="success">{t.signupDone}</Alert>;
  return (
    <form onSubmit={onSubmit} className="grid gap-4" noValidate>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <TextField name="display_name" label={t.displayName} autoComplete="name" required error={fieldErrors.display_name} />
      <TextField name="email" type="email" label={t.email} autoComplete="username" required error={fieldErrors.email} />
      <TextField name="password" type="password" label={t.password} hint={t.passwordHint} autoComplete="new-password" required error={fieldErrors.password} />
      <TextField name="organization_name" label={t.organizationName} required error={fieldErrors.organization_name} />
      <div className="a-field">
        <label htmlFor="organization_type">{t.organizationType}</label>
        <select id="organization_type" name="organization_type" defaultValue="accounting_firm">
          <option value="accounting_firm">{t.typeFirm}</option>
          <option value="independent_accountant">{t.typeIndependent}</option>
        </select>
      </div>
      <TextField name="country" label={t.country} maxLength={2} defaultValue="OM" required error={fieldErrors.country} />
      <TextField name="default_currency" label={t.currency} maxLength={3} defaultValue="OMR" required error={fieldErrors.default_currency} />
      <Button type="submit" disabled={busy}>
        {busy ? t.creating : t.signUp}
      </Button>
      <p className="a-hint">
        {t.haveAccount} <Link className="a-link" href="/login">{t.signIn}</Link>
      </p>
    </form>
  );
}
