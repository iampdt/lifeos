"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLinkIcon, Loader2Icon, PlusIcon, RefreshCwIcon, SearchIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/format";
import type { Citation, KnowledgeAnswer, KnowledgePageView, KnowledgeView, PageSearchResult } from "@/lib/types";

export default function KnowledgePage() {
  const knowledge = useQuery({ queryKey: ["knowledge"], queryFn: () => api<KnowledgeView>("/knowledge") });
  const pages = knowledge.data?.pages ?? [];
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Knowledge</h1>
        <p className="text-sm text-muted-foreground">
          Ask questions of the Notion pages you choose: notes, plans, documents. Answers cite the passages they use.
        </p>
      </div>
      <AskCard disabled={knowledge.isSuccess && pages.length === 0} />
      <Card>
        <CardHeader>
          <CardTitle>Indexed pages</CardTitle>
          {knowledge.data && (
            <CardDescription>
              {pages.length} of {knowledge.data.limits.pages} pages. LifeOS keeps a copy of their text to search it;
              refresh a page after you change it in Notion.
            </CardDescription>
          )}
        </CardHeader>
        <CardContent className="flex flex-col gap-3 text-sm">
          {knowledge.isPending && <Skeleton className="h-16 w-full" />}
          {knowledge.error && <p role="alert" className="text-destructive">{knowledge.error.message}</p>}
          {knowledge.data && pages.length === 0 && <p className="text-muted-foreground">No pages yet. Add one below.</p>}
          {pages.length > 0 && (
            <ul className="flex flex-col gap-1">
              {pages.map((page) => <PageRow key={page.id} page={page} editable={knowledge.data!.editable} />)}
            </ul>
          )}
          {knowledge.data?.editable && pages.length < knowledge.data.limits.pages && <AddPage />}
          {knowledge.data && !knowledge.data.editable && (
            <p className="text-xs text-muted-foreground">The shared demo&apos;s pages can&apos;t be changed.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function AskCard({ disabled }: { disabled: boolean }) {
  const [question, setQuestion] = useState("");
  const ask = useMutation({
    mutationFn: (q: string) => api<KnowledgeAnswer>("/knowledge/ask", { method: "POST", body: { question: q } }),
    onError: (error) => toast.error(error.message),
  });
  const result = ask.data;

  function submit(event?: React.FormEvent) {
    event?.preventDefault();
    const q = question.trim();
    if (q && !ask.isPending) ask.mutate(q);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Ask</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        <form onSubmit={submit} className="flex items-end gap-2">
          <Textarea rows={2} value={question} aria-label="Question" maxLength={500} disabled={disabled}
            placeholder={disabled ? "Add a page below first" : "What do my notes say about…"}
            onChange={(e) => setQuestion(e.target.value)} className="min-h-0 resize-none"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }} />
          <Button type="submit" disabled={disabled || !question.trim() || ask.isPending}>
            {ask.isPending ? <Loader2Icon className="animate-spin" /> : <SearchIcon />} Ask
          </Button>
        </form>
        {result && (
          <div className="flex flex-col gap-3" aria-live="polite">
            {result.answer ? (
              <p className={`whitespace-pre-wrap ${result.found === false ? "text-muted-foreground" : ""}`}>
                <Cited text={result.answer} citations={result.citations} />
              </p>
            ) : (
              <p className="text-muted-foreground">The closest passages in your pages:</p>
            )}
            {result.citations.length > 0 && (
              <ol className="flex flex-col gap-2">
                {result.citations.map((c) => <Source key={c.n} citation={c} />)}
              </ol>
            )}
            {result.warnings.map((w) => <p key={w} className="text-xs text-muted-foreground">{w}</p>)}
            <p className="text-xs text-muted-foreground">
              {result.search === "hybrid" ? "Searched by meaning and words." : "Searched by words."}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** The answer with its [n] markers as links to the numbered sources below it. */
function Cited({ text, citations }: { text: string; citations: Citation[] }) {
  const known = new Set(citations.map((c) => c.n));
  return text.split(/(\[\d+\])/).map((part, i) => {
    const n = Number(part.match(/^\[(\d+)\]$/)?.[1]);
    if (!known.has(n)) return <span key={i}>{part}</span>;
    return (
      <a key={i} href={`#source-${n}`} className="align-super text-xs text-primary hover:underline">[{n}]</a>
    );
  });
}

function Source({ citation }: { citation: Citation }) {
  return (
    <li id={`source-${citation.n}`} className="rounded-lg border p-3">
      <div className="flex items-baseline gap-2">
        <span className="text-xs text-muted-foreground tabular-nums">{citation.n}.</span>
        {citation.url ? (
          <a href={citation.url} target="_blank" rel="noreferrer" className="font-medium hover:underline">
            {citation.title}
          </a>
        ) : (
          <span className="font-medium">{citation.title}</span>
        )}
        {citation.heading && <span className="truncate text-xs text-muted-foreground">› {citation.heading}</span>}
      </div>
      <p className="mt-1 text-muted-foreground">{citation.excerpt}</p>
    </li>
  );
}

function PageRow({ page, editable }: { page: KnowledgePageView; editable: boolean }) {
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: ["knowledge"] });
  const reindex = useMutation({
    mutationFn: () => api<KnowledgePageView & { changed: boolean; warnings: string[] }>(
      `/knowledge/pages/${page.id}/refresh`, { method: "POST" }),
    onSuccess: (result) => {
      toast.success(result.changed ? "Updated from Notion." : "Already up to date.");
      result.warnings.forEach((w) => toast.warning(w));
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const remove = useMutation({
    mutationFn: () => api(`/knowledge/pages/${page.id}`, { method: "DELETE" }),
    onSuccess: refresh,
    onError: (error) => toast.error(error.message),
  });
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md px-2 py-1.5 hover:bg-muted">
      <span className="min-w-0 flex-1 truncate">
        {page.url ? (
          <a href={page.url} target="_blank" rel="noreferrer" className="hover:underline">{page.title}</a>
        ) : page.title}
      </span>
      <span className="text-xs text-muted-foreground">
        {page.passages} passage{page.passages === 1 ? "" : "s"} · {formatTime(page.indexed_at)}
      </span>
      {!page.embedded && <Badge variant="outline">Words only</Badge>}
      {page.truncated && <Badge variant="outline">Partly indexed</Badge>}
      {editable && (
        <div className="flex gap-1">
          <Button size="icon-sm" variant="ghost" aria-label={`Refresh ${page.title}`} disabled={reindex.isPending}
            onClick={() => reindex.mutate()}>
            <RefreshCwIcon className={reindex.isPending ? "animate-spin" : ""} />
          </Button>
          <Button size="icon-sm" variant="ghost" aria-label={`Remove ${page.title}`} disabled={remove.isPending}
            onClick={() => remove.mutate()}>
            <Trash2Icon />
          </Button>
        </div>
      )}
    </li>
  );
}

function AddPage() {
  const client = useQueryClient();
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const results = useQuery({
    queryKey: ["knowledge-search", submitted],
    queryFn: () => api<{ results: PageSearchResult[] }>(`/knowledge/search-pages?q=${encodeURIComponent(submitted)}`),
    enabled: submitted.length > 0,
  });
  const add = useMutation({
    mutationFn: (pageId: string) =>
      api<KnowledgePageView & { warnings: string[] }>("/knowledge/pages", { method: "POST", body: { page_id: pageId } }),
    onSuccess: (page) => {
      toast.success(`Indexed “${page.title}”.`);
      page.warnings.forEach((w) => toast.warning(w));
      client.invalidateQueries({ queryKey: ["knowledge"] });
      client.invalidateQueries({ queryKey: ["knowledge-search"] });
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <div className="flex flex-col gap-2 border-t pt-3">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setSubmitted(query.trim()); }}>
        <Input aria-label="Find a Notion page" placeholder="Find a Notion page by title" value={query} maxLength={100}
          onChange={(e) => setQuery(e.target.value)} />
        <Button type="submit" variant="outline" disabled={!query.trim()}><SearchIcon /> Find</Button>
      </form>
      {results.isFetching && <Skeleton className="h-8 w-full" />}
      {results.error && <p role="alert" className="text-destructive">{results.error.message}</p>}
      {results.data?.results.length === 0 && (
        <p className="text-muted-foreground">
          No page with that title. LifeOS sees only the pages shared with it in Notion.
        </p>
      )}
      {results.data && results.data.results.length > 0 && (
        <ul className="flex flex-col gap-1">
          {results.data.results.map((r) => (
            <li key={r.page_id} className="flex items-center gap-2 rounded-md px-2 py-1 hover:bg-muted">
              <span className="min-w-0 flex-1 truncate">{r.title}</span>
              {r.url && (
                <a href={r.url} target="_blank" rel="noreferrer" aria-label={`Open ${r.title} in Notion`}
                  className="text-muted-foreground hover:text-foreground">
                  <ExternalLinkIcon className="size-3.5" />
                </a>
              )}
              {r.indexed ? (
                <Badge variant="secondary">Indexed</Badge>
              ) : (
                <Button size="xs" variant="outline" disabled={add.isPending} onClick={() => add.mutate(r.page_id)}>
                  {add.isPending && add.variables === r.page_id ? <Loader2Icon className="animate-spin" /> : <PlusIcon />} Add
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
