"use client";
import { useRouter } from "next/navigation";
import { useI18n } from "@/i18n";

export function LocaleSwitch() {
  const { locale, t } = useI18n();
  const router = useRouter();
  const next = locale === "en" ? "ar" : "en";
  return (
    <button
      type="button"
      className="a-link bg-transparent"
      aria-label={t.language}
      onClick={() => {
        document.cookie = `artify_locale=${next}; path=/; max-age=31536000; samesite=lax`;
        router.refresh();
      }}
    >
      {next === "ar" ? "العربية" : "English"}
    </button>
  );
}
