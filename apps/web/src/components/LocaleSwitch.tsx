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
      lang={next}
      onClick={() => {
        document.cookie = `artify_locale=${next}; path=/; max-age=31536000; samesite=lax`;
        router.refresh();
      }}
    >
      {/* the accessible name must contain the visible text (WCAG 2.5.3 Label in Name) */}
      <span>{next === "ar" ? "العربية" : "English"}</span>
      <span className="a-visually-hidden"> ({t.language})</span>
    </button>
  );
}
