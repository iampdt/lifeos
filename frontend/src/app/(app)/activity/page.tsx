"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { RunsCard } from "@/components/runs-card";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { WORKFLOW_LABELS, formatDay } from "@/lib/format";
import { nativeSelect } from "@/lib/styles";
import type { UsageReport, UsageSummary } from "@/lib/types";

const PERIODS = [7, 30, 90];

function seconds(ms: number | null | undefined) {
  return ms == null ? "—" : `${(ms / 1000).toFixed(1)} s`;
}

function percent(share: number | null) {
  return share == null ? "—" : `${Math.round(share * 100)}%`;
}

export default function ActivityPage() {
  const [days, setDays] = useState(30);
  const report = useQuery({
    queryKey: ["usage", days],
    queryFn: () => api<UsageReport>(`/reports/usage?days=${days}`),
  });
  const r = report.data;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Activity</h1>
          <p className="text-sm text-muted-foreground">What your routines and chat did, how long the AI took, and what it used.</p>
        </div>
        <select aria-label="Period" className={nativeSelect} value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {PERIODS.map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select>
      </div>
      {report.error && <p role="alert" className="text-sm text-destructive">{report.error.message}</p>}
      {!r && !report.error && <Skeleton className="h-32" />}
      {r && (
        <>
          <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Figure label="Runs" value={String(r.totals.runs)} hint={`${percent(r.totals.success_rate)} finished without errors`} />
            <Figure label="AI calls" value={String(r.totals.llm_calls)} hint={Object.keys(r.models)[0] ?? "none"} />
            <Figure label="Tokens" value={(r.totals.input_tokens + r.totals.output_tokens).toLocaleString()}
              hint={`${r.totals.input_tokens.toLocaleString()} in · ${r.totals.output_tokens.toLocaleString()} out`} />
            <Figure label="AI time per run" value={seconds(r.totals.llm_latency_ms.p50)} hint={`95% under ${seconds(r.totals.llm_latency_ms.p95)}`} />
          </section>
          <Card>
            <CardHeader>
              <CardTitle>By routine</CardTitle>
              <CardDescription>AI time is the model&apos;s own time per run; it doesn&apos;t include waiting for you.</CardDescription>
            </CardHeader>
            <CardContent>
              <WorkflowTable workflows={r.workflows} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>By day</CardTitle>
            </CardHeader>
            <CardContent>
              {Object.keys(r.by_day).length === 0 ? (
                <p className="text-sm text-muted-foreground">No runs in this period.</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow><TableHead>Day</TableHead><TableHead className="text-right">Runs</TableHead><TableHead className="text-right">Tokens</TableHead></TableRow>
                  </TableHeader>
                  <TableBody>
                    {Object.entries(r.by_day).reverse().map(([day, d]) => (
                      <TableRow key={day}>
                        <TableCell>{formatDay(day)}</TableCell>
                        <TableCell className="text-right tabular-nums">{d.runs}</TableCell>
                        <TableCell className="text-right tabular-nums">{d.tokens.toLocaleString()}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      )}
      <RunsCard />
    </div>
  );
}

function Figure({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold tabular-nums">{value}</p>
      {hint && <p className="truncate text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function WorkflowTable({ workflows }: { workflows: Record<string, UsageSummary> }) {
  const rows = Object.entries(workflows);
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No runs in this period.</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Routine</TableHead>
          <TableHead className="text-right">Runs</TableHead>
          <TableHead className="text-right">Finished OK</TableHead>
          <TableHead className="text-right">AI time (median)</TableHead>
          <TableHead className="text-right">Tokens</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map(([name, s]) => (
          <TableRow key={name}>
            <TableCell className="font-medium">{WORKFLOW_LABELS[name] ?? name}</TableCell>
            <TableCell className="text-right tabular-nums">{s.runs}</TableCell>
            <TableCell className="text-right tabular-nums">{percent(s.success_rate)}</TableCell>
            <TableCell className="text-right tabular-nums">{seconds(s.llm_latency_ms.p50)}</TableCell>
            <TableCell className="text-right tabular-nums">{(s.input_tokens + s.output_tokens).toLocaleString()}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
