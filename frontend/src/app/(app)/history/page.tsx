"use client";

import { useQuery } from "@tanstack/react-query";
import { MoonIcon, SunriseIcon } from "lucide-react";
import { useState } from "react";

import { useSettings } from "@/components/app-shell";
import { RunDialog } from "@/components/run-dialog";
import { List } from "@/components/run-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { formatDay } from "@/lib/format";
import { nativeSelect } from "@/lib/styles";
import type { HistoryDay, HistoryRun } from "@/lib/types";

const PERIODS = [14, 30, 90];

/* eslint-disable @typescript-eslint/no-explicit-any -- run summaries are free-form per workflow */
type Summary = Record<string, any>;

export default function HistoryPage() {
  const [days, setDays] = useState(14);
  const [open, setOpen] = useState<string | null>(null);
  const today = useSettings().data?.today;
  const history = useQuery({
    queryKey: ["history", days],
    queryFn: () => api<HistoryDay[]>(`/history?days=${days}`),
  });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">History</h1>
          <p className="text-sm text-muted-foreground">Each day&apos;s morning plan next to what the evening check-in found.</p>
        </div>
        <select aria-label="Period" className={nativeSelect} value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {PERIODS.map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select>
      </div>
      {history.error && <p role="alert" className="text-sm text-destructive">{history.error.message}</p>}
      {history.isPending && <Skeleton className="h-40" />}
      {history.data?.length === 0 && (
        <p className="text-sm text-muted-foreground">No briefings or check-ins in this period.</p>
      )}
      {history.data?.map((day) => (
        <Card key={day.day}>
          <CardHeader>
            <CardTitle>{formatDay(day.day, { weekday: "long", month: "long" })}</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <Half icon={<SunriseIcon />} title="Morning briefing" run={day.briefing} today={day.day === today} onOpen={setOpen}>
              {(s) => (
                <>
                  <List title="Priorities" items={s.priorities} />
                  <List title="Proposed for the day" items={s.proposed_for_today} />
                  <List title="Observations" items={s.observations} />
                  {s.note && <p className="italic text-muted-foreground">{s.note}</p>}
                </>
              )}
            </Half>
            <Half icon={<MoonIcon />} title="Evening check-in" run={day.checkin} today={day.day === today} onOpen={setOpen}>
              {(s) => (
                <>
                  <List title="Finished" items={s.done} />
                  <List title="Left for the weekly review" items={s.partial} />
                  <Habits habits={s.habits} metrics={s.metrics} />
                  <List title="Already planned for the next day" items={s.tomorrow?.committed} />
                  <List title="Proposed for the next day" items={s.tomorrow?.proposed} />
                </>
              )}
            </Half>
          </CardContent>
        </Card>
      ))}
      {open && <RunDialog runId={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function Half({
  icon,
  title,
  run,
  today,
  onOpen,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  run: HistoryRun | null;
  today: boolean;
  onOpen: (id: string) => void;
  children: (summary: Summary) => React.ReactNode;
}) {
  const waiting = run?.status.startsWith("waiting");
  return (
    <section className="flex flex-col gap-3 text-sm">
      <div className="flex items-center gap-2 font-medium [&_svg]:size-4 [&_svg]:text-muted-foreground">
        {icon}
        <span className="flex-1">{title}</span>
        {run?.status === "failed" && <Badge variant="destructive">Failed</Badge>}
        {run && (run.summary || waiting) && (
          <Button size="xs" variant={waiting ? "default" : "ghost"} onClick={() => onOpen(run.id)}>
            {waiting ? "Continue" : "View"}
          </Button>
        )}
      </div>
      {!run && <p className="text-muted-foreground">{today ? "Not yet." : "Didn't run."}</p>}
      {waiting && <p className="text-muted-foreground">Waiting for you.</p>}
      {run?.summary && children(run.summary as Summary)}
    </section>
  );
}

function Habits({ habits, metrics }: { habits?: Record<string, boolean>; metrics?: string[] }) {
  const entries = Object.entries(habits ?? {});
  if (entries.length === 0 && !metrics?.length) return null;
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">Scorecard</p>
      <div className="mt-1 flex flex-wrap gap-1">
        {entries.map(([name, done]) => (
          <Badge key={name} variant={done ? "secondary" : "outline"} className={done ? "" : "text-muted-foreground line-through"}>
            {name}
          </Badge>
        ))}
        {metrics?.map((label) => <Badge key={label} variant="outline">{label}</Badge>)}
      </div>
    </div>
  );
}
