"use client";

import { useQuery } from "@tanstack/react-query";
import { LogOutIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { ApprovalsSheet } from "@/components/approvals-sheet";
import { DemoBanner } from "@/components/demo-banner";
import { useSession, useSignOut } from "@/components/session";
import { Badge } from "@/components/ui/badge";
import { useNotionConnection } from "@/components/notion-connection";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import type { SettingsView } from "@/lib/types";

const NAV = [
  { href: "/", label: "Command Center" },
  { href: "/weekly", label: "Weekly review" },
  { href: "/settings", label: "Settings" },
];

export function useSettings() {
  const session = useSession();
  return useQuery({
    queryKey: ["settings"],
    queryFn: () => api<SettingsView>("/settings"),
    enabled: session?.workspace !== false,
  });
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const session = useSession();
  const signOut = useSignOut();
  const settings = useSettings();
  const noWorkspace = session?.workspace === false;

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
          <Link href="/" className="font-semibold tracking-tight">LifeOS</Link>
          <nav className="flex gap-1 text-sm">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-md px-2.5 py-1.5 transition-colors hover:bg-muted ${
                  pathname === item.href ? "bg-muted font-medium" : "text-muted-foreground"
                }`}
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            {settings.data && (
              <Tooltip>
                <TooltipTrigger render={<span />}>
                  <Badge variant={settings.data.writes_enabled ? "secondary" : "outline"}>
                    {settings.data.demo ? "Demo · " : settings.data.notion_profile === "sandbox" ? "Sandbox · " : ""}
                    {settings.data.writes_enabled ? "Writes on" : "Writes off"}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  {settings.data.demo
                    ? "Approved changes are written to the sample workspace."
                    : settings.data.writes_enabled
                      ? "Approved changes are written to Notion."
                      : "Approvals are recorded, but nothing is written to Notion."}
                </TooltipContent>
              </Tooltip>
            )}
            {!noWorkspace && <ApprovalsSheet />}
            <Tooltip>
              <TooltipTrigger render={<Button variant="ghost" size="icon-sm" onClick={signOut} aria-label="Sign out" />}>
                <LogOutIcon />
              </TooltipTrigger>
              <TooltipContent>Sign out{session?.email ? ` (${session.email})` : ""}</TooltipContent>
            </Tooltip>
          </div>
        </div>
      </header>
      {settings.data?.demo && <DemoBanner />}
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
        {noWorkspace && !["/settings", "/setup"].includes(pathname) ? <NoWorkspace /> : children}
      </main>
    </div>
  );
}

function NoWorkspace() {
  const notion = useNotionConnection();
  const connected = notion.data?.connection?.status === "active";
  return (
    <Card className="mx-auto max-w-lg">
      <CardHeader>
        <CardTitle>No Notion workspace yet</CardTitle>
        <CardDescription>
          {connected
            ? "Your Notion is connected. Next, tell LifeOS which of its databases to use."
            : notion.data?.available
              ? "Your account is set up. Connect your Notion workspace to get started."
              : "Your account is set up, but it isn't connected to a Notion workspace, so there's nothing to plan or review yet. Connecting your own workspace is coming soon."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col items-start gap-3 text-sm text-muted-foreground">
        {notion.data?.available && !connected && (
          <a href="/api/v1/notion/connect" className={buttonVariants()}>Connect Notion</a>
        )}
        {connected && (
          <Link href="/setup" className={buttonVariants()}>Set up your workspace</Link>
        )}
        <p>
          You can delete your account at any time in{" "}
          <Link href="/settings" className="underline underline-offset-4">Settings</Link>.
        </p>
      </CardContent>
    </Card>
  );
}
