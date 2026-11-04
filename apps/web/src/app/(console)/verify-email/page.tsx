"use client";
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Alert } from "@artify/ui";
import { AuthShell } from "@/components/AuthShell";
import { useI18n } from "@/i18n";
import { bff } from "@/lib/client";

function Verify() {
  const { t } = useI18n();
  const token = useSearchParams().get("token");
  const [state, setState] = useState<"working" | "ok" | "failed">(token ? "working" : "failed");
  useEffect(() => {
    if (!token) return;
    bff("/bff/auth/verify-email", { body: { token } })
      .then(() => setState("ok"))
      .catch(() => setState("failed"));
  }, [token]);
  return state === "working" ? (
    <p>{t.verifying}</p>
  ) : state === "ok" ? (
    <div className="grid gap-4">
      <Alert tone="success">{t.verified}</Alert>
      <Link className="a-link" href="/login">{t.signIn}</Link>
    </div>
  ) : (
    <Alert tone="danger">{t.verifyFailed}</Alert>
  );
}

export default function VerifyEmailPage() {
  const { t } = useI18n();
  return (
    <AuthShell title={t.signUp}>
      <Suspense fallback={<p>{t.loading}</p>}>
        <Verify />
      </Suspense>
    </AuthShell>
  );
}
