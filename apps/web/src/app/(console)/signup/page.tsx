"use client";
import { AuthShell } from "@/components/AuthShell";
import { SignupForm } from "@/components/SignupForm";
import { useI18n } from "@/i18n";

export default function SignupPage() {
  const { t } = useI18n();
  return (
    <AuthShell title={t.signupTitle}>
      <SignupForm />
    </AuthShell>
  );
}
