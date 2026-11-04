"use client";
import Link from "next/link";
import { AuthShell } from "@/components/AuthShell";
import { LoginForm } from "@/components/LoginForm";
import { useI18n } from "@/i18n";

export default function LoginPage() {
  const { t } = useI18n();
  return (
    <AuthShell title={t.loginTitle}>
      <LoginForm realm="tenant" />
      <p className="a-hint mt-4">
        {t.noAccount} <Link className="a-link" href="/signup">{t.signUp}</Link>
      </p>
    </AuthShell>
  );
}
