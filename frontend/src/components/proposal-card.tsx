"use client";

import { ArrowRightIcon, TriangleAlertIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { formatDay, humanize } from "@/lib/format";
import type { ProposalCard as Card } from "@/lib/types";

export const KIND_LABELS: Record<string, string> = {
  "task.mark_done": "Mark done",
  "task.set_due": "Move",
  "task.set_status": "Change status",
  "task.create": "New task",
  "habit.upsert_day": "Scorecard",
  "week.create": "New week page",
  "week.close": "Close week",
  "month.create": "New month page",
  "month.close": "Close month",
  "book.update_progress": "Reading progress",
};

function Change({ from, to }: { from?: React.ReactNode; to: React.ReactNode }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {from !== undefined && <span className="text-muted-foreground line-through">{from}</span>}
      {from !== undefined && <ArrowRightIcon className="size-3.5 text-muted-foreground" />}
      <span className="font-medium">{to}</span>
    </span>
  );
}

function Details({ card }: { card: Card }) {
  const p = card.payload as Record<string, unknown>;
  const before = (card.before ?? {}) as Record<string, unknown>;
  const str = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));
  switch (card.kind) {
    case "task.mark_done":
      return <Change from={str(before.status)} to={`Done on ${formatDay(p.completed_on as string)}`} />;
    case "task.set_due": {
      const newPage = p.week_proposal_id || p.month_proposal_id;
      return (
        <span className="flex flex-col gap-1">
          <Change from={before.due ? formatDay(before.due as string) : "no date"} to={formatDay(p.new_due as string)} />
          <span className="text-xs text-muted-foreground">
            Week and Month links move too{newPage ? " (to a page created by this batch)" : ""}.
          </span>
        </span>
      );
    }
    case "task.set_status":
      return <Change from={str(before.status)} to={humanize(String(p.status))} />;
    case "task.create":
      return (
        <span>
          {humanize(String(p.status))}
          {p.due ? `, due ${formatDay(p.due as string)}` : ""}
        </span>
      );
    case "habit.upsert_day": {
      const checks = Object.entries((p.checks ?? {}) as Record<string, boolean>);
      // Older proposals stored sleep and screen time as fields rather than metrics.
      const metrics = Object.entries({
        ...(p.sleep != null ? { sleep: p.sleep } : {}),
        ...(p.screen_time != null ? { screen_time: p.screen_time } : {}),
        ...((p.metrics ?? {}) as Record<string, number>),
      });
      const label = (key: string) => card.labels?.[key] ?? humanize(key);
      return (
        <span className="flex flex-wrap gap-1.5">
          {checks.map(([name, done]) => (
            <Badge key={name} variant={done ? "secondary" : "outline"}>
              {done ? "✓" : "✗"} {label(name)}
            </Badge>
          ))}
          {metrics.map(([name, value]) => (
            <Badge key={name} variant="outline">
              {label(name)} {String(value)}{card.units?.[name] ? ` ${card.units[name]}` : ""}
            </Badge>
          ))}
          {p.notes ? <span className="w-full text-xs text-muted-foreground">Notes: {String(p.notes)}</span> : null}
        </span>
      );
    }
    case "week.create":
    case "month.create":
      return <span>{formatDay(p.start as string)} – {formatDay(p.end as string)} · {humanize(String(p.status))}</span>;
    case "week.close":
    case "month.close":
      return (
        <details className="text-sm">
          <summary className="cursor-pointer">Status → Closed, with the review text</summary>
          <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-2 text-xs">
            {String(p.review)}
          </pre>
        </details>
      );
    case "book.update_progress":
      return <Change from={str(before.pages_read)} to={`${str(p.pages_read)} pages read`} />;
    default:
      return <code className="text-xs">{JSON.stringify(p)}</code>;
  }
}

export function ProposalCardView({
  card,
  selected,
  onSelect,
  editDue,
  onEditDue,
}: {
  card: Card;
  selected?: boolean;
  onSelect?: (checked: boolean) => void;
  editDue?: string;
  onEditDue?: (value: string) => void;
}) {
  const decided = card.status !== "pending";
  return (
    <div className="flex gap-3 rounded-lg border p-3">
      {onSelect && (
        <Checkbox
          checked={selected}
          disabled={decided}
          onCheckedChange={(checked) => onSelect(Boolean(checked))}
          aria-label={`Approve ${KIND_LABELS[card.kind] ?? card.kind}: ${card.target.title}`}
          className="mt-0.5"
        />
      )}
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={card.importance === "important" ? "destructive" : "secondary"}>
            {card.importance === "important" && <TriangleAlertIcon />}
            {KIND_LABELS[card.kind] ?? card.kind}
          </Badge>
          <span className="truncate font-medium">
            {card.kind === "habit.upsert_day" ? `Scorecard · ${formatDay(String(card.payload.day))}` : card.target.title}
          </span>
          {decided && <Badge variant="outline">{card.status}</Badge>}
        </div>
        <div className="text-sm">
          <Details card={card} />
        </div>
        {card.kind === "task.set_due" && onEditDue && !decided && (
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Different day (same week and month)
            <Input
              type="date"
              className="h-7 w-40"
              value={editDue ?? String(card.payload.new_due)}
              onChange={(e) => onEditDue(e.target.value)}
            />
          </label>
        )}
        <p className="text-xs text-muted-foreground">
          {card.reason}
          {card.rule_ids.length > 0 && <span className="ml-1.5 font-mono">[{card.rule_ids.join(", ")}]</span>}
        </p>
      </div>
    </div>
  );
}
