"use client";

import { WorkspaceSetup } from "@/components/workspace-setup";

export default function SetupPage() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Set up your workspace</h1>
        <p className="text-sm text-muted-foreground">
          Tell LifeOS which of your Notion databases to use and what your statuses mean. Only Tasks
          is required; it suggests the rest from names, and checks everything against Notion before
          saving.
        </p>
      </div>
      <WorkspaceSetup />
    </div>
  );
}
