"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { useWorkspace } from "@/components/workspace-notice";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { formatDay } from "@/lib/format";
import type { BookView, GoalView, GoalsView, ReadingView } from "@/lib/types";

export default function GoalsPage() {
  const goals = useQuery({ queryKey: ["goals"], queryFn: () => api<GoalsView>("/goals") });
  const reading = useQuery({ queryKey: ["reading"], queryFn: () => api<ReadingView>("/reading") });
  const all = goals.data?.goals ?? [];
  const active = all.filter((g) => g.state === "active");
  const others = all.filter((g) => g.state !== "active");
  const books = reading.data?.books ?? [];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Goals</h1>
        <p className="text-sm text-muted-foreground">Where your goals and reading stand, read from your Notion.</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Goals</CardTitle>
          {goals.data?.available && (
            <CardDescription>
              {active.length} active{others.length > 0 && ` · ${others.length} planned, done or dropped`}
            </CardDescription>
          )}
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {goals.isPending && <Skeleton className="h-24 w-full" />}
          {goals.error && <p role="alert" className="text-sm text-destructive">{goals.error.message}</p>}
          {goals.data && !goals.data.available && <NotMapped module="Goals" />}
          {goals.data?.available && all.length === 0 && <p className="text-sm text-muted-foreground">No goals yet.</p>}
          {goals.data?.available && active.length === 0 && all.length > 0 && (
            <p className="text-sm text-muted-foreground">No active goals.</p>
          )}
          {active.map((goal) => <GoalItem key={goal.id} goal={goal} linked={Boolean(goals.data?.tasks_linked)} />)}
          {others.length > 0 && (
            <details className="rounded-lg border px-3 py-2 text-sm">
              <summary className="cursor-pointer text-muted-foreground">Planned, done and dropped</summary>
              <ul className="mt-2 flex flex-col gap-1">
                {others.map((goal) => (
                  <li key={goal.id} className="flex items-center gap-2">
                    <Title url={goal.url} className="min-w-0 flex-1 truncate">{goal.title}</Title>
                    {goal.status && <Badge variant="outline">{goal.status}</Badge>}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Reading</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {reading.isPending && <Skeleton className="h-16 w-full" />}
          {reading.error && <p role="alert" className="text-sm text-destructive">{reading.error.message}</p>}
          {reading.data && !reading.data.available && <NotMapped module="Reading" />}
          {reading.data?.available && books.length === 0 && (
            <p className="text-sm text-muted-foreground">Nothing in the reading queue.</p>
          )}
          <BookGroup title="Reading now" books={books.filter((b) => b.state === "reading")} progress />
          <BookGroup title="Up next" books={books.filter((b) => b.state === "want")} />
          <BookGroup title="Finished" books={books.filter((b) => b.state === "finished")} />
        </CardContent>
      </Card>
    </div>
  );
}

function GoalItem({ goal, linked }: { goal: GoalView; linked: boolean }) {
  const details = [goal.type, goal.area, goal.deadline && `by ${formatDay(goal.deadline, { weekday: undefined, year: "numeric" })}`]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="flex flex-col gap-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <Title url={goal.url} className="font-medium">{goal.title}</Title>
        {details && <span className="text-xs text-muted-foreground">{details}</span>}
      </div>
      {goal.success_metric && <p className="text-sm text-muted-foreground">{goal.success_metric}</p>}
      {goal.progress != null && (
        <Progress value={goal.progress} label={`${goal.current} of ${goal.target}`} />
      )}
      {linked && (
        goal.open_tasks.length > 0 ? (
          <ul className="flex flex-col gap-0.5 text-sm">
            {goal.open_tasks.slice(0, 5).map((task) => (
              <li key={task.id} className="flex items-center gap-2">
                <Title url={task.url} className="min-w-0 flex-1 truncate">{task.title}</Title>
                <span className="text-xs text-muted-foreground">{task.due ? formatDay(task.due) : task.status}</span>
              </li>
            ))}
            {goal.open_tasks.length > 5 && (
              <li className="text-xs text-muted-foreground">and {goal.open_tasks.length - 5} more</li>
            )}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">No open tasks linked to this goal.</p>
        )
      )}
    </div>
  );
}

function BookGroup({ title, books, progress }: { title: string; books: BookView[]; progress?: boolean }) {
  if (books.length === 0) return null;
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <ul className="mt-1 flex flex-col gap-2">
        {books.map((book) => (
          <li key={book.id} className="flex flex-col gap-1 text-sm">
            <div className="flex items-baseline gap-2">
              <Title url={book.url} className="min-w-0 truncate">{book.title}</Title>
              {book.author && <span className="truncate text-xs text-muted-foreground">{book.author}</span>}
            </div>
            {progress && book.progress != null && (
              <Progress value={book.progress} label={`${book.pages_read} of ${book.pages} pages`} />
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Progress({ value, label }: { value: number; label: string }) {
  const percent = Math.round(value * 100);
  return (
    <div className="flex items-center gap-3">
      <div role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} aria-label={label}
        className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-primary" style={{ width: `${percent}%` }} />
      </div>
      <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{label} · {percent}%</span>
    </div>
  );
}

/** Links to the Notion page when there is one (the demo workspace has none). */
function Title({ url, className, children }: { url: string; className?: string; children: React.ReactNode }) {
  if (!url) return <span className={className}>{children}</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer" className={`hover:underline ${className ?? ""}`}>
      {children}
    </a>
  );
}

function NotMapped({ module }: { module: string }) {
  const workspace = useWorkspace();
  return (
    <p className="text-sm text-muted-foreground">
      {module} isn&apos;t part of your workspace.
      {workspace.data?.editable && (
        <>
          {" "}Add it in <Link href="/setup" className="underline underline-offset-4">setup</Link>.
        </>
      )}
    </p>
  );
}
