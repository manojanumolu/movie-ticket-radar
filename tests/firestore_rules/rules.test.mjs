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

// ── Phase 5: resource bounds and forged claims (audit SEC-01, SEC-03) ─────
const sibling = (i) => ({ code: `ET9${String(i).padStart(7, "0")}`, label: "4DX 3D" });
const film = (variants) => ({ ...monitor(ALICE).movie, variants });

test("a monitor carries at most 12 sibling events", async () => {
  const me = db(ALICE);
  await assertSucceeds(setDoc(doc(me, "monitors/v1"),
    monitor(ALICE, { id: "v1", movie: film(Array.from({ length: 12 }, (_, i) => sibling(i))) })));
  await assertFails(setDoc(doc(me, "monitors/v2"),
    monitor(ALICE, { id: "v2", movie: film(Array.from({ length: 13 }, (_, i) => sibling(i))) })));
  await assertFails(setDoc(doc(me, "monitors/v3"),
    monitor(ALICE, { id: "v3", movie: film(Array.from({ length: 5000 }, (_, i) => sibling(i))) })));
  // Not even a stopped one: the bound is on the document's shape.
  await assertFails(setDoc(doc(me, "monitors/v4"),
    monitor(ALICE, { id: "v4", status: "STOPPED", movie: film(Array.from({ length: 13 }, (_, i) => sibling(i))) })));
});

test("a running monitor costs at most 120 BookMyShow requests per check", async () => {
  const me = db(ALICE);
  const dates = (n) => Array.from({ length: n }, (_, i) => `202612${String(i % 28 + 1).padStart(2, "0")}`);
  // 40 dates x (1 + 2 siblings) = 120: allowed. 41 x 3 = 123: refused.
  await assertSucceeds(setDoc(doc(me, "monitors/r1"),
    monitor(ALICE, { id: "r1", date_codes: dates(40), movie: film([sibling(1), sibling(2)]) })));
  await assertFails(setDoc(doc(me, "monitors/r2"),
    monitor(ALICE, { id: "r2", date_codes: dates(41), movie: film([sibling(1), sibling(2)]) })));
  // 62 dates (the date cap) with 1 sibling = 124: refused while running…
  await assertFails(setDoc(doc(me, "monitors/r3"),
    monitor(ALICE, { id: "r3", date_codes: dates(62), movie: film([sibling(1)]) })));
  // …and a stopped one costs nothing, so it may still be saved.
  await assertSucceeds(setDoc(doc(me, "monitors/r4"),
    monitor(ALICE, { id: "r4", status: "STOPPED", date_codes: dates(62), movie: film([sibling(1)]) })));
});

test("the movie map holds only MovieRef's fields, at sane sizes", async () => {
  const me = db(ALICE);
  await assertFails(setDoc(doc(me, "monitors/g1"),
    monitor(ALICE, { id: "g1", movie: { ...monitor(ALICE).movie, payload: "x".repeat(10) } })));
  await assertFails(setDoc(doc(me, "monitors/g2"),
    monitor(ALICE, { id: "g2", movie: { ...monitor(ALICE).movie, title: "x".repeat(501) } })));
  await assertFails(setDoc(doc(me, "monitors/g3"),
    monitor(ALICE, { id: "g3", movie: { ...monitor(ALICE).movie, event_code: "ET1\r\nX-Injected: 1".padEnd(40, "x") } })));
  const { title, ...noTitle } = monitor(ALICE).movie;
  await assertFails(setDoc(doc(me, "monitors/g4"), monitor(ALICE, { id: "g4", movie: noTitle })));
});

test("a history line has only the fields the application writes", async () => {
  const me = db(ALICE);
  const line = { owner_uid: ALICE.uid, monitor_id: "m1", kind: "STOPPED", message: "Monitoring stopped.",
                 movie: "Film", language: "Telugu", poster_url: "", targets: ["ALLU · Any format"],
                 at: new Date().toISOString() };
  await assertSucceeds(setDoc(doc(me, "history/k1"), line));
  await assertFails(setDoc(doc(me, "history/k2"), { ...line, blob: "x" }));
  await assertFails(setDoc(doc(me, "history/k3"), { ...line, message: "x".repeat(501) }));
  await assertFails(setDoc(doc(me, "history/k4"), { ...line, targets: Array.from({ length: 201 }, () => "t") }));
  await assertFails(setDoc(doc(me, "history/k5"), { owner_uid: ALICE.uid }));         // no kind
});

test("an admin claim that is not exactly the boolean true is no admin claim", async () => {
  const forged = env.authenticatedContext(ALICE.uid,
    { email: ALICE.email, email_verified: true, admin: "true" }).firestore();
  await assertFails(setDoc(doc(forged, "monitors/a1"), monitor(ALICE, { id: "a1", interval_minutes: 5 })));
  await assertFails(setDoc(doc(forged, "monitors/a2"), monitor(ALICE, { id: "a2", categories: ["GOLD"] })));
  // …and no claim at all lets anyone read another account's records.
  await seed("monitors/b1", monitor(BOB, { id: "b1" }));
  await assertFails(getDoc(doc(db(ALICE, { admin: true }), "monitors/b1")));
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
