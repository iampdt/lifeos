"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownIcon, ArrowUpIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, api } from "@/lib/api";
import { humanize } from "@/lib/format";
import { nativeSelect } from "@/lib/styles";
import type {
  DataSourceSchema,
  FieldRule,
  ModuleCatalogue,
  ModuleMapping,
  PropertyMapping,
  SchemaProperty,
  WorkspaceSettingsView,
  WorkspaceSuggestion,
  WorkspaceView,
} from "@/lib/types";

const MEANINGS: Record<string, string> = {
  next: "To do", doing: "In progress", done: "Done", inbox: "Inbox (not triaged)",
  waiting: "Waiting", cancelled: "Cancelled", upcoming: "Upcoming", current: "Current",
  closed: "Closed", active: "Active", planned: "Planned", dropped: "Dropped",
  want: "Want to read", reading: "Reading", finished: "Finished", abandoned: "Abandoned",
};

const FIELD_LABELS: Record<string, string> = {
  title: "Name", due: "Due date", day: "Date", week: "Link to weekly page",
  month: "Link to monthly page", goal: "Link to goal", tasks: "Link to tasks",
  created: "Created time", pages_read: "Pages read", up_next: "Up next",
  success_metric: "Success metric", review_notes: "Review notes",
};

// Formula fields some setups use to cross-check week and month keys; not needed for a new one.
const HIDDEN_FIELDS = new Set(["week_key", "month_key"]);

const TITLE_FORMATS: { format: string; example: string }[] = [
  { format: "%Y-%m-%d", example: "2026-11-03" },
  { format: "%d/%m/%Y", example: "03/11/2026" },
  { format: "%m/%d/%Y", example: "11/03/2026" },
  { format: "%B %d, %Y", example: "November 03, 2026" },
  { format: "%d %B %Y", example: "03 November 2026" },
];

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

type Issue = { module: string | null; field: string | null; problem: string };

function browserZone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

function zones(current: string): string[] {
  const all = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  return all.includes(current) ? all : [current, ...all];
}

function fieldKey(name: string, taken: Set<string>) {
  let key = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "field";
  if (/^\d/.test(key)) key = `f_${key}`;
  const base = key;
  for (let n = 2; taken.has(key); n++) key = `${base}_${n}`;
  return key;
}

/** Loads everything the form needs, then mounts it with that data as its starting state. */
export function WorkspaceSetup() {
  const catalogue = useQuery({ queryKey: ["workspace-modules"], queryFn: () => api<ModuleCatalogue>("/workspace/modules") });
  const existing = useQuery({ queryKey: ["workspace"], queryFn: () => api<WorkspaceView>("/workspace") });
  const suggestion = useQuery({
    queryKey: ["workspace-suggestion"],
    queryFn: () => api<WorkspaceSuggestion>("/workspace/suggestion"),
    enabled: existing.data?.source !== "environment",
    staleTime: Infinity,
  });

  const error = catalogue.error ?? existing.error ?? suggestion.error;
  if (error) return <p role="alert" className="text-sm text-destructive">{error.message}</p>;
  if (existing.data?.source === "environment") {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Workspace</CardTitle>
          <CardDescription>This deployment&apos;s own workspace is configured on the server.</CardDescription>
        </CardHeader>
      </Card>
    );
  }
  if (!catalogue.data || !existing.data || !suggestion.data) {
    return <p className="text-sm text-muted-foreground">Looking at your Notion workspace…</p>;
  }
  return <SetupForm catalogue={catalogue.data} existing={existing.data} suggestion={suggestion.data} />;
}

