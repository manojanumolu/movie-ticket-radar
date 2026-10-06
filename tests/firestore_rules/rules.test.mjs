// firestore.rules, exercised in the Firebase emulator.
//
// Run with `npm test` from this directory (see README.md). Every case states
// what a person — or someone calling Firebase's API directly with their own
// ID token — can and cannot do.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import {
  initializeTestEnvironment,
  assertFails,
  assertSucceeds,
} from "@firebase/rules-unit-testing";
import { doc, getDoc, setDoc, updateDoc, deleteDoc } from "firebase/firestore";

const here = dirname(fileURLToPath(import.meta.url));
const rules = readFileSync(join(here, "..", "..", "firestore.rules"), "utf8");

const env = await initializeTestEnvironment({
  projectId: "demo-ticketradar",
  firestore: { rules, host: "127.0.0.1", port: 8080 },
});

const DAY = 86400e3;
const iso = (ms) => new Date(Date.now() + ms).toISOString();

const ALICE = { uid: "uid-alice", email: "alice@example.com" };
const BOB = { uid: "uid-bob", email: "bob@example.com" };

function db(user, { verified = true, admin = false } = {}) {
  return env
    .authenticatedContext(user.uid, { email: user.email, email_verified: verified, ...(admin ? { admin: true } : {}) })
    .firestore();
}

function monitor(owner, overrides = {}) {
  return {
    id: overrides.id ?? "m1",
    movie: { platform: "bookmyshow", event_code: "ET1", title: "Film", region_code: "HYD",
             region_slug: "hyderabad", city: "Hyderabad", language: "", poster_url: "",
             source_url: "", variants: [] },
    targets: [{ venue_code: "ALLU", venue_name: "ALLU Cinemas", area: "Kokapet", fmt: "Any format" }],
    interval_minutes: 10,
    monitor_until: iso(2 * DAY),
    start_immediately: true,
    notify_email: owner.email,
    date_codes: [],
    status: "ACTIVE",
    created_at: iso(0),
    stopped_at: null,
    stopped_reason: "",
    first_check_requested_at: null,
    problem: null,
    owner_uid: owner.uid,
    categories: [],
    show_time: "",
    ...overrides,
  };
}

async function seed(path, data) {
  await env.withSecurityRulesDisabled(async (ctx) => {
    await setDoc(doc(ctx.firestore(), path), data);
  });
}

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

// ── Authentication ──────────────────────────────────────────────────────
test("an unauthenticated visitor can read and write nothing", async () => {
  await seed("monitors/m1", monitor(ALICE));
  const anon = env.unauthenticatedContext().firestore();
  await assertFails(getDoc(doc(anon, "monitors/m1")));
  await assertFails(setDoc(doc(anon, "monitors/m2"), monitor(ALICE, { id: "m2" })));
  await assertFails(setDoc(doc(anon, "users/uid-alice"), { owner_uid: ALICE.uid }));
  await assertFails(getDoc(doc(anon, "catalogue/x")));
});

test("an unverified account cannot write monitors, settings or history", async () => {
  const me = db(ALICE, { verified: false });
  await assertFails(setDoc(doc(me, "monitors/m1"), monitor(ALICE)));
  await assertFails(setDoc(doc(me, "users/uid-alice"), { owner_uid: ALICE.uid }));
  await assertFails(setDoc(doc(me, "history/h1"), { owner_uid: ALICE.uid, kind: "CREATED" }));
});

// ── Ownership (unchanged) ───────────────────────────────────────────────
test("a verified owner creates, reads, stops and deletes their own monitor", async () => {
  const me = db(ALICE);
  await assertSucceeds(setDoc(doc(me, "monitors/m1"), monitor(ALICE)));
  await assertSucceeds(getDoc(doc(me, "monitors/m1")));
  await assertSucceeds(setDoc(doc(me, "monitors/m1"), monitor(ALICE, { status: "STOPPED" })));
  await assertSucceeds(deleteDoc(doc(me, "monitors/m1")));
});

