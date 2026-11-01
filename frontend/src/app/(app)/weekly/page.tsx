"use client";

import { useQuery } from "@tanstack/react-query";
import { CalendarCheckIcon } from "lucide-react";

import { RunPanel } from "@/components/run-panel";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/format";
import { useRun } from "@/lib/runs";
import type { RunView } from "@/lib/types";

export default function WeeklyReviewPage() {
  const { run, setRun, start, resume, pending } = useRun();
  const open = useQuery({
    queryKey: ["runs", "weekly-open"],
    queryFn: async () => {
      const runs = await api<RunView[]>("/runs?limit=20");
      return runs.find((r) => r.workflow === "weekly_review" && r.status.startsWith("waiting")) ?? null;
    },
  });

  async function continueRun(id: string) {
    setRun(await api<RunView>(`/runs/${id}`));
  }

  const request = run?.waiting_for;
  const title =
    request?.type === "decisions"
      ? `Week of ${request.week.label}`
      : request?.type === "approval"
        ? "Confirm the changes"
        : "Weekly review";

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Weekly review</h1>
        <p className="text-sm text-muted-foreground">
          About 20 minutes on Sunday: see the week, decide every overdue task, triage the Inbox, close the week.
        </p>
      </div>
      {!run && (
        <Card>
          <CardHeader>
            <CardTitle>Ready when you are</CardTitle>
            <CardDescription>
              LifeOS reads this week&apos;s tasks and scorecard, drafts a review and suggests a decision for each
              overdue task. You decide; nothing changes until you approve.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button onClick={() => start.mutate({ workflow: "weekly_review" })} disabled={pending}>
              <CalendarCheckIcon /> Start this week&apos;s review
            </Button>
            {open.data && (
              <Button variant="outline" onClick={() => continueRun(open.data!.id)}>
                Continue the review started {formatTime(open.data.started_at)}
              </Button>
            )}
          </CardContent>
        </Card>
      )}
      {(run || pending) && (
        <Card>
          <CardHeader>
            <CardTitle>{title}</CardTitle>
          </CardHeader>
          <CardContent>
            <RunPanel run={run ?? ({ status: "running" } as RunView)} pending={pending}
              onAnswer={(answer) => resume.mutate(answer)} />
            {run?.status === "completed" && (
              <Button variant="outline" className="mt-4" onClick={() => setRun(null)}>Back</Button>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
