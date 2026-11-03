"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { KIND_LABELS } from "@/components/proposal-card";
import { api } from "@/lib/api";
import { nativeSelect } from "@/lib/styles";

const MODES: Record<string, string> = {
  approve: "Ask me first",
  suggest: "Suggest only",
  off: "Off",
};

/** How LifeOS handles each kind of change: proposed for approval (the default), shown as a
 * suggestion that is never written, or not proposed at all. Read-only in the demo. */
export function ModesEditor({ modes, readOnly }: { modes: Record<string, string>; readOnly: boolean }) {
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: (change: Record<string, string>) =>
      api<{ modes: Record<string, string> }>("/settings/modes", { method: "PUT", body: { modes: change } }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["settings"] }),
    onError: (error) => toast.error(error.message),
  });
  return (
    <div className="flex flex-col gap-2">
      <p className="text-muted-foreground">How LifeOS handles each kind of change</p>
      <div className="grid grid-cols-1 gap-x-4 gap-y-1.5 sm:grid-cols-2">
        {Object.entries(modes).map(([kind, mode]) => (
          <label key={kind} className="flex items-center justify-between gap-2">
            <span>{KIND_LABELS[kind] ?? kind}</span>
            <select aria-label={`Mode for ${KIND_LABELS[kind] ?? kind}`} className={nativeSelect} value={mode}
              disabled={readOnly || save.isPending} onChange={(e) => save.mutate({ [kind]: e.target.value })}>
              {Object.entries(MODES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              {!MODES[mode] && <option value={mode}>{mode}</option>}
            </select>
          </label>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        Suggestions appear with the others but are never written to Notion; anything off isn&apos;t proposed.
      </p>
    </div>
  );
}
