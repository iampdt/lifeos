"use client";

import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { useState } from "react";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError, isUnreachable, WAKE_RETRIES, WAKE_RETRY_MS } from "@/lib/api";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => {
    const queryClient: QueryClient = new QueryClient({
      defaultOptions: {
        queries: {
          staleTime: 30_000,
          refetchOnWindowFocus: false,
          // While a sleeping API starts, keep trying for two minutes; otherwise twice, for
          // server errors only.
          retry: (count, error) =>
            isUnreachable(error)
              ? count < WAKE_RETRIES
              : !(error instanceof ApiError && error.status < 500) && count < 2,
          retryDelay: (count, error) =>
            isUnreachable(error) ? WAKE_RETRY_MS : Math.min(1_000 * 2 ** count, 30_000),
        },
      },
      queryCache: new QueryCache({
        onError: (error, query) => {
          // An expired session anywhere: re-check it, and AuthGate sends the owner to /login.
          if (error instanceof ApiError && error.status === 401 && query.queryKey[0] !== "me") {
            queryClient.invalidateQueries({ queryKey: ["me"] });
          }
        },
      }),
    });
    return queryClient;
  });
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={client}>
        <TooltipProvider>{children}</TooltipProvider>
        <Toaster richColors position="bottom-right" />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