test("nobody can create a monitor under another UID", async () => {
  await assertFails(setDoc(doc(db(ALICE), "monitors/m1"), monitor(ALICE, { owner_uid: BOB.uid })));
});

test("one account cannot read, change or delete another's monitor", async () => {
  await seed("monitors/m1", monitor(BOB));
  const me = db(ALICE);
  await assertFails(getDoc(doc(me, "monitors/m1")));
  await assertFails(setDoc(doc(me, "monitors/m1"), monitor(ALICE)));
  await assertFails(updateDoc(doc(me, "monitors/m1"), { status: "STOPPED" }));
  await assertFails(deleteDoc(doc(me, "monitors/m1")));
});

test("users/{uid} is the owner's alone", async () => {
  await assertSucceeds(setDoc(doc(db(ALICE), "users/uid-alice"), { owner_uid: ALICE.uid, avatar_key: "x" }));
  await assertFails(setDoc(doc(db(ALICE), "users/uid-bob"), { owner_uid: ALICE.uid }));
  await assertFails(getDoc(doc(db(ALICE), "users/uid-bob")));
  await assertFails(setDoc(doc(db(ALICE), "users/uid-alice"), { owner_uid: BOB.uid }));
});

// ── Recipients ──────────────────────────────────────────────────────────
test("a running monitor must email the account's own verified address", async () => {
  const me = db(ALICE);
  await assertFails(setDoc(doc(me, "monitors/m1"), monitor(ALICE, { notify_email: "victim@example.org" })));
  await assertFails(setDoc(doc(me, "monitors/m1"),
    monitor(ALICE, { notify_email: "alice@example.com, victim@example.org" })));
  await assertFails(setDoc(doc(me, "monitors/m1"), monitor(ALICE, { notify_email: "Alice <alice@example.com>" })));
  await assertSucceeds(setDoc(doc(me, "monitors/m1"), monitor(ALICE, { notify_email: "ALICE@example.com" })));
});

test("an older monitor addressed elsewhere can still be stopped, but not restarted as it is", async () => {
  await seed("monitors/m1", monitor(ALICE, { notify_email: "old@example.org" }));
  const me = db(ALICE);
  await assertSucceeds(setDoc(doc(me, "monitors/m1"),
    monitor(ALICE, { notify_email: "old@example.org", status: "STOPPED" })));
  await assertFails(setDoc(doc(me, "monitors/m1"), monitor(ALICE, { notify_email: "old@example.org" })));
  await assertSucceeds(setDoc(doc(me, "monitors/m1"), monitor(ALICE)));
});

// ── Intervals and admin-only features ───────────────────────────────────
test("intervals: 10/15/30 for everyone, 5 for the admin claim only, nothing else", async () => {
  for (const minutes of [10, 15, 30]) {
    await assertSucceeds(setDoc(doc(db(ALICE), `monitors/i${minutes}`),
      monitor(ALICE, { id: `i${minutes}`, interval_minutes: minutes })));
  }
  await assertFails(setDoc(doc(db(ALICE), "monitors/i5"), monitor(ALICE, { id: "i5", interval_minutes: 5 })));
  await assertSucceeds(setDoc(doc(db(ALICE, { admin: true }), "monitors/i5"),
    monitor(ALICE, { id: "i5", interval_minutes: 5 })));
  for (const minutes of [0, 1, 7, 60]) {
    await assertFails(setDoc(doc(db(ALICE, { admin: true }), `monitors/x${minutes}`),
      monitor(ALICE, { id: `x${minutes}`, interval_minutes: minutes })));
  }
  await assertFails(setDoc(doc(db(ALICE), "monitors/s"), monitor(ALICE, { id: "s", interval_minutes: "10" })));
});

test("a category watch is the admin claim's alone", async () => {
  await assertFails(setDoc(doc(db(ALICE), "monitors/c1"), monitor(ALICE, { id: "c1", categories: ["GOLD"] })));
  await assertFails(setDoc(doc(db(ALICE), "monitors/c2"), monitor(ALICE, { id: "c2", show_time: "07:15 PM" })));
  await assertSucceeds(setDoc(doc(db(ALICE, { admin: true }), "monitors/c3"),
    monitor(ALICE, { id: "c3", categories: ["GOLD"], show_time: "07:15 PM" })));
});

