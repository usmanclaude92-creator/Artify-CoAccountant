"use client";
import type { ReactNode } from "react";
import { ControlCenterShell } from "@/components/cc/shell";

export default function ControlCenterLayout({ children }: { children: ReactNode }) {
  return <ControlCenterShell>{children}</ControlCenterShell>;
}
