"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { AIView } from "@/lib/types";

const MODES: Record<string, string> = {
  own_key: "Using your own Gemini key",
  allowance: "Using the shared daily allowance",
  allowance_used: "Today's shared allowance is used up",
  off: "Off: your routines run with rules",
};

/** An account's AI access: agreeing to the free-tier terms, usage, and its own key. Renders
 * nothing for the deployment owner, who uses the deployment's key. */
export function AIAccessCard() {
  const client = useQueryClient();
  const view = useQuery({ queryKey: ["ai"], queryFn: () => api<AIView>("/ai") });
  const update = (next: AIView) => {
    client.setQueryData(["ai"], next);
    client.invalidateQueries({ queryKey: ["settings"] });
  };
  const consent = useMutation({
    mutationFn: (on: boolean) =>
      on
        ? api<AIView>("/ai/consent", { method: "POST", body: { version: view.data?.consent?.version } })
        : api<AIView>("/ai/consent/withdraw", { method: "POST" }),
    onSuccess: update,
    onError: (error) => toast.error(error.message),
  });
  const v = view.data;
  if (!v || v.mode === "deployment" || !v.consent || !v.allowance || !v.own_key) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Your AI access
          <Badge variant={v.mode === "off" || v.mode === "allowance_used" ? "outline" : "secondary"}>
            {MODES[v.mode] ?? v.mode}
          </Badge>
        </CardTitle>
        <CardDescription>
          LifeOS reads check-ins, plans days and drafts reviews with Google&apos;s Gemini. Without it,
          everything still works with rules.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5 text-sm">
        {v.service_available && (
          <section className="flex flex-col gap-2">
            <h3 className="font-medium">Shared allowance</h3>
            <p className="text-muted-foreground">{v.consent.text}</p>
            {v.consent.outdated && <p className="text-destructive">These terms changed since you agreed; read them again.</p>}
            {v.consent.given ? (
              <div className="flex flex-wrap items-center gap-3">
                <span>
                  {v.allowance.used_today.toLocaleString()} of {v.allowance.tokens.toLocaleString()} tokens used today
                </span>
                <Button variant="ghost" onClick={() => consent.mutate(false)} disabled={consent.isPending}>Turn off</Button>
              </div>
            ) : (
              <div>
                <Button onClick={() => consent.mutate(true)} disabled={consent.isPending}>I agree, turn on AI</Button>
              </div>
            )}
          </section>
        )}
        {v.own_key.available && <OwnKey view={v} onChange={update} />}
      </CardContent>
    </Card>
  );
}

function OwnKey({ view, onChange }: { view: AIView; onChange: (v: AIView) => void }) {
  const [key, setKey] = useState("");
  const add = useMutation({
    mutationFn: () => api<AIView>("/ai/key", { method: "PUT", body: { key } }),
    onSuccess: (next) => {
      setKey("");
      onChange(next);
      toast.success("Your key works and is saved.");
    },
    onError: (error) => toast.error(error.message),
  });
  const remove = useMutation({
    mutationFn: () => api<AIView>("/ai/key", { method: "DELETE" }),
    onSuccess: onChange,
    onError: (error) => toast.error(error.message),
  });
  const own = view.own_key!;

  return (
    <section className="flex flex-col gap-2">
      <h3 className="font-medium">Your own Gemini key</h3>
      <p className="text-muted-foreground">
        A free key from{" "}
        <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer" className="underline underline-offset-4">
          Google AI Studio
        </a>{" "}
        gives you full use under your own Google account&apos;s terms. LifeOS checks it with Google and stores it
        encrypted; it&apos;s never shown again.
      </p>
      {own.hint ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-mono">…{own.hint}</span>
          {own.status === "rejected" ? (
            <Badge variant="destructive">Google stopped accepting it</Badge>
          ) : (
            <span className="text-muted-foreground">{own.tokens_today.toLocaleString()} tokens today</span>
          )}
          <Button variant="ghost" onClick={() => remove.mutate()} disabled={remove.isPending}>Remove</Button>
        </div>
      ) : (
        <form
          className="flex flex-col gap-2 sm:flex-row sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <div className="flex flex-1 flex-col gap-1.5">
            <Label htmlFor="gemini-key">API key</Label>
            <Input id="gemini-key" type="password" autoComplete="off" spellCheck={false}
              value={key} onChange={(e) => setKey(e.target.value)} placeholder="AIza…" />
          </div>
          <Button type="submit" disabled={add.isPending || key.trim().length < 20}>
            {add.isPending ? "Checking with Google…" : "Add key"}
          </Button>
        </form>
      )}
    </section>
  );
}
