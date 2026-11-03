"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLinkIcon } from "lucide-react";
import { useState } from "react";

import { useSettings } from "@/components/app-shell";
import { TaskRow } from "@/components/today-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { formatDay } from "@/lib/format";
import type { Task } from "@/lib/types";

const VIEWS = [
  { key: "today", label: "Today", empty: "Nothing due today." },
  { key: "overdue", label: "Overdue", empty: "Nothing overdue." },
  { key: "next7", label: "Next 7 days", empty: "Nothing due in the next 7 days." },
  { key: "inbox", label: "Inbox", empty: "The Inbox is empty." },
  { key: "active", label: "All open", empty: "No open tasks." },
] as const;

type View = (typeof VIEWS)[number]["key"];

export default function TasksPage() {
  const [view, setView] = useState<View>("today");
  const [filter, setFilter] = useState("");
  const tasks = useQuery({ queryKey: ["tasks", view], queryFn: () => api<Task[]>(`/tasks?view=${view}`) });
  const today = useSettings().data?.today ?? "";
  const needle = filter.trim().toLowerCase();
  const shown = (tasks.data ?? [])
    .filter((t) => !needle || t.title.toLowerCase().includes(needle) || t.area?.toLowerCase().includes(needle))
    .sort((a, b) => (a.due ?? "9999").localeCompare(b.due ?? "9999") || a.title.localeCompare(b.title));
  const current = VIEWS.find((v) => v.key === view)!;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>
        <p className="text-sm text-muted-foreground">
          Read from your Notion. Change them there, in a routine, or by asking in chat.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label="View" className="flex flex-wrap gap-1">
          {VIEWS.map((v) => (
            <Button key={v.key} size="sm" variant={view === v.key ? "secondary" : "ghost"}
              aria-pressed={view === v.key} onClick={() => setView(v.key)}>
              {v.label}
            </Button>
          ))}
        </div>
        <Input aria-label="Filter tasks" placeholder="Filter by title or area" value={filter}
          onChange={(e) => setFilter(e.target.value)} className="h-8 w-full sm:ml-auto sm:w-56" />
      </div>
      <Card>
        <CardContent className="flex flex-col gap-1">
          {tasks.isPending && <Skeleton className="h-24 w-full" />}
          {tasks.error && <p role="alert" className="text-sm text-destructive">Couldn&apos;t load tasks: {tasks.error.message}</p>}
          {tasks.data && shown.length === 0 && (
            <p className="text-sm text-muted-foreground">{needle ? "No task matches the filter." : current.empty}</p>
          )}
          {shown.map((task) => (
            <TaskRow key={task.id} url={task.url}>
              <span className="min-w-0 flex-1 truncate">{task.title || "Untitled"}</span>
              {task.area && <span className="hidden text-xs text-muted-foreground sm:inline">{task.area}</span>}
              {task.recurring && <Badge variant="outline">Recurring</Badge>}
              {task.priority && <Badge variant="outline">{task.priority}</Badge>}
              <span className="hidden w-20 text-xs text-muted-foreground sm:inline">{task.status}</span>
              {task.due ? (
                <Badge variant={today && task.due < today ? "destructive" : "secondary"} className="tabular-nums">
                  {formatDay(task.due)}
                </Badge>
              ) : (
                <span className="text-xs text-muted-foreground">No date</span>
              )}
              {task.url && <ExternalLinkIcon className="size-3.5 opacity-0 group-hover:opacity-60" />}
            </TaskRow>
          ))}
          {tasks.data && shown.length > 0 && (
            <p className="px-2 pt-2 text-xs text-muted-foreground">
              {shown.length} task{shown.length === 1 ? "" : "s"}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
