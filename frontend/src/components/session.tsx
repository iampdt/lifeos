"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect } from "react";

import { ApiError, api, setCsrfToken } from "@/lib/api";

export type Me = { via: string; email: string | null; csrf_token: string | null };

const SessionContext = createContext<Me | null>(null);

/** The signed-in owner, or null while the session is still being checked. */
export function useSession() {
  return useContext(SessionContext);
}

export function useSignOut() {
  const client = useQueryClient();
  const router = useRouter();
  return async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      setCsrfToken(null);
      client.clear();
      router.replace("/login");
    }
  };
}

/** Checks the session and sends a signed-out visitor to /login.
 *
 * Pages render straight away with their own loading states, so every route has a static
 * shell. This is a UI decision only: the backend checks the session on every API call, so a
 * signed-out visitor sees skeletons and errors, never data. */
export function SessionProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<Me>("/auth/me"),
    retry: false,
    staleTime: Infinity,
  });

  useEffect(() => {
    if (me.data) setCsrfToken(me.data.csrf_token);
    if (me.error instanceof ApiError && me.error.status === 401) router.replace("/login");
  }, [me.data, me.error, router]);

  return <SessionContext.Provider value={me.data ?? null}>{children}</SessionContext.Provider>;
}
