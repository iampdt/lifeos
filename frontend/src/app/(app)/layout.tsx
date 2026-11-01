import { AppShell } from "@/components/app-shell";
import { SessionProvider } from "@/components/session";

export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <SessionProvider>
      <AppShell>{children}</AppShell>
    </SessionProvider>
  );
}
