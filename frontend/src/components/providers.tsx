"use client";

import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { useState } from "react";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => {
    const queryClient: QueryClient = new QueryClient({
      defaultOptions: {
        queries: {
          staleTime: 30_000,
          refetchOnWindowFocus: false,
          retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
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
