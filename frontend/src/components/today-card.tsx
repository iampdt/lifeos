"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLinkIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { useSettings } from "@/components/app-shell";
import { formatDay } from "@/lib/format";
import type { Task } from "@/lib/types";

export function TodayCard() {
  const today = useQuery({ queryKey: ["tasks", "today"], queryFn: () => api<Task[]>("/tasks?view=today") });
  // "Today" comes from the backend, in the owner's timezone.
  const settings = useSettings().data;
  const now = settings?.today ?? "";
  const dueToday = today.data?.filter((t) => t.due === now) ?? [];
  const overdue = today.data?.filter((t) => t.due && t.due < now) ?? [];
  const major = dueToday.filter((t) => ["Next", "Doing"].includes(t.status ?? "") && !t.recurring).length;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Today</CardTitle>
        <CardDescription>
          {now ? formatDay(now, { weekday: "long", month: "long" }) : "Today"} · {major} of {settings?.max_major_per_day ?? 3} major tasks
          {overdue.length > 0 && ` · ${overdue.length} overdue, for the weekly review`}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        {(today.isPending || !now) && <Skeleton className="h-20 w-full" />}
        {today.error && <p className="text-sm text-destructive">Couldn&apos;t load tasks: {today.error.message}</p>}
        {today.data?.length === 0 && <p className="text-sm text-muted-foreground">Nothing due. Plan the day in the morning briefing.</p>}
        {now && [...dueToday, ...overdue].map((task) => (
          <TaskRow key={task.id} url={task.url}>
            <span className="min-w-0 flex-1 truncate">{task.title || "Untitled"}</span>
            {task.priority && <Badge variant="outline">{task.priority}</Badge>}
            {task.due && task.due < now ? (
              <Badge variant="destructive">{formatDay(task.due)}</Badge>
            ) : (
              <span className="text-xs text-muted-foreground">{task.status}</span>
            )}
            {task.url && <ExternalLinkIcon className="size-3.5 opacity-0 group-hover:opacity-60" />}
          </TaskRow>
        ))}
      </CardContent>
    </Card>
  );
}

const rowClass = "group flex items-center gap-3 rounded-md px-2 py-1.5 text-sm hover:bg-muted";

/** A task links to its Notion page when it has one (the demo workspace has none). */
export function TaskRow({ url, children }: { url: string; children: React.ReactNode }) {
  if (!url) return <div className={rowClass}>{children}</div>;
  return (
    <a href={url} target="_blank" rel="noreferrer" className={rowClass}>
      {children}
    </a>
  );
}
