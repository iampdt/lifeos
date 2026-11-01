"""Check a deployment end to end, read-only.

    python -m scripts.smoke_test --api https://<api-host> [--app https://<app-host>] [--chat]

Uses LIFEOS_API_TOKEN from the environment for the authenticated checks. Waits for a sleeping
free-tier instance to start. Nothing is written to Notion; `--chat` asks one question through
the web app's proxy to confirm that streamed answers arrive incrementally.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

PASS, FAIL = "ok  ", "FAIL"


class Smoke:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        print(f"{PASS if ok else FAIL} {label}{f' — {detail}' if detail else ''}")
        self.failures += 0 if ok else 1


def wait_for(url: str, seconds: int = 120) -> float:
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        try:
            if httpx.get(url, timeout=30).status_code == 200:
                return time.monotonic() - started
        except httpx.HTTPError:
            pass
        time.sleep(5)
    raise SystemExit(f"{url} did not answer within {seconds} s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--api", required=True, help="API base URL, e.g. https://lifeos-api.onrender.com"
    )
    parser.add_argument("--app", help="web app base URL, e.g. https://lifeos.vercel.app")
    parser.add_argument("--chat", action="store_true", help="also stream one chat answer via --app")
    args = parser.parse_args()
    token = os.environ.get("LIFEOS_API_TOKEN")
    if not token:
        raise SystemExit("set LIFEOS_API_TOKEN")
    auth = {"Authorization": f"Bearer {token}"}
    api = args.api.rstrip("/") + "/api/v1"
    smoke = Smoke()

    waited = wait_for(f"{api}/health")
    smoke.check("API reachable", True, f"answered after {waited:.0f} s")
    with httpx.Client(timeout=60) as client:
        smoke.check("auth required", client.get(f"{api}/tasks").status_code == 401)
        ready = client.get(f"{api}/health/ready", headers=auth)
        smoke.check("database ready", ready.status_code == 200, ready.text[:120])
        settings = client.get(f"{api}/settings", headers=auth).json()
        smoke.check("LLM configured", settings["llm"]["configured"])
        print(f"     profile={settings['notion_profile']} writes={settings['writes_enabled']} "
              f"today={settings['today']}")  # fmt: skip
        docs = client.get(args.api.rstrip("/") + "/openapi.json").status_code
        smoke.check("API docs hidden (production)", docs == 404, f"status {docs}")
        status = client.get(f"{api}/notion/status", headers=auth).json()
        for source in status["data_sources"]:
            if source["configured"]:
                smoke.check(f"Notion {source['name']}", bool(source.get("ok")),
                            source.get("error") or "schema matches")  # fmt: skip
        today = client.get(f"{api}/tasks?view=today", headers=auth)
        smoke.check("tasks readable", today.status_code == 200, f"{len(today.json())} due today")

        if args.app:
            app = args.app.rstrip("/")
            smoke.check("web app serves /login", client.get(f"{app}/login").status_code == 200)
            proxied = client.get(f"{app}/api/v1/health")
            smoke.check("web app proxies /api", proxied.status_code == 200)
            if args.chat:
                started, stamps = time.monotonic(), []
                body = {"message": "How many tasks are due today? Answer in one sentence."}
                with client.stream("POST", f"{app}/api/v1/chat", json=body, headers=auth,
                                   timeout=120) as response:  # fmt: skip
                    for line in response.iter_lines():
                        if line.startswith("data: "):
                            kind = json.loads(line[6:])["type"]
                            stamps.append((kind, round(time.monotonic() - started, 1)))
                first, last = stamps[0][1], stamps[-1][1]
                smoke.check("chat streams through the proxy", last - first > 0.5,
                            " ".join(f"{k}@{t}s" for k, t in stamps))  # fmt: skip

    print("\nall checks passed" if smoke.failures == 0 else f"\n{smoke.failures} check(s) failed")
    return 0 if smoke.failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
