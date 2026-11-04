"use client";
import type { ReactNode } from "react";
import { Panel } from "@artify/ui";
import { useI18n } from "@/i18n";
import { LocaleSwitch } from "./LocaleSwitch";

export function AuthShell({ title, children, platform = false }: { title: string; children: ReactNode; platform?: boolean }) {
  const { t } = useI18n();
  return (
    <main id="main" className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 px-4 py-8">
      <header className="flex items-center justify-between">
        <span className="text-lg font-semibold">{platform ? t.platformName : t.appName}</span>
        <LocaleSwitch />
      </header>
      <Panel>
        <h1 className="mb-4 text-xl font-semibold">{title}</h1>
        {children}
      </Panel>
    </main>
  );
}
