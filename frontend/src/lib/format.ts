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

export const HABIT_LABELS: Record<string, string> = {
  woke_on_time: "Woke on time",
  dsa: "DSA",
  deep_work: "Deep work",
  technical_learning: "Technical learning",
  workout: "Workout",
  protein_target: "Protein target",
  reading: "Reading",
  speech_practice: "Speech practice",
  daily_highlight: "Daily highlight",
};

export function humanize(key: string) {
  const text = HABIT_LABELS[key] ?? key.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
