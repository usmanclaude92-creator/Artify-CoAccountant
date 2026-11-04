"use client";
import type { ReactNode } from "react";
import { PlatformShell } from "@/components/platform/shell";

export default function PlatformAppLayout({ children }: { children: ReactNode }) {
  return <PlatformShell>{children}</PlatformShell>;
}
