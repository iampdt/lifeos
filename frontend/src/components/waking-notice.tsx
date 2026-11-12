/** Shown while requests are being retried because the API can't be reached yet. */
export function WakingNotice() {
  return (
    <p
      role="status"
      className="fixed inset-x-0 top-0 z-50 border-b bg-muted px-4 py-2 text-center text-sm text-muted-foreground"
    >
      The server is waking up. This can take a minute…
    </p>
  );
}
