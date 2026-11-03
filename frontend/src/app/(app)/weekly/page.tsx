"use client";

import { useQuery } from "@tanstack/react-query";
import { CalendarCheckIcon, CalendarRangeIcon } from "lucide-react";

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
    queryKey: ["runs", "reviews-open"],
    queryFn: async () => {
      const runs = await api<RunView[]>("/runs?limit=20");
      const waiting = (workflow: string) =>
        runs.find((r) => r.workflow === workflow && r.status.startsWith("waiting")) ?? null;
      return { weekly: waiting("weekly_review"), monthly: waiting("monthly_review") };
    },
  });

  async function continueRun(id: string) {
    setRun(await api<RunView>(`/runs/${id}`));
  }

  const request = run?.waiting_for;
  const title =
    request?.type === "decisions"
      ? `Week of ${request.week.label}`
      : request?.type === "month_review"
        ? request.month.label
        : request?.type === "approval"
          ? "Confirm the changes"
          : run?.workflow === "monthly_review"
            ? "Monthly review"
            : "Weekly review";

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Reviews</h1>
        <p className="text-sm text-muted-foreground">
          About 20 minutes once a week: see the week, decide every overdue task, triage the Inbox, close the week.
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
            {open.data?.weekly && (
              <Button variant="outline" onClick={() => continueRun(open.data!.weekly!.id)}>
                Continue the review started {formatTime(open.data.weekly.started_at)}
              </Button>
            )}
          </CardContent>
        </Card>
      )}
      {!run && (
        <Card>
          <CardHeader>
            <CardTitle>Monthly review</CardTitle>
            <CardDescription>
              Early in a month it looks back at the month before: tasks, scorecard, goals and the weekly reviews.
              LifeOS drafts it, you add a note, and it closes the month&apos;s page and prepares the next.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => start.mutate({ workflow: "monthly_review" })} disabled={pending}>
              <CalendarRangeIcon /> Start the monthly review
            </Button>
            {open.data?.monthly && (
              <Button variant="outline" onClick={() => continueRun(open.data!.monthly!.id)}>
                Continue the review started {formatTime(open.data.monthly.started_at)}
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
