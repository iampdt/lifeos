import { Contact, LegalPage } from "@/components/legal-page";

export const metadata = { title: "Terms · LifeOS" };

export default function TermsPage() {
  return (
    <LegalPage title="Terms of use">
      <p>By signing in to LifeOS you agree to these terms. If you don&apos;t, please don&apos;t use it.</p>

      <section>
        <h2>The service</h2>
        <p>
          LifeOS is a free tool that runs routines on your own Notion workspace: it reads the pages you share with
          it and proposes changes you approve. It runs on free hosting and free AI tiers, so it may be slow, limited
          or unavailable at times, and it may change or stop. It is provided as is, without warranties of any
          kind.
        </p>
      </section>

      <section>
        <h2>Your part</h2>
        <ul>
          <li>Your Notion content stays yours. You decide which pages LifeOS can see, and every change it makes
            needs your approval first.</li>
          <li>AI suggestions can be wrong. Review proposals before you approve them.</li>
          <li>Use LifeOS only with your own account and workspace. Don&apos;t try to reach other people&apos;s data,
            get around limits, or overload the service.</li>
          <li>Your use of Notion and Google (including Gemini) also follows their own terms.</li>
        </ul>
      </section>

      <section>
        <h2>Ending</h2>
        <p>
          You can delete your account at any time in Settings; that removes everything LifeOS stored for you (see
          the <a href="/privacy" className="underline underline-offset-4">privacy policy</a>). Accounts that abuse
          the service may be suspended.
        </p>
      </section>

      <section>
        <h2>Liability</h2>
        <p>
          To the extent the law allows, whoever runs LifeOS isn&apos;t liable for indirect or consequential losses,
          or for changes you approved in your workspace.
        </p>
      </section>

      <section>
        <h2>Contact</h2>
        <p>Questions about these terms: <Contact />.</p>
      </section>
    </LegalPage>
  );
}
