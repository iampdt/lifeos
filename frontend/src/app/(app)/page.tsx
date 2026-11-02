"use client";

import { MoonIcon, SunriseIcon } from "lucide-react";
import { useState } from "react";

import { ChatPanel } from "@/components/chat-panel";
import { RunDialog } from "@/components/run-dialog";
import { RunsCard } from "@/components/runs-card";
import { TodayCard } from "@/components/today-card";
import { Button } from "@/components/ui/button";

export default function CommandCenter() {
  const [workflow, setWorkflow] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Command Center</h1>
          <p className="text-sm text-muted-foreground">Notion stays the source of truth. LifeOS prepares, you decide.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setWorkflow("morning_briefing")}>
            <SunriseIcon /> Morning briefing
          </Button>
          <Button onClick={() => setWorkflow("evening_checkin")}>
            <MoonIcon /> Evening check-in
          </Button>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex flex-col gap-6">
          <TodayCard />
          <RunsCard />
        </div>
        <ChatPanel />
      </div>
      {workflow && <RunDialog key={workflow} workflow={workflow} onClose={() => setWorkflow(null)} />}
    </div>
  );
}
