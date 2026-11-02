"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api, setCsrfToken } from "@/lib/api";

type Me = { email: string; csrf_token: string };

export default function LoginPage() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api<{ demo: boolean }>("/meta") });
  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      {meta.isPending ? (
        <Skeleton className="h-64 w-full max-w-sm" />
      ) : meta.data?.demo ? (
        <DemoEntry />
      ) : (
        <PasswordSignIn />
      )}
    </main>
  );
}

function useSignedIn() {
  const router = useRouter();
  const client = useQueryClient();
  return (me: Me) => {
    setCsrfToken(me.csrf_token);
    client.setQueryData(["me"], { via: "session", ...me });
    router.replace("/");
  };
}

function PasswordSignIn() {
  const signedIn = useSignedIn();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      signedIn(await api<Me>("/auth/login", { method: "POST", body: { email, password } }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach the backend.");
    } finally {
      setPending(false);
    }
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle className="text-xl">LifeOS</CardTitle>
        <CardDescription>Sign in to your chief of staff.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" autoComplete="username" required
              value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="password">Password</Label>
            <Input id="password" type="password" autoComplete="current-password" required
              value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={pending} className="mt-1">
            {pending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

const TRY = [
  "Run the evening check-in: tick what you finished or just describe your day.",
  "Approve or reject the proposed changes, and watch them apply.",
  "Ask the chat \"what's overdue?\" or \"move the resume task to Friday\".",
  "Run the weekly review and decide what happens to overdue tasks.",
];

function DemoEntry() {
  const signedIn = useSignedIn();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function enter() {
    setPending(true);
    setError(null);
    try {
      signedIn(await api<Me>("/auth/demo", { method: "POST" }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach the demo. Try again in a minute.");
      setPending(false);
    }
  }

  return (
    <Card className="w-full max-w-lg">
      <CardHeader>
        <CardTitle className="text-2xl">LifeOS</CardTitle>
        <CardDescription className="text-base">
          A chief of staff for a productivity system in Notion. It runs your daily and weekly
          routines and proposes changes, and nothing is written until you approve it.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <ul className="flex list-disc flex-col gap-1.5 pl-5 text-sm text-muted-foreground">
          {TRY.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <p className="text-xs text-muted-foreground">
          This demo uses a sample workspace instead of a real Notion account. It&apos;s shared
          with other visitors and resets when idle. The first visit can take up to a minute while
          the free server wakes up.
        </p>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <Button size="lg" onClick={enter} disabled={pending}>
          {pending ? "Opening the demo…" : "Try the demo"}
        </Button>
      </CardContent>
    </Card>
  );
}
