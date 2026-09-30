"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

import { AuthProvider } from "@/components/account";
import { PaletteProvider } from "@/components/palette";
import { AppShell } from "@/components/shell";
import { ThemeProvider } from "@/components/theme";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { UploadProvider } from "@/components/upload";
import { ApiError } from "@/lib/api";

export function Providers({ children }: { children: ReactNode }) {
  // One client per browser session, created on first render rather than at import time.
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            // Retry once for outages, not refusals: a 401, 404 or 429 would only repeat.
            retry: (failures, error) => failures < 1 && !(error instanceof ApiError && error.status < 500),
          },
        },
      }),
  );
  return (
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <AuthProvider>
          <TooltipProvider delayDuration={300}>
            <UploadProvider>
              <PaletteProvider>
                <AppShell>{children}</AppShell>
                <Toaster position="bottom-right" />
              </PaletteProvider>
            </UploadProvider>
          </TooltipProvider>
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
