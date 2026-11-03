export function formatDay(iso: string | null | undefined, options: Intl.DateTimeFormatOptions = {}) {
  if (!iso) return "No date";
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    ...options,
  });
}

export function formatTime(iso: string | null | undefined) {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export const WORKFLOW_LABELS: Record<string, string> = {
  evening_checkin: "Evening check-in",
  morning_briefing: "Morning briefing",
  weekly_review: "Weekly review",
  chat: "Chat",
};

/** A key like `carry_forward` as words ("Carry forward"). Habit and metric names come from the
 * workspace instead (a scorecard card's `labels`). */
export function humanize(key: string) {
  const text = key.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
