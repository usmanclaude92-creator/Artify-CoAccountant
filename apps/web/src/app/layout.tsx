import type { Metadata } from "next";
import type { ReactNode } from "react";
import { I18nProvider } from "@/i18n";
import { getLocale } from "@/i18n/server";
import { LOCALES } from "@/i18n/messages";
import "./globals.css";

export const metadata: Metadata = { title: "Artify Co-Accountant", description: "Accounting operations platform" };

export default async function RootLayout({ children }: { children: ReactNode }) {
  const locale = await getLocale();
  return (
    <html lang={locale} dir={locale === "ar" ? "rtl" : "ltr"}>
      <body>
        <I18nProvider locale={locale}>
          <a href="#main" className="a-visually-hidden">
            {LOCALES[locale].continue}
          </a>
          {children}
        </I18nProvider>
      </body>
    </html>
  );
}
