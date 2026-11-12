// node --test ops/keep-awake/worker.test.js
import assert from "node:assert/strict";
import { test } from "node:test";

import worker, { isQuiet } from "./worker.js";

const env = { APP_URL: "https://app.example", TIMEZONE: "Asia/Kolkata", QUIET_HOURS: "04-07" };

test("quiet from 04:00 to 06:59 in the configured timezone", () => {
  // Asia/Kolkata is UTC+05:30.
  assert.equal(isQuiet(new Date("2026-11-12T22:20:00Z"), env), false); // 03:50
  assert.equal(isQuiet(new Date("2026-11-12T22:30:00Z"), env), true); // 04:00
  assert.equal(isQuiet(new Date("2026-11-13T01:20:00Z"), env), true); // 06:50
  assert.equal(isQuiet(new Date("2026-11-13T01:30:00Z"), env), false); // 07:00
});

test("a window can cross midnight", () => {
  const overnight = { ...env, QUIET_HOURS: "23-05" };
  assert.equal(isQuiet(new Date("2026-11-12T18:00:00Z"), overnight), true); // 23:30
  assert.equal(isQuiet(new Date("2026-11-12T23:00:00Z"), overnight), true); // 04:30
  assert.equal(isQuiet(new Date("2026-11-12T12:00:00Z"), overnight), false); // 17:30
});

test("pings the API through the web app, and fails loudly when it isn't healthy", async (t) => {
  const calls = [];
  let status = 200;
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(url);
    return new Response("{}", { status });
  });
  const at = (iso) => ({ scheduledTime: Date.parse(iso) });
  await worker.scheduled(at("2026-11-12T10:00:00Z"), env);
  await worker.scheduled(at("2026-11-12T23:00:00Z"), env); // quiet: no request
  assert.deepEqual(calls, ["https://app.example/api/v1/health"]);
  status = 502;
  await assert.rejects(worker.scheduled(at("2026-11-12T10:10:00Z"), env), /answered 502/);
});
