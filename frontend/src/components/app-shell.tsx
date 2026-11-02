"use client";

import { useQuery } from "@tanstack/react-query";
import { LogOutIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { ApprovalsSheet } from "@/components/approvals-sheet";
import { DemoBanner } from "@/components/demo-banner";
import { useSession, useSignOut } from "@/components/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import type { SettingsView } from "@/lib/types";

const NAV = [
  { href: "/", label: "Command Center" },
  { href: "/weekly", label: "Weekly review" },
  { href: "/settings", label: "Settings" },
];

export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsView>("/settings") });
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const session = useSession();
  const signOut = useSignOut();
  const settings = useSettings();

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
            <ApprovalsSheet />
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
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
