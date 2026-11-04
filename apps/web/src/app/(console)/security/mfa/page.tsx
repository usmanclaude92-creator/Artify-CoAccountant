"use client";
import { useRouter } from "next/navigation";
import { AuthShell } from "@/components/AuthShell";
import { MfaEnrollment } from "@/components/MfaEnrollment";
import { useI18n } from "@/i18n";

export default function MfaPage() {
  const { t } = useI18n();
  const router = useRouter();
  return (
    <AuthShell title={t.enrollTitle}>
      <MfaEnrollment realm="tenant" onDone={() => router.push("/dashboard")} />
    </AuthShell>
  );
}