function SetupForm({ catalogue, existing, suggestion }: {
  catalogue: ModuleCatalogue;
  existing: WorkspaceView;
  suggestion: WorkspaceSuggestion;
}) {
  const client = useQueryClient();
  const router = useRouter();
  const saved = existing.source === "account";
  const [modules, setModules] = useState<Record<string, ModuleMapping | null>>(() =>
    Object.fromEntries(Object.keys(catalogue).map((m) => [m, (saved ? existing.modules : suggestion.modules)[m] ?? null])),
  );
  const [schemas, setSchemas] = useState<Record<string, DataSourceSchema>>(suggestion.schemas);
  const [settings, setSettings] = useState<WorkspaceSettingsView>(
    existing.settings ?? { timezone: browserZone(), max_major_per_day: 3, review_weekday: 6, review_time: "18:00" },
  );
  const [issues, setIssues] = useState<Issue[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function chooseSource(module: string, sourceId: string) {
    if (!sourceId) {
      setModules({ ...modules, [module]: null });
      return;
    }
    const result = await api<{ suggestion: ModuleMapping; schema: DataSourceSchema }>(
      `/workspace/data-sources/${sourceId}?module=${module}`,
    );
    setSchemas({ ...schemas, [sourceId]: result.schema });
    setModules({ ...modules, [module]: result.suggestion });
  }

  async function save() {
    setPending(true);
    setIssues([]);
    setMessage(null);
    const chosen = Object.fromEntries(Object.entries(modules).filter(([, m]) => m !== null));
    try {
      await api("/workspace", { method: "PUT", body: { modules: chosen, settings } });
      await client.invalidateQueries();
      router.push("/");
    } catch (err) {
      if (err instanceof ApiError && Array.isArray(err.details)) setIssues(err.details as Issue[]);
      setMessage(err instanceof Error ? err.message : "Saving failed.");
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {suggestion.from_template && !saved && (
        <p className="rounded-lg border bg-muted/40 p-3 text-sm">
          Your workspace comes from the LifeOS template, so everything below is already matched. Review it and save.
        </p>
      )}
      {suggestion.data_sources.length === 0 && (
        <p role="alert" className="text-sm text-destructive">
          LifeOS can&apos;t see any databases. Connect Notion again and share the pages that hold them.
        </p>
      )}
      {Object.entries(catalogue).map(([name, module]) => (
        <ModuleCard
          key={name}
          name={name}
          module={module}
          mapping={modules[name]}
          sources={suggestion.data_sources}
          schemas={schemas}
          issues={issues.filter((i) => i.module === name)}
          onSource={(id) => chooseSource(name, id).catch((e) => setMessage(e.message))}
          onChange={(m) => setModules({ ...modules, [name]: m })}
        />
      ))}
      <SettingsCard settings={settings} onChange={setSettings} />
      {issues.some((i) => !i.module) && (
        <ul className="text-sm text-destructive">{issues.filter((i) => !i.module).map((i) => <li key={i.problem}>{i.problem}</li>)}</ul>
      )}
      {message && <p role="alert" className="text-sm text-destructive">{message}</p>}
      <div className="flex justify-end">
        <Button size="lg" onClick={save} disabled={pending || !modules.tasks}>
          {pending ? "Checking against Notion…" : "Save and start"}
        </Button>
      </div>
    </div>
  );
}

function ModuleCard({ name, module, mapping, sources, schemas, issues, onSource, onChange }: {
  name: string;
  module: ModuleCatalogue[string];
  mapping: ModuleMapping | null;
  sources: { id: string; title: string }[];
  schemas: Record<string, DataSourceSchema>;
  issues: Issue[];
  onSource: (id: string) => void;
  onChange: (m: ModuleMapping) => void;
}) {
  const sourceId = mapping?.data_source_id ?? "";
  // A saved mapping can point at a database the suggestion didn't look at.
  const fetched = useQuery({
    queryKey: ["workspace-schema", sourceId, name],
    queryFn: () => api<{ schema: DataSourceSchema }>(`/workspace/data-sources/${sourceId}?module=${name}`),
    enabled: Boolean(sourceId) && !schemas[sourceId],
  });
  const schema = schemas[sourceId] ?? fetched.data?.schema;

  function setField(field: string, propName: string) {
    if (!mapping) return;
    const properties = { ...mapping.properties };
    const prop = schema?.properties.find((p) => p.name === propName);
    if (!prop) {
      delete properties[field];
    } else {
      const same = properties[field]?.name === prop.name;
      const next: PropertyMapping = { name: prop.name, type: prop.type };
      if (field === "priority") {
        next.values = same ? properties[field].values : Object.fromEntries(prop.options.map((o, i) => [`p${i}`, o]));
      } else if (module.fields[field].needs.length || module.fields[field].allows.length) {
        next.values = same ? properties[field].values : {};
      }
      properties[field] = next;
    }
    onChange({ ...mapping, properties });
  }

  function setValues(field: string, values: Record<string, string>) {
    if (!mapping) return;
    onChange({ ...mapping, properties: { ...mapping.properties, [field]: { ...mapping.properties[field], values } } });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {module.label}
          {module.required ? <Badge variant="secondary">Required</Badge> : <Badge variant="outline">Optional</Badge>}
        </CardTitle>
        <CardDescription>{DESCRIPTIONS[name]}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
          <Label htmlFor={`source-${name}`} className="sm:w-40">Database</Label>
          <select id={`source-${name}`} className={nativeSelect} value={sourceId} onChange={(e) => onSource(e.target.value)}>
            <option value="">{module.required ? "Choose a database…" : "Not used"}</option>
            {sources.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
          </select>
        </div>
        {mapping && !schema && <p className="text-muted-foreground">Reading the database…</p>}
        {mapping && schema && (
          <>
            {Object.entries(module.fields)
              .filter(([field]) => !HIDDEN_FIELDS.has(field))
              .map(([field, rule]) => (
                <FieldRow key={field} field={field} rule={rule} schema={schema}
                  current={mapping.properties[field]}
                  onProperty={(p) => setField(field, p)}
                  onValues={(v) => setValues(field, v)} />
              ))}
            {module.free_fields && (
              <HabitFields mapping={mapping} schema={schema} onChange={onChange} />
            )}
          </>
        )}
        {issues.length > 0 && (
          <ul className="list-disc pl-5 text-destructive">
            {issues.map((i) => <li key={`${i.field}-${i.problem}`}>{i.field ? `${FIELD_LABELS[i.field] ?? humanize(i.field)}: ` : ""}{i.problem}</li>)}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

const DESCRIPTIONS: Record<string, string> = {
  tasks: "Your to-dos. LifeOS plans days from them and proposes status and date changes.",
  habits: "One row per day. Every checkbox is a habit and every number a metric the check-in can fill in.",
  weeks: "One page per week, closed with a review at the weekly review.",
  months: "One page per month, linked from tasks.",
  goals: "What your work is for; used to prioritise.",
  books: "What you're reading; the check-in updates pages read.",
};

function FieldRow({ field, rule, schema, current, onProperty, onValues }: {
  field: string;
  rule: FieldRule;
  schema: DataSourceSchema;
  current?: PropertyMapping;
  onProperty: (name: string) => void;
  onValues: (values: Record<string, string>) => void;
}) {
  const choices = schema.properties.filter((p) => rule.types.includes(p.type));
  if (!choices.length && !rule.required) return null;
  const prop = choices.find((p) => p.name === current?.name);
  const meanings = [...rule.needs, ...rule.allows];
  return (
    <div className="flex flex-col gap-2 border-t pt-3">
      <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
        <Label htmlFor={`field-${field}`} className="sm:w-40">
          {FIELD_LABELS[field] ?? humanize(field)}{rule.required ? " *" : ""}
        </Label>
        <select id={`field-${field}`} className={nativeSelect} value={current?.name ?? ""} onChange={(e) => onProperty(e.target.value)}>
          <option value="">{rule.required ? "Choose a property…" : "—"}</option>
          {choices.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
        </select>
      </div>
      {prop && field === "priority" && (
        <PriorityOrder values={current?.values ?? {}} onChange={onValues} />
      )}
      {prop && field !== "priority" && meanings.length > 0 && (
        <div className="grid grid-cols-1 gap-2 sm:ml-43 sm:grid-cols-2">
          {meanings.map((meaning) => (
            <label key={meaning} className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">{MEANINGS[meaning] ?? humanize(meaning)}{rule.needs.includes(meaning) ? " *" : ""}</span>
              <select aria-label={`Option meaning ${meaning}`} className={nativeSelect} value={current?.values?.[meaning] ?? ""}
                onChange={(e) => {
                  const values = { ...(current?.values ?? {}) };
                  if (e.target.value) values[meaning] = e.target.value;
                  else delete values[meaning];
                  onValues(values);
                }}>
                <option value="">—</option>
                {prop.options.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

function PriorityOrder({ values, onChange }: { values: Record<string, string>; onChange: (v: Record<string, string>) => void }) {
  const order = Object.keys(values).sort().map((k) => values[k]);
  function move(index: number, by: number) {
    const next = [...order];
    [next[index], next[index + by]] = [next[index + by], next[index]];
    onChange(Object.fromEntries(next.map((name, i) => [`p${i}`, name])));
  }
  return (
    <div className="flex flex-col gap-1 sm:ml-43">
      <span className="text-xs text-muted-foreground">Highest first</span>
      {order.map((name, i) => (
        <div key={name} className="flex items-center gap-1">
          <Badge variant="outline">{name}</Badge>
          <Button variant="ghost" size="icon-xs" aria-label={`Move ${name} up`} disabled={i === 0} onClick={() => move(i, -1)}><ArrowUpIcon /></Button>
          <Button variant="ghost" size="icon-xs" aria-label={`Move ${name} down`} disabled={i === order.length - 1} onClick={() => move(i, 1)}><ArrowDownIcon /></Button>
        </div>
      ))}
    </div>
  );
}

function HabitFields({ mapping, schema, onChange }: {
  mapping: ModuleMapping;
  schema: DataSourceSchema;
  onChange: (m: ModuleMapping) => void;
}) {
  const used = new Set(Object.entries(mapping.properties).filter(([k]) => ["title", "day", "notes"].includes(k)).map(([, p]) => p.name));
  const trackable = schema.properties.filter((p) => (p.type === "checkbox" || p.type === "number") && !used.has(p.name));
  const keyOf = (p: SchemaProperty) => Object.entries(mapping.properties).find(([, m]) => m.name === p.name)?.[0];

  function toggle(p: SchemaProperty, on: boolean) {
    const properties = { ...mapping.properties };
    const key = keyOf(p);
    if (on && !key) {
      properties[fieldKey(p.name, new Set(Object.keys(properties)))] = { name: p.name, type: p.type };
    } else if (!on && key) {
      delete properties[key];
    }
    onChange({ ...mapping, properties });
  }

  function update(key: string, change: Partial<PropertyMapping>) {
    onChange({ ...mapping, properties: { ...mapping.properties, [key]: { ...mapping.properties[key], ...change } } });
  }

  return (
    <div className="flex flex-col gap-3 border-t pt-3">
      {!mapping.properties.day && (
        <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
          <Label htmlFor="title-format" className="sm:w-40">Date in the name</Label>
          <select id="title-format" className={nativeSelect} value={mapping.date_format ?? "%Y-%m-%d"}
            onChange={(e) => onChange({ ...mapping, date_format: e.target.value })}>
            {TITLE_FORMATS.map((f) => <option key={f.format} value={f.format}>{f.example}</option>)}
          </select>
        </div>
      )}
      <p className="text-muted-foreground">Habits and metrics to track:</p>
      {trackable.length === 0 && <p className="text-muted-foreground">This database has no checkbox or number properties.</p>}
      {trackable.map((p) => {
        const key = keyOf(p);
        const current = key ? mapping.properties[key] : undefined;
        return (
          <div key={p.name} className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
            <label className="flex items-center gap-2 sm:w-40">
              <input type="checkbox" checked={Boolean(key)} onChange={(e) => toggle(p, e.target.checked)} />
              {p.name}
            </label>
            {key && p.type === "checkbox" && (
              <Input className="sm:max-w-sm" placeholder="What counts (optional), e.g. 20 minutes or more"
                value={current?.description ?? ""} onChange={(e) => update(key, { description: e.target.value || null })} />
            )}
            {key && p.type === "number" && (
              <Input className="w-40" placeholder="Unit, e.g. hours"
                value={current?.unit ?? ""} onChange={(e) => update(key, { unit: e.target.value || null })} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function SettingsCard({ settings, onChange }: { settings: WorkspaceSettingsView; onChange: (s: WorkspaceSettingsView) => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Your rules</CardTitle>
        <CardDescription>How LifeOS plans your days. You can change these later.</CardDescription>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="timezone">Timezone</Label>
          <select id="timezone" className={nativeSelect} value={settings.timezone}
            onChange={(e) => onChange({ ...settings, timezone: e.target.value })}>
            {zones(settings.timezone).map((z) => <option key={z} value={z}>{z}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="majors">Major tasks a day</Label>
          <select id="majors" className={nativeSelect} value={settings.max_major_per_day}
            onChange={(e) => onChange({ ...settings, max_major_per_day: Number(e.target.value) })}>
            {[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="review-day">Weekly review day</Label>
          <select id="review-day" className={nativeSelect} value={settings.review_weekday}
            onChange={(e) => onChange({ ...settings, review_weekday: Number(e.target.value) })}>
            {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="review-time">Weekly review time</Label>
          <Input id="review-time" type="time" className="w-32" value={settings.review_time}
            onChange={(e) => onChange({ ...settings, review_time: e.target.value })} />
        </div>
      </CardContent>
    </Card>
  );
}
