"use client";
import { AuthShell } from "@/components/AuthShell";
import { LoginForm } from "@/components/LoginForm";
import { useI18n } from "@/i18n";

export default function PlatformLoginPage() {
  const { t } = useI18n();
  return (
    <AuthShell title={t.platformLoginTitle} platform>
      <LoginForm realm="platform" />
    </AuthShell>
  );
}
