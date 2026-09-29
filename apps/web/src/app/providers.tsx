"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

import { PaletteProvider } from "@/components/palette";
import { AppShell } from "@/components/shell";
import { ThemeProvider } from "@/components/theme";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { UploadProvider } from "@/components/upload";

export function Providers({ children }: { children: ReactNode }) {
  // One client per browser session, created on first render rather than at import time.
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 10_000, retry: 1 } } }),
  );
  return (
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <TooltipProvider delayDuration={300}>
          <UploadProvider>
            <PaletteProvider>
              <AppShell>{children}</AppShell>
              <Toaster position="bottom-right" />
            </PaletteProvider>
          </UploadProvider>
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
