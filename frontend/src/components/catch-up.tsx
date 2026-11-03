"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import { humanize } from "@/lib/format";

let asked = false; // once per page load

/** Starts any routine that's due but hasn't run today (the hourly tick can run late). */
export function useCatchUp(enabled: boolean) {
  const client = useQueryClient();
  useEffect(() => {
    if (!enabled || asked) return;
    asked = true;
    api<{ started: { job: string }[] }>("/schedule/catch-up", { method: "POST" })
      .then((result) => {
        if (!result.started.length) return;
        client.invalidateQueries();
        const names = result.started.map((r) => humanize(r.job).toLowerCase()).join(" and ");
        toast.success(`Started your ${names}.`);
      })
      .catch(() => {}); // the hourly tick will get to it
  }, [enabled, client]);
}
