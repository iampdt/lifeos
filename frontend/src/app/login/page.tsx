"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api, setCsrfToken } from "@/lib/api";

type Me = { email: string; csrf_token: string };
type Meta = { demo: boolean; google: boolean; password: boolean };

export default function LoginPage() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api<Meta>("/meta") });
  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      {meta.isPending ? (
        <Skeleton className="h-64 w-full max-w-sm" />
      ) : meta.data?.demo ? (
        <DemoEntry />
      ) : meta.data?.google ? (
        <Suspense fallback={<Skeleton className="h-64 w-full max-w-sm" />}>
          <GoogleSignIn />
        </Suspense>
      ) : (
        <PasswordSignIn />
      )}
    </main>
  );
}

// The API sends failed Google sign-ins back here with ?error=<code>.
const GOOGLE_ERRORS: Record<string, string> = {
  cancelled: "Sign-in was cancelled.",
  expired: "That sign-in attempt expired. Try again.",
  invalid_token: "Google's response couldn't be verified. Try again.",
  exchange_failed: "Google's response couldn't be verified. Try again.",
  email_unverified: "Your Google account's email address isn't verified.",
  signup_closed: "LifeOS isn't open to new accounts yet.",
  account_conflict: "This email address is already linked to a different Google account.",
};

function GoogleSignIn() {
  const code = useSearchParams().get("error");
  const error = code ? (GOOGLE_ERRORS[code] ?? "Sign-in failed. Try again.") : null;
  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle className="text-xl">LifeOS</CardTitle>
        <CardDescription>Sign in to your chief of staff.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {/* A full-page navigation: the API redirects to Google's consent page. */}
        <a href="/api/v1/auth/google/start" className={buttonVariants({ variant: "outline", size: "lg" })}>
          <GoogleMark />
          Continue with Google
        </a>
      </CardContent>
    </Card>
  );
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
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
