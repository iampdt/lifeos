"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { RunPanel } from "@/components/run-panel";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { WORKFLOW_LABELS } from "@/lib/format";
import { useRun } from "@/lib/runs";
import type { RunView } from "@/lib/types";

/** A workflow run in a dialog: start one (`workflow`) or continue one (`runId`). */
export function RunDialog({
  workflow,
  runId,
  inputs,
  onClose,
}: {
  workflow?: string;
  runId?: string;
  inputs?: Record<string, unknown>;
  onClose: () => void;
}) {
  const { run, setRun, start, resume, pending } = useRun();
  const existing = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api<RunView>(`/runs/${runId}`),
    enabled: Boolean(runId),
    staleTime: 0,
  });

  useEffect(() => {
    if (existing.data && !run) setRun(existing.data);
  }, [existing.data, run, setRun]);

  useEffect(() => {
    if (workflow && !run && !start.isPending && !start.isError) start.mutate({ workflow, inputs });
    // Start once when the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const name = run?.workflow ?? workflow ?? existing.data?.workflow ?? "";
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{WORKFLOW_LABELS[name] ?? "Run"}</DialogTitle>
          <DialogDescription>
            {run?.status === "waiting_approval"
              ? "Review the proposed changes."
              : run?.status === "completed"
                ? "Finished."
                : "LifeOS prepares; you decide."}
          </DialogDescription>
        </DialogHeader>
        {run ? (
          <RunPanel run={run} pending={pending} onAnswer={(answer) => resume.mutate(answer)} />
        ) : (
          <RunPanel
            run={{ status: "running" } as RunView}
            pending
            onAnswer={() => undefined}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
