"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { useSession } from "@/components/session";
import { api } from "@/lib/api";
import type { WorkspaceView } from "@/lib/types";

export const MODULE_LABELS: Record<string, string> = {
  tasks: "Tasks", habits: "Habits", weeks: "Weekly reviews", months: "Monthly reviews",
  goals: "Goals", books: "Reading",
};

export function useWorkspace() {
  const session = useSession();
  return useQuery({
    queryKey: ["workspace"],
    queryFn: () => api<WorkspaceView>("/workspace"),
    enabled: Boolean(session && !session.owner),
  });
}

/** Shown on every page when the daily drift check paused part of an account's workspace. */
export function WorkspaceNotice() {
  const workspace = useWorkspace();
  const paused = workspace.data?.paused ?? [];
  if (paused.length === 0) return null;
  const names = paused.map((m) => MODULE_LABELS[m] ?? m).join(", ");
  return (
    <div className="border-b bg-destructive/10">
      <p className="mx-auto max-w-6xl px-4 py-2 text-sm">
        <span className="font-medium">Paused: {names}.</span>{" "}
        {paused.length === 1 ? "Its database" : "Their databases"} changed in Notion, so LifeOS stopped using{" "}
        {paused.length === 1 ? "it" : "them"}.{" "}
        <Link href="/setup" className="underline underline-offset-4">Review the setup</Link> to continue.
      </p>
    </div>
  );
}
