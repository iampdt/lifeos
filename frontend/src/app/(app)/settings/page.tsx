"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheckIcon, CircleXIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useSettings } from "@/components/app-shell";
import { type Me, useSession } from "@/components/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError, api, setCsrfToken } from "@/lib/api";
import type { NotionStatus } from "@/lib/types";

export default function SettingsPage() {
  const session = useSession();
  const settings = useSettings();
  const workspace = session?.workspace !== false;
  const notion = useQuery({
    queryKey: ["notion-status"],
    queryFn: () => api<NotionStatus>("/notion/status"),
    enabled: workspace,
  });
  const s = settings.data;
  // The shared demo account can't be deleted; wait for the settings to know whether this is it.
  const deletable = workspace ? s !== undefined && !s.demo : true;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
      {workspace && (
        <>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Safety</CardTitle>
                <CardDescription>Set on the server; shown here read-only.</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-3 text-sm">
                {!s && <Skeleton className="h-24" />}
                {s && (
                  <>
                    <Row label="Notion workspace">{s.notion_profile === "sandbox" ? "Sandbox copy" : "Life OS (live)"}</Row>
                    <Row label="Writes to Notion">
                      <Badge variant={s.writes_enabled ? "secondary" : "outline"}>{s.writes_enabled ? "On" : "Off"}</Badge>
                      <span className="ml-2 text-xs text-muted-foreground">LIFEOS_WRITES_ENABLED</span>
                    </Row>
                    <Row label="Timezone">{s.timezone}</Row>
                    <div>
                      <p className="mb-1.5 text-muted-foreground">Permission mode per action</p>
                      <div className="flex flex-wrap gap-1.5">
                        {Object.entries(s.modes).map(([kind, mode]) => (
                          <Badge key={kind} variant="outline" className="font-mono text-[11px]">{kind}: {mode}</Badge>
                        ))}
                      </div>
                      <p className="mt-1.5 text-xs text-muted-foreground">Every change needs your approval in v1.</p>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>AI</CardTitle>
                <CardDescription>Models are tried in order; if all fail, LifeOS falls back to rules.</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-3 text-sm">
                {s && (
                  <>
                    <Row label="Provider">{s.llm.configured ? s.llm.provider : "Not configured (rule-based only)"}</Row>
                    {Object.entries(s.llm.chains).map(([tier, models]) => (
                      <Row key={tier} label={tier === "fast" ? "Parsing and planning" : "Reviews"}>
                        <span className="font-mono text-xs">{models.join(" → ")}</span>
                      </Row>
                    ))}
                    <Row label="Tokens today">
                      {s.llm.tokens_today.toLocaleString()} of {s.llm.daily_token_budget.toLocaleString()}
                    </Row>
                  </>
                )}
              </CardContent>
            </Card>
          </div>
          <Card>
            <CardHeader>
              <CardTitle>Notion connection</CardTitle>
              <CardDescription>
                {notion.data ? `${notion.data.bot ?? "Integration"} in ${notion.data.workspace ?? "your workspace"}` : "Checking…"}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {notion.error && <p className="text-sm text-destructive">{notion.error.message}</p>}
              {notion.data && (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Data source</TableHead>
                      <TableHead>Schema</TableHead>
                      <TableHead>Notes</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {notion.data.data_sources.map((ds) => (
                      <TableRow key={ds.name}>
                        <TableCell className="font-medium capitalize">{ds.title || ds.name}</TableCell>
                        <TableCell>
                          {!ds.configured ? (
                            <Badge variant="outline">Not connected</Badge>
                          ) : ds.reachable === false ? (
                            <span className="inline-flex items-center gap-1 text-destructive"><CircleXIcon className="size-4" /> Unreachable</span>
                          ) : ds.ok ? (
                            <span className="inline-flex items-center gap-1"><CircleCheckIcon className="size-4" /> Matches</span>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-destructive"><CircleXIcon className="size-4" /> Drift</span>
                          )}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {ds.error ?? ds.drift?.map((d) => `${d.property}: ${d.problem}${d.detail ? ` (${d.detail})` : ""}`).join("; ")}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      )}
      {session && deletable && <AccountCard me={session} />}
    </div>
  );
}

function AccountCard({ me }: { me: Me }) {
  const [open, setOpen] = useState(false);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Account</CardTitle>
        <CardDescription>{me.name ? `${me.name} · ${me.email}` : me.email}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col items-start gap-3 text-sm">
        <p className="text-muted-foreground">
          Deleting your account removes everything LifeOS stored for it: runs, proposals and their
          history, and sessions. Nothing in Notion is changed.
        </p>
        <Button variant="destructive" onClick={() => setOpen(true)}>Delete account</Button>
      </CardContent>
      {open && <DeleteAccountDialog email={me.email} onClose={() => setOpen(false)} />}
    </Card>
  );
}

function DeleteAccountDialog({ email, onClose }: { email: string; onClose: () => void }) {
  const client = useQueryClient();
  const router = useRouter();
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const matches = confirm.trim().toLowerCase() === email.toLowerCase();

  async function remove(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      await api("/account/delete", { method: "POST", body: { confirm } });
      setCsrfToken(null);
      client.clear();
      router.replace("/login");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach the backend.");
      setPending(false);
    }
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <form onSubmit={remove} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>Delete your account?</DialogTitle>
            <DialogDescription>This can&apos;t be undone. Type your email address to confirm.</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <Label htmlFor="confirm-email">Email</Label>
            <Input id="confirm-email" autoComplete="off" placeholder={email}
              value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </div>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" variant="destructive" disabled={!matches || pending}>
              {pending ? "Deleting…" : "Delete account"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right">{children}</span>
    </div>
  );
}
