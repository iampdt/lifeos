"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { api } from "@/lib/api";

export const UPDATED = "3 November 2026";

/** The privacy and terms pages: public, readable without signing in. */
export function LegalPage({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-6 px-4 py-10 text-sm leading-6">
      <div className="flex items-center justify-between gap-4">
        <Link href="/" className="font-semibold tracking-tight">LifeOS</Link>
        <nav className="flex gap-3 text-muted-foreground">
          <Link href="/privacy" className="hover:text-foreground">Privacy</Link>
          <Link href="/terms" className="hover:text-foreground">Terms</Link>
        </nav>
      </div>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="text-muted-foreground">Last updated {UPDATED}</p>
      </div>
      <div className="flex flex-col gap-5 [&_h2]:text-base [&_h2]:font-semibold [&_ul]:list-disc [&_ul]:pl-5 [&_li]:mt-1">
        {children}
      </div>
    </main>
  );
}

/** Whoever runs this deployment, as configured (LIFEOS_CONTACT_EMAIL). */
export function Contact() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api<{ contact: string | null }>("/meta") });
  const email = meta.data?.contact;
  if (!email) return <>the person who runs this LifeOS deployment</>;
  return <a href={`mailto:${email}`} className="underline underline-offset-4">{email}</a>;
}
