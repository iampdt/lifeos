"use client";

import { CircleCheckIcon, Loader2Icon, TriangleAlertIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { ProposalCardView } from "@/components/proposal-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { formatDay, humanize } from "@/lib/format";
import type { ApprovalRequest, DecisionsRequest, QuestionRequest, RunView } from "@/lib/types";

type Props = { run: RunView; pending: boolean; onAnswer: (answer: unknown) => void };

export function RunPanel({ run, pending, onAnswer }: Props) {
  if (pending) return <Working />;
  if (run.status === "failed") {
    return (
      <Notice tone="error" title="This run failed">
        {run.error?.message ?? run.error?.type ?? "Unknown error"}. Nothing was written to Notion.
      </Notice>
    );
  }
  const request = run.waiting_for;
  if (run.status.startsWith("waiting") && request) {
    if (request.type === "question") return <QuestionStep request={request} onAnswer={onAnswer} />;
    if (request.type === "decisions") return <DecisionsStep request={request} onAnswer={onAnswer} />;
    if (request.type === "approval") return <ApprovalStep request={request} onAnswer={onAnswer} />;
  }
  if (run.status === "completed") return <RunSummary run={run} />;
  return <Working />;
}

function Working() {
  return (
    <div className="flex items-center gap-3 py-8 text-sm text-muted-foreground">
      <Loader2Icon className="size-4 animate-spin" />
      Working… the AI can take up to half a minute on the free tier.
    </div>
  );
}

function Notice({ tone, title, children }: { tone: "error" | "info"; title: string; children?: React.ReactNode }) {
  return (
    <div className={`rounded-lg border p-3 text-sm ${tone === "error" ? "border-destructive/40 text-destructive" : ""}`}>
      <p className="flex items-center gap-2 font-medium">
        {tone === "error" ? <TriangleAlertIcon className="size-4" /> : <CircleCheckIcon className="size-4" />}
        {title}
      </p>
      {children && <div className="mt-1 text-muted-foreground">{children}</div>}
    </div>
  );
}

// --- evening check-in question -------------------------------------------------------------

function QuestionStep({ request, onAnswer }: { request: QuestionRequest; onAnswer: (a: unknown) => void }) {
  const [text, setText] = useState("");
  const [done, setDone] = useState<Set<string>>(new Set());
  const logged = request.habits.logged_today?.checks ?? {};
  const [habits, setHabits] = useState<Record<string, boolean>>(
    Object.fromEntries(request.habits.names.map((n) => [n, Boolean(logged[n])])),
  );
  const [sleep, setSleep] = useState("");
  const [screen, setScreen] = useState("");

  function submit() {
    const answer: Record<string, unknown> = {};
    if (text.trim()) answer.text = text.trim();
    if (done.size) answer.done_refs = [...done];
    const ticked = Object.fromEntries(Object.entries(habits).filter(([n, v]) => v && !logged[n]));
    if (Object.keys(ticked).length) answer.habits = ticked;
    if (sleep) answer.sleep_hours = Number(sleep);
    if (screen) answer.screen_time_hours = Number(screen);
    onAnswer(answer);
  }

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm text-muted-foreground">{request.prompt}</p>
      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Finished today</h3>
        {request.tasks.length === 0 && <p className="text-sm text-muted-foreground">No tasks due today.</p>}
        {request.tasks.map((task) => (
          <label key={task.ref} className="flex items-start gap-2.5 text-sm">
            <Checkbox
              checked={done.has(task.ref)}
              onCheckedChange={(checked) => {
                const next = new Set(done);
                if (checked) next.add(task.ref);
                else next.delete(task.ref);
                setDone(next);
              }}
            />
            <span className="flex-1">
              {task.title}
              <span className="ml-2 text-xs text-muted-foreground">
                {task.priority ?? ""} · {formatDay(task.due)}
              </span>
            </span>
          </label>
        ))}
      </section>
      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Scorecard</h3>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {request.habits.names.map((name) => (
            <label key={name} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={habits[name]}
                disabled={Boolean(logged[name])}
                onCheckedChange={(checked) => setHabits({ ...habits, [name]: Boolean(checked) })}
              />
              {humanize(name)}
            </label>
          ))}
        </div>
        <div className="flex gap-3">
          <div className="flex flex-col gap-1">
            <Label htmlFor="sleep" className="text-xs">Sleep (hours)</Label>
            <Input id="sleep" type="number" min={0} max={24} step={0.5} className="w-28"
              value={sleep} onChange={(e) => setSleep(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="screen" className="text-xs">Screen time (hours)</Label>
            <Input id="screen" type="number" min={0} max={24} step={0.5} className="w-28"
              value={screen} onChange={(e) => setScreen(e.target.value)} />
          </div>
        </div>
      </section>
      <section className="flex flex-col gap-2">
        <Label htmlFor="checkin-text">Anything else, in your own words</Label>
        <Textarea id="checkin-text" rows={4} value={text} onChange={(e) => setText(e.target.value)}
          placeholder="Finished the SOLID notes, read 20 pages of DDIA, learned that…" />
        <p className="text-xs text-muted-foreground">
          The AI reads this to find finished tasks, habits, pages read and new to-dos. Ticks above win.
        </p>
      </section>
      <div className="flex justify-end">
        <Button onClick={submit}>Continue</Button>
      </div>
    </div>
  );
}

// --- weekly review decisions ---------------------------------------------------------------

type OverdueChoice = { decision: string; due: string };
type InboxChoice = { action: string; due: string };

const nativeSelect =
  "h-8 rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

export function DecisionsStep({ request, onAnswer }: { request: DecisionsRequest; onAnswer: (a: unknown) => void }) {
  const [overdue, setOverdue] = useState<Record<string, OverdueChoice>>(
    Object.fromEntries(request.overdue.map((r) => [r.ref, { decision: r.recommended, due: r.suggested_due ?? "" }])),
  );
  const [inbox, setInbox] = useState<Record<string, InboxChoice>>(
    Object.fromEntries(request.inbox.map((r) => [r.ref, { action: "keep", due: "" }])),
  );
  const [note, setNote] = useState("");
  const m = request.metrics;
  const review = request.review;

  function submit() {
    onAnswer({
      overdue: Object.fromEntries(
        Object.entries(overdue).map(([ref, c]) => [ref, { decision: c.decision, ...(c.due ? { due: c.due } : {}) }]),
      ),
      inbox: Object.fromEntries(
        Object.entries(inbox).map(([ref, c]) => [ref, { action: c.action, ...(c.due ? { due: c.due } : {}) }]),
      ),
      ...(note.trim() ? { note: note.trim() } : {}),
    });
  }

  return (
    <div className="flex flex-col gap-6">
      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Done" value={`${m.done} / ${m.planned}`} hint={m.completion_rate != null ? `${Math.round(m.completion_rate * 100)}%` : undefined} />
        <Stat label="Still open" value={String(m.still_open)} />
        <Stat label="Cancelled" value={String(m.cancelled)} />
        <Stat label="Scorecard days" value={`${request.habits.this_week.days_logged} / 7`}
          hint={`last week ${request.habits.last_week.days_logged}`} />
      </section>

      <section className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium">Review</h3>
          <Badge variant="outline">{review.source === "ai" ? "AI draft" : "From the numbers"}</Badge>
        </div>
        <p className="text-sm">{review.summary}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <List title="Wins" items={review.wins} />
          <List title="Missed" items={review.misses} />
          <List title="Bottlenecks" items={review.bottlenecks} />
          <List title="Next week" items={review.priorities} />
        </div>
      </section>

      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Overdue: decide each one</h3>
        {request.overdue.length === 0 && <p className="text-sm text-muted-foreground">Nothing overdue.</p>}
        {request.overdue.map((row) => {
          const choice = overdue[row.ref];
          const needsDate = choice.decision === "carry_forward" || choice.decision === "reschedule";
          return (
            <div key={row.ref} className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{row.title}</p>
                <p className="text-xs text-muted-foreground">
                  {row.priority ?? "No priority"} · was due {formatDay(row.due)} · suggested: {humanize(row.recommended)}
                  {row.reason ? ` — ${row.reason}` : ""}
                </p>
              </div>
              <select aria-label={`Decision for ${row.title}`} className={nativeSelect} value={choice.decision}
                onChange={(e) => setOverdue({ ...overdue, [row.ref]: { ...choice, decision: e.target.value } })}>
                <option value="carry_forward">Carry forward</option>
                <option value="reschedule">Reschedule</option>
                <option value="cancel">Cancel</option>
                <option value="skip">Decide later</option>
              </select>
              {needsDate && (
                <Input type="date" aria-label={`New date for ${row.title}`} className="h-8 w-40" value={choice.due}
                  onChange={(e) => setOverdue({ ...overdue, [row.ref]: { ...choice, due: e.target.value } })} />
              )}
            </div>
          );
        })}
      </section>

      {request.inbox.length > 0 && (
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Inbox triage</h3>
          {request.inbox.map((row) => {
            const choice = inbox[row.ref];
            return (
              <div key={row.ref} className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center">
                <p className="min-w-0 flex-1 truncate text-sm">{row.title}</p>
                <select aria-label={`Inbox action for ${row.title}`} className={nativeSelect} value={choice.action}
                  onChange={(e) => setInbox({ ...inbox, [row.ref]: { ...choice, action: e.target.value } })}>
                  <option value="keep">Keep in Inbox</option>
                  <option value="next">Make it Next</option>
                  <option value="cancel">Cancel</option>
                </select>
                {choice.action === "next" && (
                  <Input type="date" aria-label={`Due date for ${row.title}`} className="h-8 w-40" value={choice.due}
                    onChange={(e) => setInbox({ ...inbox, [row.ref]: { ...choice, due: e.target.value } })} />
                )}
              </div>
            );
          })}
        </section>
      )}

      <section className="flex flex-col gap-2">
        <Label htmlFor="review-note">Your note for the week (added to the Review in your words)</Label>
        <Textarea id="review-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
      </section>
      <div className="flex justify-end">
        <Button onClick={submit}>Prepare changes</Button>
      </div>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold tabular-nums">{value}</p>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function List({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <ul className="mt-1 list-disc pl-5 text-sm">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

// --- approval ------------------------------------------------------------------------------

export function ApprovalStep({ request, onAnswer }: { request: ApprovalRequest; onAnswer: (a: unknown) => void }) {
  const pending = request.proposals.filter((c) => c.status === "pending");
  // Important changes (cancelling a task) need an explicit tick.
  const [selected, setSelected] = useState<Set<string>>(
    new Set(pending.filter((c) => c.importance !== "important").map((c) => c.id)),
  );
  const [edits, setEdits] = useState<Record<string, string>>({});
  const dependants = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const card of request.proposals)
      for (const dep of card.depends_on) map.set(dep, [...(map.get(dep) ?? []), card.id]);
    return map;
  }, [request.proposals]);

  function toggle(id: string, checked: boolean) {
    const next = new Set(selected);
    if (checked) {
      next.add(id);
      // A move needs the page it links to, so selecting it selects that page's creation too.
      for (const card of request.proposals) if (card.id === id) card.depends_on.forEach((d) => next.add(d));
    } else {
      next.delete(id);
      for (const dependant of dependants.get(id) ?? []) next.delete(dependant);
    }
    setSelected(next);
  }

  function submit(approveAll: boolean) {
    const approve = approveAll ? [...selected] : [];
    const changed = Object.fromEntries(
      Object.entries(edits)
        .filter(([id, due]) => approve.includes(id) && due)
        .map(([id, due]) => [id, { new_due: due }]),
    );
    onAnswer({ approve, ...(Object.keys(changed).length ? { edits: changed } : {}) });
  }

  return (
    <div className="flex flex-col gap-4">
      {!request.writes_enabled && (
        <Notice tone="info" title="Writes are switched off">
          Approving records your decision, but nothing changes in Notion until LIFEOS_WRITES_ENABLED is true.
        </Notice>
      )}
      <div className="flex flex-col gap-2">
        {request.proposals.map((card) => (
          <ProposalCardView
            key={card.id}
            card={card}
            selected={selected.has(card.id)}
            onSelect={(checked) => toggle(card.id, checked)}
            editDue={edits[card.id]}
            onEditDue={(value) => setEdits({ ...edits, [card.id]: value })}
          />
        ))}
      </div>
      <p className="text-xs text-muted-foreground">Anything you don&apos;t approve is rejected.</p>
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={() => submit(false)}>Reject all</Button>
        <Button onClick={() => submit(true)} disabled={selected.size === 0}>
          Approve {selected.size} of {pending.length}
        </Button>
      </div>
    </div>
  );
}

// --- summary -------------------------------------------------------------------------------

function RunSummary({ run }: { run: RunView }) {
  const s = (run.summary ?? {}) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  const execution = Object.entries((s.execution ?? {}) as Record<string, number>);
  const errors = Object.entries((s.proposals?.errors ?? {}) as Record<string, string>);
  return (
    <div className="flex flex-col gap-4 text-sm">
      <Notice tone="info" title="Done">
        {s.proposals ? `${s.proposals.approved} approved, ${s.proposals.rejected} rejected.` : "Nothing needed approval."}
        {execution.length > 0 && ` Notion: ${execution.map(([k, v]) => `${v} ${k}`).join(", ")}.`}
      </Notice>
      {run.workflow === "evening_checkin" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <List title="Finished" items={s.done} />
          <List title="Left for Sunday's review" items={s.partial} />
          <List title="Proposed for tomorrow" items={s.tomorrow?.proposed} />
          <List title="Already planned for tomorrow" items={s.tomorrow?.committed} />
        </div>
      )}
      {run.workflow === "morning_briefing" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <List title="Today's priorities" items={s.priorities} />
          <List title="Proposed for today" items={s.proposed_for_today} />
          <List title="Observations" items={s.observations} />
          <List title="Next 7 days" items={(s.next_7_days ?? []).map((t: { title: string; due: string }) => `${t.title} · ${formatDay(t.due)}`)} />
        </div>
      )}
      {run.workflow === "weekly_review" && (
        <div className="flex flex-col gap-2">
          <p>{s.review?.summary}</p>
          <List title="Next week" items={s.review?.priorities} />
        </div>
      )}
      {(s.note || s.tomorrow?.note) && <p className="italic text-muted-foreground">{s.note || s.tomorrow?.note}</p>}
      <List title="Notes" items={s.notes} />
      <List title="Warnings" items={s.warnings} />
      {errors.length > 0 && <List title="Couldn't apply" items={errors.map(([, msg]) => msg)} />}
      {run.usage.llm_calls > 0 && (
        <p className="text-xs text-muted-foreground">
          AI: {run.usage.llm_calls} call{run.usage.llm_calls > 1 ? "s" : ""} · {run.usage.model} ·{" "}
          {(run.usage.llm_latency_ms / 1000).toFixed(1)} s
        </p>
      )}
    </div>
  );
}
