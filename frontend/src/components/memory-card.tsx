"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckIcon, PencilIcon, PlusIcon, Trash2Icon, XIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { MemoryNote, MemoryView } from "@/lib/types";

export function useMemory() {
  return useQuery({ queryKey: ["memory"], queryFn: () => api<MemoryView>("/memory") });
}

/** Save a note, from this card or from a suggestion in chat. */
export function useRemember() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (note: { text: string; source?: "you" | "chat" }) =>
      api<MemoryNote>("/memory", { method: "POST", body: note }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["memory"] }),
    onError: (error) => toast.error(error.message),
  });
}

/** Notes LifeOS keeps in mind when it plans, reviews and chats. */
export function MemoryCard() {
  const memory = useMemory();
  const remember = useRemember();
  const [draft, setDraft] = useState("");
  const data = memory.data;
  const full = data ? data.notes.length >= data.limits.notes : false;

  function add(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    remember.mutate({ text }, { onSuccess: () => setDraft("") });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Memory</CardTitle>
        <CardDescription>
          Preferences and context LifeOS keeps in mind when it plans your days, drafts reviews and answers in chat,
          for example &ldquo;Deep work before noon&rdquo; or &ldquo;Fridays are light&rdquo;. They never change the
          rules or approve anything.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        {memory.isPending && <Skeleton className="h-16 w-full" />}
        {memory.error && <p role="alert" className="text-destructive">{memory.error.message}</p>}
        {data && data.notes.length === 0 && <p className="text-muted-foreground">Nothing saved yet.</p>}
        {data && data.notes.length > 0 && (
          <ul className="flex flex-col gap-1">
            {data.notes.map((note) => (
              <NoteRow key={note.id} note={note} maxLength={data.limits.chars} readOnly={!data.editable} />
            ))}
          </ul>
        )}
        {data?.editable && (
          <form onSubmit={add} className="flex gap-2">
            <Input aria-label="New note" placeholder={full ? "Memory is full" : "Something to remember"}
              value={draft} maxLength={data.limits.chars} disabled={full} onChange={(e) => setDraft(e.target.value)} />
            <Button type="submit" variant="outline" disabled={full || !draft.trim() || remember.isPending}>
              <PlusIcon /> Add
            </Button>
          </form>
        )}
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.editable
              ? `${data.notes.length} of ${data.limits.notes} notes. Notes are sent to the AI with your routines and chat.`
              : "The shared demo's memory can't be changed."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function NoteRow({ note, maxLength, readOnly }: { note: MemoryNote; maxLength: number; readOnly: boolean }) {
  const client = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const refresh = () => client.invalidateQueries({ queryKey: ["memory"] });
  const save = useMutation({
    mutationFn: (text: string) => api<MemoryNote>(`/memory/${note.id}`, { method: "PUT", body: { text } }),
    onSuccess: () => {
      setEditing(null);
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const remove = useMutation({
    mutationFn: () => api(`/memory/${note.id}`, { method: "DELETE" }),
    onSuccess: refresh,
    onError: (error) => toast.error(error.message),
  });

  if (editing !== null) {
    return (
      <li>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (editing.trim()) save.mutate(editing); }}>
          <Input aria-label="Edit note" value={editing} maxLength={maxLength} autoFocus
            onChange={(e) => setEditing(e.target.value)} />
          <Button type="submit" size="icon" variant="ghost" aria-label="Save note" disabled={!editing.trim() || save.isPending}>
            <CheckIcon />
          </Button>
          <Button type="button" size="icon" variant="ghost" aria-label="Cancel" onClick={() => setEditing(null)}>
            <XIcon />
          </Button>
        </form>
      </li>
    );
  }
  return (
    <li className="group flex items-center gap-2 rounded-md px-2 py-1 hover:bg-muted">
      <span className="min-w-0 flex-1">{note.text}</span>
      {note.source === "chat" && <Badge variant="outline">From chat</Badge>}
      {!readOnly && (
        <>
          <Button size="icon-sm" variant="ghost" aria-label={`Edit “${note.text}”`} onClick={() => setEditing(note.text)}>
            <PencilIcon />
          </Button>
          <Button size="icon-sm" variant="ghost" aria-label={`Delete “${note.text}”`} disabled={remove.isPending}
            onClick={() => remove.mutate()}>
            <Trash2Icon />
          </Button>
        </>
      )}
    </li>
  );
}
