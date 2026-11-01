"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { RunDialog } from "@/components/run-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { WORKFLOW_LABELS, formatTime } from "@/lib/format";
import type { RunView } from "@/lib/types";

const STATUS: Record<string, { label: string; variant: "secondary" | "outline" | "destructive" | "default" }> = {
  waiting_input: { label: "Needs your answer", variant: "default" },
  waiting_approval: { label: "Needs approval", variant: "default" },
  running: { label: "Running", variant: "outline" },
  completed: { label: "Done", variant: "secondary" },
  failed: { label: "Failed", variant: "destructive" },
};

export function RunsCard() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api<RunView[]>("/runs?limit=8") });
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent runs</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        {runs.data?.length === 0 && <p className="text-sm text-muted-foreground">No runs yet.</p>}
        {runs.data?.map((run) => {
          const status = STATUS[run.status] ?? STATUS.running;
          const waiting = run.status.startsWith("waiting");
          return (
            <div key={run.id} className="flex items-center gap-3 rounded-md px-2 py-1.5 text-sm">
              <span className="flex-1">{WORKFLOW_LABELS[run.workflow] ?? run.workflow}</span>
              <span className="text-xs text-muted-foreground">{formatTime(run.started_at)}</span>
              <Badge variant={status.variant}>{status.label}</Badge>
              {run.workflow !== "chat" && (
                <Button size="xs" variant={waiting ? "default" : "ghost"} onClick={() => setOpen(run.id)}>
                  {waiting ? "Continue" : "View"}
                </Button>
              )}
            </div>
          );
        })}
      </CardContent>
      {open && <RunDialog runId={open} onClose={() => setOpen(null)} />}
    </Card>
  );
}
