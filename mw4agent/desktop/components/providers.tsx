"use client";

import { ThemeProvider } from "next-themes";
import { I18nProvider } from "@/lib/i18n";
import { GatewayWsProvider } from "@/lib/gateway-ws-context";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <I18nProvider>
      <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
        <GatewayWsProvider>{children}</GatewayWsProvider>
      </ThemeProvider>
    </I18nProvider>
  );
}
