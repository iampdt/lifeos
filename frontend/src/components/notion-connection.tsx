"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { MODULE_LABELS, useWorkspace } from "@/components/workspace-notice";
import { ApiError, api } from "@/lib/api";
import type { DataSourceList, NotionConnectionView, WorkspaceView } from "@/lib/types";

export function useNotionConnection() {
  return useQuery({
    queryKey: ["notion-connection"],
    queryFn: () => api<NotionConnectionView>("/notion/connection"),
  });
}

// The API sends a finished connection attempt back to /settings with one of these.
const CALLBACK_ERRORS: Record<string, string> = {
  cancelled: "Connecting Notion was cancelled.",
  expired: "That attempt expired. Try again.",
  wrong_account: "Finish connecting in the same account you started from.",
  exchange_failed: "Notion didn't accept the connection. Try again.",
};

/** The account's own Notion connection: connect, see what LifeOS can reach, disconnect.
 * Renders nothing when the deployment doesn't offer Notion connections. */
export function NotionConnectionCard({ owner }: { owner: boolean }) {
  const view = useNotionConnection();
  if (!view.data?.available) return null;
  const connection = view.data.connection;
  const active = connection?.status === "active";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Your Notion
          {connection && (
            <Badge variant={active ? "secondary" : "destructive"}>
              {active ? "Connected" : "Needs reconnecting"}
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          {!connection
            ? "Connect your own Notion workspace. On Notion's page you choose which pages LifeOS can see."
            : active
              ? `Connected to ${connection.workspace_name ?? "your workspace"}.`
              : "Notion stopped accepting LifeOS's access, for example because it was removed in Notion. Connect again to continue."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        <Suspense fallback={null}>
          <CallbackNotice />
        </Suspense>
        {owner && <OwnerMove />}
        {active && <DataSources />}
        <div className="flex flex-wrap gap-2">
          {active && !owner && (
            <Link href="/setup" className={buttonVariants()}>Choose databases</Link>
          )}
          {/* A full-page navigation: the API redirects to Notion's consent page. */}
          <a
            href="/api/v1/notion/connect"
            className={buttonVariants({ variant: connection && active ? "outline" : "default" })}
          >
            {!connection ? "Connect Notion" : active ? "Change pages or workspace" : "Connect again"}
          </a>
          {connection && <DisconnectButton />}
        </div>
      </CardContent>
    </Card>
  );
}

function CallbackNotice() {
  const params = useSearchParams();
  if (params.get("notion") === "connected") {
    return <p className="text-emerald-600 dark:text-emerald-400">Notion is connected.</p>;
  }
  const code = params.get("notion_error");
  if (!code) return null;
  return (
    <p role="alert" className="text-destructive">
      {CALLBACK_ERRORS[code] ?? "Connecting Notion failed. Try again."}
    </p>
  );
}

const SHOWN = 8;

function DataSources() {
  const list = useQuery({
    queryKey: ["notion-data-sources"],
    queryFn: () => api<DataSourceList>("/notion/data-sources"),
  });
  if (list.isPending) return <Skeleton className="h-10" />;
  if (list.error) return <p className="text-destructive">{list.error.message}</p>;
  const sources = list.data.data_sources;
  if (sources.length === 0) {
    return (
      <p className="text-muted-foreground">
        LifeOS can&apos;t see any databases yet. Use &ldquo;Change pages or workspace&rdquo; to share
        the pages that hold them.
      </p>
    );
  }
  const more = sources.length - SHOWN;
  return (
    <div>
      <p className="mb-1.5 text-muted-foreground">
        LifeOS can see {sources.length}
        {list.data.truncated ? "+" : ""} database{sources.length === 1 ? "" : "s"}.
      </p>
      <div className="flex flex-wrap gap-1.5">
        {sources.slice(0, SHOWN).map((source) => (
          <Badge key={source.id} variant="outline">{source.title || "Untitled"}</Badge>
        ))}
        {more > 0 && <Badge variant="ghost">and {more} more</Badge>}
      </div>
    </div>
  );
}

function DisconnectButton() {
  const client = useQueryClient();
  const disconnect = useMutation({
    mutationFn: () => api<{ revoked: boolean }>("/notion/disconnect", { method: "POST" }),
    onSuccess: (result) => {
      client.invalidateQueries({ queryKey: ["notion-connection"] });
      client.removeQueries({ queryKey: ["notion-data-sources"] });
      if (result.revoked) toast.success("Notion is disconnected.");
      else toast.warning("Disconnected here. If LifeOS still appears in Notion's Connections settings, remove it there.");
    },
    onError: (error) => toast.error(error.message),
  });
  return (
    <Button variant="ghost" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>
      {disconnect.isPending ? "Disconnecting…" : "Disconnect"}
    </Button>
  );
}

/** The owner: move the routines from the environment's workspace to this connection. */
function OwnerMove() {
  const client = useQueryClient();
  const workspace = useWorkspace();
  const [problems, setProblems] = useState<string[]>([]);
  const move = useMutation({
    mutationFn: () => api<WorkspaceView>("/workspace/import-environment", { method: "POST" }),
    onSuccess: () => {
      setProblems([]);
      client.invalidateQueries();
      toast.success("Your routines now run on this connection.");
    },
    onError: (error) => {
      const details = error instanceof ApiError && Array.isArray(error.details) ? error.details : [];
      setProblems(details.map((d: { module: string | null; problem: string }) =>
        `${d.module ? `${MODULE_LABELS[d.module] ?? d.module}: ` : ""}${d.problem}`));
      toast.error(error.message);
    },
  });
  const view = workspace.data;
  if (view?.source === "account") {
    return <p className="text-muted-foreground">Your routines run on this connection.</p>;
  }
  return (
    <div className="flex flex-col items-start gap-2">
      <p className="text-muted-foreground">
        Your routines still run on the workspace this deployment is configured with
        (<span className="font-mono text-xs">NOTION_TOKEN</span>). Moving them keeps your setup and times; share
        every database it uses on Notion&apos;s page first.
      </p>
      {view?.can_import && (
        <Button variant="outline" onClick={() => move.mutate()} disabled={move.isPending}>
          {move.isPending ? "Checking against Notion…" : "Move my routines to this connection"}
        </Button>
      )}
      {problems.length > 0 && (
        <ul className="list-disc pl-5 text-destructive">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
      )}
    </div>
  );
}

