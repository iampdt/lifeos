"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Loader2Icon, SendIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ProposalCardView } from "@/components/proposal-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, stream } from "@/lib/api";
import type { ProposalCard } from "@/lib/types";

type Message =
  | { role: "user" | "assistant"; content: string }
  | { role: "activity"; content: string }
  | { role: "proposal"; card: ProposalCard };

const TOOL_LABELS: Record<string, string> = {
  get_today: "Checking today's tasks",
  get_overdue: "Checking overdue tasks",
  get_next_7_days: "Looking at the next 7 days",
  get_inbox: "Reading the Inbox",
  get_week_summary: "Summarising the week",
  get_goals: "Reading goals",
  get_reading: "Checking reading",
  propose_mark_done: "Preparing a change",
  propose_reschedule: "Preparing a change",
  propose_set_status: "Preparing a change",
  propose_create_task: "Preparing a change",
};

export function ChatPanel() {
  const client = useQueryClient();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Block body on purpose: scrollIntoView returns a Promise in newer browsers, and an effect
    // must not return anything but a cleanup function.
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  async function send(event?: React.FormEvent) {
    event?.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    const history = messages
      .filter((m): m is { role: "user" | "assistant"; content: string } => m.role === "user" || m.role === "assistant")
      .slice(-10);
    setMessages((m) => [...m, { role: "user", content: text }]);
    setInput("");
    setBusy(true);
    try {
      await stream("/chat", { message: text, history }, (event) => {
        if (event.type === "tool") {
          setMessages((m) => [...m, { role: "activity", content: TOOL_LABELS[String(event.name)] ?? "Working" }]);
        } else if (event.type === "proposal") {
          setMessages((m) => [...m, { role: "proposal", card: event.proposal as ProposalCard }]);
          client.invalidateQueries({ queryKey: ["approvals"] });
        } else if (event.type === "answer") {
          setMessages((m) => [...m, { role: "assistant", content: String(event.text) }]);
        } else if (event.type === "error") {
          setMessages((m) => [...m, { role: "assistant", content: String(event.message) }]);
        }
      });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "The connection dropped.";
      setMessages((m) => [...m, { role: "assistant", content: `Sorry, that failed: ${message}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="flex h-[36rem] flex-col">
      <CardHeader>
        <CardTitle>Chief of Staff</CardTitle>
        <CardDescription>Ask about your week, or ask for a change. Changes wait in Approvals.</CardDescription>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col gap-3">
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1" aria-live="polite">
          {messages.length === 0 && (
            <div className="flex flex-col gap-1.5 text-sm text-muted-foreground">
              <p>Try:</p>
              {["What's overdue?", "How did this week go?", "Move the SOLID task to Tuesday"].map((s) => (
                <button key={s} type="button" onClick={() => setInput(s)}
                  className="w-fit rounded-md border px-2 py-1 text-left hover:bg-muted">{s}</button>
              ))}
            </div>
          )}
          {messages.map((m, i) =>
            m.role === "proposal" ? (
              <ProposalCardView key={i} card={m.card} />
            ) : m.role === "activity" ? (
              <p key={i} className="text-xs text-muted-foreground">{m.content}…</p>
            ) : (
              <div key={i} className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
                m.role === "user" ? "self-end bg-primary text-primary-foreground" : "self-start bg-muted"}`}>
                {m.content}
              </div>
            ),
          )}
          {busy && <Loader2Icon className="size-4 animate-spin text-muted-foreground" />}
          <div ref={bottom} />
        </div>
        <form onSubmit={send} className="flex items-end gap-2">
          <Textarea
            rows={2}
            value={input}
            aria-label="Message"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Ask anything…"
            className="min-h-0 resize-none"
          />
          <Button type="submit" size="icon" disabled={busy || !input.trim()} aria-label="Send">
            <SendIcon />
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
