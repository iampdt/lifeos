"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { InboxIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ProposalCardView } from "@/components/proposal-card";
import { RunDialog } from "@/components/run-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { api } from "@/lib/api";
import { WORKFLOW_LABELS, formatTime } from "@/lib/format";
import type { Approvals, DecisionResult } from "@/lib/types";

export function useApprovals() {
  return useQuery({ queryKey: ["approvals"], queryFn: () => api<Approvals>("/approvals"), refetchInterval: 60_000 });
}

export function ApprovalsSheet() {
  const approvals = useApprovals();
  const client = useQueryClient();
  const [openRun, setOpenRun] = useState<string | null>(null);
  const runCount = approvals.data?.runs.reduce((n, r) => n + r.proposals.filter((p) => p.status === "pending").length, 0) ?? 0;
  const total = runCount + (approvals.data?.standalone.length ?? 0);

  const decide = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) =>
      api<DecisionResult>(`/proposals/${id}/${approve ? "approve" : "reject"}`, { method: "POST", body: {} }),
    onSuccess: (result) => {
      client.invalidateQueries({ queryKey: ["approvals"] });
      client.invalidateQueries({ queryKey: ["tasks"] });
      const outcome = result.results[0]?.outcome;
      if (result.errors && Object.keys(result.errors).length) toast.error(Object.values(result.errors)[0]);
      else if (result.approved.length) toast.success(result.writes_enabled ? `Applied: ${outcome}` : "Approved (writes are off)");
      else toast("Rejected");
    },
    onError: (error) => toast.error(error.message),
  });

  const undo = useMutation({
    mutationFn: (id: string) => api<{ outcome: string; writes_enabled: boolean }>(`/proposals/${id}/undo`, { method: "POST" }),
    onSuccess: (result) => {
      client.invalidateQueries({ queryKey: ["approvals"] });
      toast.success(result.writes_enabled ? "Undone in Notion." : "Nothing to undo: writes are off.");
    },
    onError: (error) => toast.error(error.message),
  });
  const automatic = approvals.data?.automatic ?? [];

  return (
    <>
      <Sheet>
        <SheetTrigger render={<Button variant="outline" size="sm" />}>
          <InboxIcon />
          Approvals
          {total > 0 && <Badge className="ml-1 h-5 min-w-5 px-1">{total}</Badge>}
        </SheetTrigger>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>Approvals</SheetTitle>
            <SheetDescription>Nothing reaches Notion until you approve it.</SheetDescription>
          </SheetHeader>
          <div className="flex flex-col gap-6 px-4 pb-6">
            {total === 0 && <p className="text-sm text-muted-foreground">You&apos;re all caught up.</p>}
            {approvals.data?.runs.map((run) => (
              <section key={run.run_id} className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-medium">
                    {WORKFLOW_LABELS[run.workflow] ?? run.workflow}
                    <span className="ml-2 text-xs font-normal text-muted-foreground">{formatTime(run.started_at)}</span>
                  </h3>
                  <Button size="sm" onClick={() => setOpenRun(run.run_id)}>Review</Button>
                </div>
                {run.proposals.map((card) => (
                  <ProposalCardView key={card.id} card={card} />
                ))}
              </section>
            ))}
            {(approvals.data?.standalone.length ?? 0) > 0 && (
              <section className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">From chat</h3>
                {approvals.data?.standalone.map((card) => (
                  <div key={card.id} className="flex flex-col gap-2">
                    <ProposalCardView card={card} />
                    <div className="flex justify-end gap-2">
                      <Button size="sm" variant="outline" disabled={decide.isPending}
                        onClick={() => decide.mutate({ id: card.id, approve: false })}>Reject</Button>
                      <Button size="sm" disabled={decide.isPending}
                        onClick={() => decide.mutate({ id: card.id, approve: true })}>Approve</Button>
                    </div>
                  </div>
                ))}
              </section>
            )}
            {automatic.length > 0 && (
              <section className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">Applied automatically</h3>
                <p className="text-xs text-muted-foreground">
                  The kinds you set to Automatic in Settings, from the last week. Undo puts back the values from before,
                  unless the page changed since.
                </p>
                {automatic.map((card) => (
                  <div key={card.id} className="flex flex-col gap-2">
                    <ProposalCardView card={card} />
                    <div className="flex items-center justify-end gap-2 text-xs text-muted-foreground">
                      {card.decided_at && <span className="mr-auto">{formatTime(card.decided_at)}</span>}
                      {card.status === "undone" ? (
                        <Badge variant="outline">Undone</Badge>
                      ) : card.undoable ? (
                        <Button size="sm" variant="outline" disabled={undo.isPending} onClick={() => undo.mutate(card.id)}>
                          Undo
                        </Button>
                      ) : (
                        <Badge variant="outline">{card.status === "executed" ? "Already as it was" : card.status}</Badge>
                      )}
                    </div>
                  </div>
                ))}
              </section>
            )}
          </div>
        </SheetContent>
      </Sheet>
      {openRun && <RunDialog runId={openRun} onClose={() => setOpenRun(null)} />}
    </>
  );
}
