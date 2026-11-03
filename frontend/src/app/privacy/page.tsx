import { Contact, LegalPage } from "@/components/legal-page";

export const metadata = { title: "Privacy · LifeOS" };

export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy policy">
      <p>
        LifeOS helps you run daily and weekly routines on your own Notion workspace. It reads the pages you share
        with it, proposes changes, and writes to Notion only what you approve. This page says what it keeps, where,
        and how to remove it.
      </p>

      <section>
        <h2>What LifeOS stores</h2>
        <ul>
          <li><strong>Your account:</strong> your email address and name from Google, and Google&apos;s ID for your
            account. A session cookie keeps you signed in; LifeOS stores only a hash of it.</li>
          <li><strong>Your Notion connection:</strong> the access tokens Notion issues, encrypted, and your
            workspace&apos;s name and ID. Your Notion content itself stays in Notion.</li>
          <li><strong>Your setup:</strong> which databases and properties LifeOS uses, your timezone and routine
            times.</li>
          <li><strong>Runs and proposals:</strong> each routine run (status, timing, and the number of AI tokens it
            used) and each proposed change, including the titles of the tasks or pages it concerns, their values
            before the change, and your decision. While a run waits for you, its state (including what you wrote
            in a check-in) is kept so it can continue.</li>
          <li><strong>An activity log</strong> of IDs, kinds and outcomes (for example &quot;proposal approved&quot;),
            without page contents.</li>
          <li><strong>AI settings:</strong> whether and when you agreed to the AI terms below, your own Gemini key if
            you add one (encrypted, shown only by its last four characters), and how many tokens you used each
            day.</li>
        </ul>
        <p>Chat messages and answers aren&apos;t stored, only how many there were.</p>
      </section>

      <section>
        <h2>AI</h2>
        <p>
          LifeOS uses Google&apos;s Gemini API on its free tier to read check-ins, plan days and draft reviews. On the
          free tier, Google may use what LifeOS sends (your check-in messages, task titles and other workspace
          content) to improve its products, and human reviewers may read it. LifeOS sends nothing to Gemini for your
          account until you agree to this in Settings, and you can turn it off at any time; without AI, LifeOS runs
          your routines with rules. If you add your own Gemini key, your calls follow the terms of your own Google
          account instead.
        </p>
      </section>

      <section>
        <h2>Who else handles your data</h2>
        <ul>
          <li><strong>Google</strong>, for signing in and, if you turn AI on, Gemini.</li>
          <li><strong>Notion</strong>, where your workspace lives.</li>
          <li><strong>Render</strong> (the server), <strong>Neon</strong> (the database) and <strong>Vercel</strong>
            (the web app), which host LifeOS. GitHub Actions starts the scheduled routines; it sees internal
            account IDs, never your content.</li>
        </ul>
        <p>LifeOS has no ads, sells nothing, and uses no analytics or tracking cookies.</p>
      </section>

      <section>
        <h2>Keeping and deleting it</h2>
        <p>
          LifeOS keeps your data while you have an account. Settings → Account → <strong>Delete account</strong>
          removes all of it at once: your Notion connection (and LifeOS&apos;s access in Notion), runs, proposals,
          the activity log, AI settings and sessions, leaving only a note that an account (by an internal ID) was
          deleted. Nothing in your Notion workspace is changed. Disconnecting
          Notion alone deletes the stored tokens. The servers&apos; own logs hold IDs and counts, not your content,
          and expire on the hosts&apos; schedules.
        </p>
      </section>

      <section>
        <h2>Security</h2>
        <p>
          Everything travels over HTTPS. Notion tokens and Gemini keys are encrypted at rest, and session tokens are
          stored only as hashes. LifeOS writes to Notion only after you approve each change, and checks each write
          by reading it back.
        </p>
      </section>

      <section>
        <h2>Changes and questions</h2>
        <p>
          If this policy changes, the date above changes with it, and a change to the AI terms asks for your
          agreement again. For questions or requests, contact <Contact />.
        </p>
      </section>
    </LegalPage>
  );
}
