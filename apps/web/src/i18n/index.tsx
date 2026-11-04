"use client";
import { createContext, useContext, type ReactNode } from "react";
import { LOCALES, type Locale, type Messages } from "./messages";

const Ctx = createContext<{ locale: Locale; t: Messages }>({ locale: "en", t: LOCALES.en });

export function I18nProvider({ locale, children }: { locale: Locale; children: ReactNode }) {
  return <Ctx.Provider value={{ locale, t: LOCALES[locale] }}>{children}</Ctx.Provider>;
}
export const useI18n = () => useContext(Ctx);
