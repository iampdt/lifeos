"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RotateCcwIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

/** Shown on every page of the public demo. */
export function DemoBanner() {
  const client = useQueryClient();
  const reset = useMutation({
    mutationFn: () => api("/demo/reset", { method: "POST" }),
    onSuccess: () => {
      client.invalidateQueries();
      toast.success("The sample workspace was reset.");
    },
    onError: (error) => toast.error(error.message),
  });
  return (
    <div className="border-b bg-muted/50">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm">
        <span className="font-medium">Demo</span>
        <span className="text-muted-foreground">
          A sample workspace instead of Notion, shared with other visitors and reset when idle.
          <span className="hidden sm:inline">
            {" "}Try the evening check-in, ask the chat to move a task, or run the weekly review.
          </span>
        </span>
        <Button size="xs" variant="outline" className="ml-auto" disabled={reset.isPending}
          onClick={() => reset.mutate()}>
          <RotateCcwIcon /> Reset demo
        </Button>
      </div>
    </div>
  );
}