// ── End date ────────────────────────────────────────────────────────────
test("a running monitor ends within the 30-day limit", async () => {
  await assertSucceeds(setDoc(doc(db(ALICE), "monitors/e1"), monitor(ALICE, { id: "e1", monitor_until: iso(29 * DAY) })));
  await assertFails(setDoc(doc(db(ALICE), "monitors/e2"), monitor(ALICE, { id: "e2", monitor_until: iso(60 * DAY) })));
  await assertFails(setDoc(doc(db(ALICE), "monitors/e3"), monitor(ALICE, { id: "e3", monitor_until: "2099-01-01T00:00:00+05:30" })));
  await assertFails(setDoc(doc(db(ALICE), "monitors/e4"), monitor(ALICE, { id: "e4", monitor_until: "soon" })));
  await assertFails(setDoc(doc(db(ALICE, { admin: true }), "monitors/e5"),
    monitor(ALICE, { id: "e5", monitor_until: iso(90 * DAY) })));
});

// ── Shape ───────────────────────────────────────────────────────────────
test("only the fields the application writes, with sane sizes", async () => {
  const me = db(ALICE);
  await assertFails(setDoc(doc(me, "monitors/f1"), monitor(ALICE, { id: "f1", cc: "victim@example.org" })));
  await assertFails(setDoc(doc(me, "monitors/f2"), monitor(ALICE, { id: "f2", targets: [] })));
  const many = Array.from({ length: 201 }, (_, i) => ({ venue_code: `V${i}`, venue_name: "V", area: "", fmt: "Any format" }));
  await assertFails(setDoc(doc(me, "monitors/f3"), monitor(ALICE, { id: "f3", targets: many })));
  await assertFails(setDoc(doc(me, "monitors/f4"), monitor(ALICE, { id: "not-f4" })));
  await assertFails(setDoc(doc(me, "monitors/f5"), monitor(ALICE, { id: "f5", status: "PAUSED" })));
  const { notify_email, ...noRecipient } = monitor(ALICE, { id: "f6" });
  await assertFails(setDoc(doc(me, "monitors/f6"), noRecipient));
});

// ── Worker-owned records (unchanged) ────────────────────────────────────
test("monitor_state is the worker's: the owner may read and delete it, never write it", async () => {
  await seed("monitor_state/m1", { owner_uid: ALICE.uid, check_count: 1 });
  const me = db(ALICE);
  await assertSucceeds(getDoc(doc(me, "monitor_state/m1")));
  await assertFails(setDoc(doc(me, "monitor_state/m1"), { owner_uid: ALICE.uid, check_count: 99 }));
  await assertFails(setDoc(doc(me, "monitor_state/m2"), { owner_uid: ALICE.uid }));
  await assertFails(getDoc(doc(db(BOB), "monitor_state/m1")));
  await assertSucceeds(deleteDoc(doc(me, "monitor_state/m1")));
});

test("history is append-only and the owner's", async () => {
  const me = db(ALICE);
  await assertSucceeds(setDoc(doc(me, "history/h1"), { owner_uid: ALICE.uid, kind: "CREATED" }));
  await assertFails(setDoc(doc(me, "history/h1"), { owner_uid: ALICE.uid, kind: "EDITED" }));
  await assertFails(setDoc(doc(me, "history/h2"), { owner_uid: BOB.uid, kind: "CREATED" }));
  await assertFails(getDoc(doc(db(BOB), "history/h1")));
  await assertSucceeds(deleteDoc(doc(me, "history/h1")));
});

let failed = 0;
for (const [name, fn] of cases) {
  await env.clearFirestore();
  try {
    await fn();
    console.log(`ok   - ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`FAIL - ${name}\n       ${String(err && err.message || err).split("\n")[0]}`);
  }
}
await env.cleanup();
console.log(`\n${cases.length - failed} passed, ${failed} failed`);
assert.equal(failed, 0);
