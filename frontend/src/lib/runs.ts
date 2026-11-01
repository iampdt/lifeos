"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { RunView } from "@/lib/types";

/** Holds one workflow run and advances it: start, then answer each pause. */
export function useRun(initial: RunView | null = null) {
  const client = useQueryClient();
  const [run, setRun] = useState<RunView | null>(initial);

  const refresh = () => {
    for (const key of ["runs", "approvals", "tasks"]) client.invalidateQueries({ queryKey: [key] });
  };

  const start = useMutation({
    mutationFn: ({ workflow, inputs }: { workflow: string; inputs?: Record<string, unknown> }) =>
      api<RunView>(`/workflows/${workflow}/runs`, { method: "POST", body: { inputs: inputs ?? {} } }),
    onSuccess: (view) => {
      setRun(view);
      refresh();
    },
    onError: (error) => toast.error(`Couldn't start: ${error.message}`),
  });

  const resume = useMutation({
    mutationFn: (answer: unknown) =>
      api<RunView>(`/runs/${run?.id}/resume`, { method: "POST", body: { answer } }),
    onSuccess: (view) => {
      setRun(view);
      refresh();
    },
    onError: (error) => toast.error(`Couldn't continue: ${error.message}`),
  });

  return { run, setRun, start, resume, pending: start.isPending || resume.isPending };
}
