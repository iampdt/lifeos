// Render's free tier stops an instance after 15 minutes without a request, and starting it
// again takes about a minute. A Cron Trigger runs this every 10 minutes, outside QUIET_HOURS,
// and requests /health, so the API stays awake and its in-process tick runs on time.

export default {
  async scheduled(controller, env) {
    if (isQuiet(new Date(controller.scheduledTime), env)) return;
    // A sleeping instance answers once it has started.
    const response = await fetch(`${env.APP_URL}/api/v1/health`, {
      signal: AbortSignal.timeout(120_000),
    });
    // Throwing marks the run as failed in the Worker's logs.
    if (!response.ok) throw new Error(`/health answered ${response.status}`);
  },
};

/** QUIET_HOURS is "start-end" in whole hours of TIMEZONE: "04-07" is 04:00 to 06:59. */
export function isQuiet(when, env) {
  const [start, end] = env.QUIET_HOURS.split("-").map(Number);
  const hour = Number(
    new Intl.DateTimeFormat("en-GB", { hour: "numeric", hourCycle: "h23", timeZone: env.TIMEZONE })
      .format(when),
  );
  return start <= end ? hour >= start && hour < end : hour >= start || hour < end;
}
