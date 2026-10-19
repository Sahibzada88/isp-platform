"use client";

import type { ReactNode } from "react";
import { AuthProvider } from "@/lib/auth";
import { I18nProvider } from "@/lib/i18n";
import { ToastProvider } from "./ui";

export function Providers({ children }: { children: ReactNode }) {
  return <I18nProvider><AuthProvider><ToastProvider>{children}</ToastProvider></AuthProvider></I18nProvider>;
}
