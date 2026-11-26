# Movie Ticket Radar — Production Readiness, Fixes, Tests and Activation Runbook

| | |
|---|---|
| Prepared | 26 November 2026 (night pass, IST) |
| Production baseline | `main` = `cb9c6a130b1b3f321435c817fabb133d3742f27b` |
| Work branch | `claude/charming-bardeen-9al13c` (not merged) |
| Code commits in this pass | `89936bc` worker hardening · `1df5499` staged relay cutover · `d18b9a9` report and production config · backup-isolation update (DR project + access verifier) |
| Supersedes | `2026-11-25-cloud-run-migration-runbook.md` (kept for history; **use this document**) |
| Production changes made | **None.** No GCP resource, IAM, Rule, backup, credential, workflow, monitor or email was touched. Nothing was merged. |

---

## 1. Verdict

| Scope | Verdict |
|---|---|
| **Repository** (code, tests, image, configuration, runbook) | **GO.** All repository-level issues found in this pass are fixed and tested. No unresolved critical or high issue. |
| **Production activation tomorrow** | **CONDITIONAL GO.** Follow §8. Every mandatory gate is mechanical and has a defined stop action. `af1711d4` is reactivated only at step A-11, after all earlier gates pass. |
| **Not provable from here** | Anything inside `ticketradar03` (no GCP access in this session), and BookMyShow from Cloud Run (gate A-6). |
| **BookMyShow permission** | **Not a technical blocker; a business decision you must make.** No written authorization exists. See §6. |

**Total time tomorrow: about 3½ hours hands-on, about 5 hours elapsed** (§9). This includes backups, the
build, the gates, one controlled email and the first-hour watch.

---

## 2. Issues — discovered, fixed, remaining

### 2.1 Fixed in code (commit `89936bc`, worker candidate only)

| # | Issue (verified in code) | Impact | Fix | Tests |
|---|---|---|---|---|
| F1 | `Execution.finish` marks a change as announced in `monitor_execution.detection` when it writes the outbox row. A row ending `failed` (SMTP refusal) or `cancelled` (older than 20 min, monitor edited/extended/stopped, target no longer bookable) was **never regenerated**. Pressing *Extend*/*Check now* on a monitor with a pending alert changes its version and cancels that alert. | **Silently missed alerts** | `release_detection_mark()`: on `failed`/`cancelled`, reset this target's notified fields in `detection` to what the user was actually told (the accepted state). Only when the mark is still this item's own (`notified_at == created_at`). `uncertain` is never released. | 5 unit + 1 Firestore-emulator test |
| F2 | Fenced checks printed no per-target result. The audit's "verify the four theatres in logs" step could not work. | Operability | `_log_fenced_check`: `[checker] af1711d4 — Avengers: Doomsday (5 target(s))` and one line per target (private Cloud Run logs, no address) | 2 |
| F3 | `/healthz` said `ready` regardless of mode; there was no evidence of which mode/authority/sender a revision runs | Misleading health signal | `/healthz` → `{"ready": true, "mode": "<mode>"}`; one start-up JSON line: `worker_start`, `authority`, `credential_mode`, `project_pinned`, `sender_configured`, `legacy_key_present` (never a value) | 1 + container |
| F4 | No in-image BookMyShow probe; the old runbook's `list_movies` command could make up to **9** requests (3 strategies × 3 profile retries) | Excess requests | `run_bms_probe.py`: exactly **one** GET (the quickbook listing the worker already reads), block-retries 1, no fall-through, refuses a second request; verdict line plus exit code | 8 + container |
| F5 | Relay `curl` failed if a scheduled tick held the worker (`503 busy` / lease) | Lost "Check now" | `--retry 2 --retry-delay 15 --retry-all-errors`; deduplicated by `request_id` | structural |

### 2.2 Fixed by staging (commit `1df5499`, activation needs your merge)

| # | Issue | Fix |
|---|---|---|
| F6 | After the legacy workflow is disabled, *Start now* on a new monitor shows a false red **PROBLEM** banner. *Check now*/*Retry* fail. Cause: the app dispatches `bookmyshow-monitor.yml`, which is then disabled. | `worker_candidate/cutover/bookmyshow-monitor.yml`: same name, same inputs (`force`, `dry_run`, `monitor_id`). It runs no engine, has no schedule/push/loop, holds no stored credential, and has `contents: read`. It forwards a targeted check to the private worker via the keyless relay. Untargeted and dry-run dispatches are no-ops. The app is unchanged. Before the relay is configured, the dispatch still succeeds, so there is no banner, and the scheduler checks within a minute. Includes the matching `test_release_isolation.py`. The cutover was **simulated** in a scratch worktree: the isolation and structure tests pass, and actionlint is clean. |

This is deliberately not applied to `.github/workflows/` on this branch. The repository's `tests/test_release_isolation.py`
pins the live app, legacy monitor and every workflow to the production baseline. Changing them is a production
release you approve (Stage 2, §8.4).

### 2.3 Fixed in the runbook (this document)

| # | Error in the 25 Nov runbook | Correction |
|---|---|---|
| R1 | Auth export (contains **password hashes**) uploaded in plaintext to a bucket with default project-wide access | Superseded by R11. Export GPG-encrypted (AES-256) in RAM (`/dev/shm`) before upload; plaintext never touches the persistent disk. |
| R2 | Export bucket placed in the Cloud Run region | Bucket location derived from the Firestore location (`nam5→US`, `eur3→EU`, else the same region) |
| R3 | Firestore service agent bucket permission missing | Explicit binding on the DR bucket only |
| R4 | Four-target check read `last_check_at` per target, but that field is monitor-level | Monitor-level `last_check_at` + per-target `availability`, plus the new log lines |
| R5 | Legacy exclusion relied only on *Disable workflow*. An accidental re-enable, or a *Re-run* of an old run, would still receive production secrets. | Also add **required reviewer** on the GitHub `production` environment. The legacy engine then cannot run unattended. |
| R6 | Sink test left its alert permanently suppressed (F1) | New flow: sink → `failed`, no mail → secrets mounted → forced check → exactly one `accepted` → no duplicate |
| R7 | Image build depended on local Docker/Cloud Build permissions | Everything runs in **Cloud Shell** (Docker, git, gcloud preinstalled) |
| R8 | Alerting was only suggested | 4 log-based metrics + 4 alert policies in `infra/production/` |
| R9 | No before/after proof that `af1711d4` is unchanged | Snapshot before; field-by-field diff after *Extend* (only `status`, `monitor_until`, `stopped_*` may differ) |
| R10 | Names were scattered and inconsistent | `infra/production/env.sh` is the single source; a test checks the runbook against it |
| R11 | **The first version of this report (owner review, 26 Nov) removed `projectViewer`/`projectEditor` from the bucket policy. Inherited access cannot be removed that way.** Effective bucket access is the union of the bucket, project, folder and organisation policies, plus groups and impersonation. Project-level `roles/storage.*` grants in `ticketradar03` (for example, to the Firebase Admin SDK account whose key is in GitHub) would still reach the backups. | Backups move to a **dedicated project `ticketradar03-dr`** where only the owner holds a role. The bucket policy is **replaced** with exactly two bindings: owner `objectAdmin`, and `ticketradar03`'s Firestore service agent `storage.admin` on that bucket only. Ancestors and their policies (including deny) are listed. **Policy Analyzer** (`--expand-groups --analyze-service-account-impersonation`) must return only approved identities before anything is uploaded, and again afterwards. A tested verifier, `tools/verify_backup_access.py`, turns all of this into pass/fail. No IAM in `ticketradar03` changes. |

### 2.4 Remaining — cannot be resolved in the repository

| # | Item | Severity | Owner action |
|---|---|---|---|
| U1 | **BookMyShow written permission** does not exist (§6) | Business/legal | Decide; request text in §6.3 |
| U2 | Nothing has been executed in `ticketradar03` | Process | Gates in §8 |
| U3 | BookMyShow may refuse Cloud Run egress | Unknown until A-6 | Gate A-6; stop if blocked |
| U4 | *Start now* banner remains until Stage 2 is merged | Cosmetic | §8.4, or tell users |
| U5 | Worker reads the catalogue from `raw.githubusercontent.com/…/main/data/catalogue.json`. The repo must stay public and catalogue sync must keep running. | Low | None (fail-safe: no checks, no mail) |
| U6 | `roles/datastore.user` is database-wide; IAM cannot scope it to collections. `firebaseauth.viewer` can read whole user records. | Accepted | None; worker logs neither |
| U7 | Long-lived `FIREBASE_SERVICE_ACCOUNT` key stays in GitHub (used by `rules-audit`, `grant-admin`) | Medium (pre-existing) | Retire after moving those to federation (later, separate approval) |
| U8 | A BookMyShow read can outlive Cloud Run's 55-s timeout (20-s timeout × retries). The lease then blocks ≤3 ticks. | Low | Self-heals; alert fires if persistent |
| U9 | Terraform `validate` could not run (provider registry blocked here); `fmt` passes. Terraform is staging-only and refuses `ticketradar03` by design. | None for production | — |
| U10 | Log filters and alert-policy JSON were not executed against real Cloud Logging/Monitoring | Low | Gate A-9 |
| U11 | Relay request receipts accumulate in `worker_runtime/` (tiny docs, no TTL) | Low | Add TTL later |
| U12 | Effective access to the DR bucket cannot be determined from here (no GCP access). If the account sits in a Google Workspace/Cloud **organisation**, its policies apply to the DR project too. | Gate | A-2b/A-2c decide it tomorrow. With an organisation, the analysis needs org-scope Cloud Asset access, or backups stop. |
| U13 | A project owner of `ticketradar03` can still create managed backups or PITR restores there (by design) | Accepted | Same population that can read the live database |

---

## 3. Independent verification (Phase 1)

| Area | Verified behaviour | Evidence |
|---|---|---|
| Worker architecture | Python `ThreadingHTTPServer`; `/healthz`, `/tick` (no body), `/check` (`monitor_id`, `force`, `request_id`); 1 KiB body cap; busy lock → 503; two slots (0 s, ~30 s) within a 45-s budget | `run_worker_service.py`, `monitor/runtime.py`; container test |
| Lease and epoch fencing | `worker_runtime/control` {authority, epoch (int ≥1), holder, lease_until}, 3-min lease. Every write calls `assert_lease` (authority, holder, epoch, expiry). Claim compares monitor and state documents exactly; finish verifies versions. | `execution.py`; unit + emulator "competing worker" test |
| Authority / legacy exclusion | Fenced mode refuses `legacy` credentials and malformed authority at start-up. The legacy runner ignores the fence, so exclusion is operational: disable workflow + `production` reviewer gate + staged relay-only file. | container tests B, C |
| Outbox | Durable rows; `pending → sending → accepted / retry(2ⁿ min, ≤3) / failed / uncertain / cancelled`. A stranded `sending` → `uncertain`, never auto-resent. Stable `Message-ID <item@ticket-radar.invalid>`. Recipient hash checked at send. F1 fixed. | `outbox.py`; 10 outbox tests |
| Owner lookup | `accounts:lookup` each tick via keyless token. An unusable identity → nothing checked, nothing sent. **Requires `roles/firebaseauth.viewer`.** | patched `policy.worker_resolver` |
| Rules | `worker_runtime`, `monitor_execution`, `monitor_outbox` fall under the `/{document=**}` deny for every Firebase client. Rules file unchanged. | emulator: "worker control, claims and outbox reject all Firebase client identities" |
| Keyless identity | `adc` requires `compute_engine.Credentials` and the detected project = `FIREBASE_EXPECTED_PROJECT` | container tests D, E |
| Scheduler OIDC | `--oidc-service-account-email` + audience = service URL; invoker on this service only | runbook A-5 |
| Artifact Registry / Run | Image runs as UID 65532, contains no `tools/`, `data/`, `.github/`; hash-locked deps; deploy pinned by digest | container test |
| BookMyShow | §6 | code + 54 legacy run logs |
| Email | `smtp.gmail.com:465` over TLS, 10-s timeout, explicit refusal → retry/failed, ambiguity → uncertain | SMTP tests |
| Backup / DR | Exporter and verifier tools tested on emulators only; no production backup evidence | emulator recovery test |
| App / UX | Streamlit app, Explorer, catalogue untouched. Extend keeps the ID. F6 is the only UX effect. | `app.py:893`, `:546` |
| Actions usage | After cutover: catalogue sync (4×/day, ~3 min), CI on pushes, optional relay runs (<1 min, per user click). No continuous runner. | workflows |
| Monitoring / rollback | §8.3, §10 | — |

---

## 4. Code changes

| Commit | Files |
|---|---|
| `89936bc` | `worker_candidate/overlay/notifications/outbox.py`, `run_worker_service.py`, `run_bms_probe.py` (new), `.dockerignore`, `.github/workflows/monitor-worker-relay.yml`, `runtime.patch` (fenced logging in `monitor/checker.py`); tests `test_outbox_release.py`, `test_bms_probe.py`, `test_fenced_logging.py`, `test_monitor_preservation.py`, `test_worker_service.py`, `test_firebase_recovery_emulator.py` |
| `1df5499` | `worker_candidate/cutover/{bookmyshow-monitor.yml, test_release_isolation.py, README.md}`, `tests/test_cutover_staging.py` |
| `d18b9a9` | `infra/production/{env.sh, logging-metrics.txt, alerts/*.json}`, `tests/test_production_runbook.py`, this report, superseded banner on the 25 Nov runbook |
| backup-isolation commit | `tools/verify_backup_access.py` (new), `tests/test_verify_backup_access.py` (new), `infra/production/env.sh` (DR project), A-2 rewritten, gates G3a–G3d, R11, U12–U13, runbook tests |

Live runtime files (app, legacy monitor, `.github/workflows/*`, `firestore.rules`, locks): **unchanged**
(`tests/test_release_isolation.py` passes).

---

## 5. Test results

| Suite | Where | Result |
|---|---|---|
| Production layout, full history | local (Python 3.13) | **2,153 passed, 48 skipped, 0 failed** (+5 runbook tests in the final commit) |
| Candidate layout (materialised from working tree) | local | **2,206 passed, 51 skipped, 0 failed** |
| Firestore Rules (22) + recovery/fencing/outbox/release (4) | **Firebase emulators** (Auth + Firestore, firebase-tools 13) | **22 + 4 passed** |
| Worker image build | local Docker. Sandbox variant differs only by a build-secret CA mount; the unmodified `Dockerfile.worker` is built in CI (`worker-image`) | built |
| Shadow HTTP (offline container) | local Docker, `--network none` | healthz `{"ready":true,"mode":"shadow"}`; `/check` 1 slot; `/tick` 2 slots; bad bodies 400; unknown 404; concurrent → one 200 + one **503 busy**; UID 65532 |
| Fail-closed start-up | local Docker | no mode / legacy creds / bad authority / project mismatch / no attached identity → all exit 1 |
| Probe packaging | local Docker, no network | `[probe] requests=1` → `BMS_PROBE_NETWORK_ERROR`, exit 5 |
| Relay cutover simulation | scratch worktree | isolation + structure tests pass |
| actionlint 1.7.12 | live workflows; cutover set | clean; clean |
| pip-audit | worker lock; dev lock | no known vulnerabilities; none |
| bandit `-ll` (worker code) | local | 1 medium: B104 bind `0.0.0.0` (required by Cloud Run; accepted) |
| gcloud syntax | gcloud 570.0.0 `--help`, offline | all commands and flags exist, including `asset analyze-iam-policy`, `projects get-ancestors(-iam-policy)`, `projects create`, `billing projects link` and `storage buckets set-iam-policy` (`gcloud beta services identity create` is beta-only: present in Cloud Shell, not installable here) |
| DR access verifier | local | 16 tests: inherited project/org grants, convenience values, `allUsers`, group members, impersonators, incomplete analysis, analysis errors, missing Firestore agent, exact bucket policy, conditional bindings, ancestors, DR-project service agents only |
| Terraform | local | `fmt` OK; `validate` **not run** (registry blocked) |
| Browser/UI tests | in the suites above (Playwright-based tests that need a browser are among the skips) | as reported |

| Category | Status |
|---|---|
| Tested locally | everything above |
| Tested against emulator | Rules, transactions, fencing, outbox, release path, recovery |
| Tested against a real external service | **none in this pass.** No BookMyShow, Gmail, Firebase or GCP call was made. |
| Not tested | Cloud Run, Scheduler OIDC, IAM, Secret Manager, backups, real SMTP, BookMyShow from Cloud Run, log filters, alert policies. All are tomorrow's gates. |

---

## 6. BookMyShow

### 6.1 Technical behaviour (verified)

* Client: `curl-cffi` with browser TLS impersonation. On 401/403 it retries up to 2 more times with a different
  browser profile (1.5–3.5-s pauses). 429 is not retried. Timeout 20 s.
* Per due check of `af1711d4` (admin interval **5 min**; members may choose 10/15/30): **4 reads** (1 date × the base
  event + 3 format siblings), shared across every watcher of the same film/city/date. City re-listing about every
  15 min while a target is unlisted. Tick budget 240 requests.
* Measured legacy load during 23–25 Nov: 509 checks, 148 re-lists, **0 refusals**. About 52 requests/hour for this monitor.
* The worker runs the **same** checker, provider and lock. Extra requests versus legacy: one city re-list on the first
  tick (the discovery clock moves from git to Firestore), and the single probe. Scheduler ticks without due monitors make no requests.
* Cloud Run egress uses Google IPs in Mumbai rather than GitHub/Azure runners. Acceptance is unknown until probe A-6.

### 6.2 Technical vs contractual

* **A — Technical:** probe A-6 proves only that one request from Cloud Run is answered.
* **B — Permission:** no written authorization exists. The client deliberately presents browser fingerprints and
  rotates them after a refusal. A provider could regard that as circumventing its bot controls. Passing the probe does
  **not** mean BookMyShow permits this. Whether to proceed without permission is **your decision**. The legacy engine
  operated the same way since September; the migration does not change the exposure.
* **Lowest-risk option:** send the request below, and stop immediately on any objection or refusal pattern (A-6 / §10).

### 6.3 Request text (ready to send to BookMyShow partnerships/legal)

> Subject: Request for permission — low-volume personal availability alerts (Hyderabad)
>
> Hello,
>
> I run a small non-commercial tool, "TicketRadar", that e-mails a handful of registered users when tickets
> for a show they chose become bookable at a specific theatre and format in Hyderabad. It reads public
> listing data only. It does not book, hold seats, resell, or store personal data from BookMyShow, and every
> alert links users back to BookMyShow to book.
>
> Volume: one shared listing read per film every 5–30 minutes while a user's watch is active (currently one
> film, about 50 requests per hour at most), plus one city listing about every 15 minutes. The requests come
> from a single Google Cloud service in Mumbai.
>
> I would like your written permission for this usage, or guidance on an approved API or partner programme.
> I will adjust frequency or identification as you require, and stop immediately on request.
>
> Contact: <your name, e-mail>
>
> Thank you.

---

## 7. Go / No-Go gates

Each gate lists the **stop state**: what stays paused and what must not be activated if it fails.

| Gate | Pass condition | If it fails |
|---|---|---|
| **G0** CI | Tests workflow green on the commit you build | Do not build. Everything stays as today. |
| **G1** Original monitor | `af1711d4…` exists, not ACTIVE, 5 targets as listed; `6150dc3b…` not ACTIVE; snapshot saved | Stop. Do not deploy fenced; do not Extend. |
| **G2** Rules | Deployed ruleset = `firestore.rules` at `cb9c6a1` | Stop before fenced deploy; investigate Rules. |
| **G3a** DR isolation | `ANCESTORS_OK` (or every folder/org reviewed and passed with `--allow-ancestor`); DR project policy holds only you + its own Google-managed agents | **Upload nothing.** Stop before A-2c. Monitoring stays as today. |
| **G3b** Effective access (before upload) | `BUCKET_POLICY_OK` and `EFFECTIVE_ACCESS_OK`; public access prevention enforced; uniform access on | **Upload nothing.** Fix the named binding. Never widen access in `ticketradar03`. |
| **G3c** Export works | Export `SUCCESSFUL` with `overall_export_metadata` in the DR bucket | Stop before A-7. Do not add project-level roles to make it work. |
| **G3d** Backups complete | PITR enabled; daily schedule; encrypted Auth export present; plaintext gone from `/dev/shm`; re-run of both access checks still passes | Stop before A-7 (no real-data worker). |
| **G4** Private service | IAM has only the invoker SA (and owners); unauthenticated `/healthz` → 403 | Scheduler stays paused; fix IAM. |
| **G5** Shadow + scheduler | 6 shadow tests pass; ≥4 consecutive scheduler `complete`; then `PAUSED` | Scheduler paused; do not deploy fenced. |
| **G6** BookMyShow | `BMS_PROBE_OK movies=N`, N ≈ 40–60, `requests=1` | **Stop the migration.** Scheduler paused, legacy untouched, `af1711d4` stays stopped. Do not retry in a loop. |
| **G7** Legacy off | Workflow state `disabled_manually`; `production` env has a required reviewer; no `in_progress`/`queued` legacy runs | Do not create the control document; do not deploy fenced. |
| **G8** Fenced sink | Start-up line: `fenced`, correct authority, `adc`, `project_pinned: true`, **`sender_configured: false`**, `legacy_key_present: false`; manual tick 200; control lease released | Scheduler paused; kill switch (§10) if needed. |
| **G9** Alerts | 4 metrics + 4 policies created; channel verified | May proceed; watch manually. |
| **G10** Notification | Test monitor: one `failed` row (no mail), then exactly one `accepted` + one inbox mail, no duplicate after a further forced check; test monitor deleted | Remove secrets (§10); scheduler paused; `af1711d4` stays stopped. |
| **G11** Original monitor active | After *Extend*: same ID, ACTIVE, only allowed fields changed, `monitor_state.last_check_at` > Extend time, 5 target verdicts in state and logs, `6150dc3b` still not ACTIVE | Stop the monitor in the app; pause scheduler. |
| **G12** First hour | ~60 `complete`, 0 persistent `failed`, outbox has no `failed`/`uncertain`/stuck `sending`, no 403/429 | §10 |
| **G13** Catalogue | `catalogue-sync.yml` still `active`, cron `0 1,7,13,19 * * *` | Never edited by this runbook. If changed, revert. |

---

## 8. Tomorrow — single checklist

### 8.0 Already done by this repository work (nothing for you to do)

* Worker fixes F1–F5 with tests; image builds; start-up fails closed.
* One-request probe packaged in the image.
* Relay-only cutover staged and simulated (F6).
* All resource names in `infra/production/env.sh`; metrics and alert policies in `infra/production/`.
* Backup isolation designed (dedicated DR project) with a tested pass/fail verifier for effective access.
* Every gcloud command checked against gcloud 570.0.0.

### 8.1 Where you work

**Google Cloud Console → top bar project picker → `ticketradar03` → Cloud Shell icon (`>_`, top right) → *Open Editor* is not needed.**
All commands below are typed in Cloud Shell. Docker, git, gcloud, jq and python3 are preinstalled. You are
authenticated as yourself. Labels: **[READ]** inspects only · **[CHANGE]** creates/changes · **[UI]** a click path.

### 8.2 Steps

#### A-0 Setup — 5 min **[READ]**

```bash
git clone --branch claude/charming-bardeen-9al13c https://github.com/manojanumolu/movie-ticket-radar.git ~/radar
cd ~/radar && git log -1 --format='%H %s'          # record this SHA as BUILD_SHA
export BUILD_SHA=$(git rev-parse HEAD)
source infra/production/env.sh
export PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
gcloud auth list --filter=status:ACTIVE --format='value(account)'
```

* **Expected:** your owner account; a 12-digit project number.
* **Gate G0:** open `https://github.com/manojanumolu/movie-ticket-radar/actions/workflows/tests.yml?query=branch%3Aclaude%2Fcharming-bardeen-9al13c`.
  The run for `BUILD_SHA` must be green (4 jobs).
* **If merged first (optional):** use `--branch main` and the merge SHA instead.

#### A-1 Pre-flight — 15 min **[READ]**

```bash
# Firestore location → DR bucket location
export FS_LOCATION=$(gcloud firestore databases describe --database='(default)' --format='value(locationId)')
case "$FS_LOCATION" in nam5) export DR_LOCATION=US;; eur3) export DR_LOCATION=EU;; *) export DR_LOCATION=$FS_LOCATION;; esac
echo "Firestore=$FS_LOCATION  DR bucket=$DR_LOCATION  Cloud Run=$REGION"
gcloud firestore databases describe --database='(default)' --format='yaml(pointInTimeRecoveryEnablement,deleteProtectionState)'
gcloud firestore backups schedules list --database='(default)'

# G2 Rules: deployed = repository
TOKEN=$(gcloud auth print-access-token); RULES=https://firebaserules.googleapis.com/v1
RS=$(curl -s -H "Authorization: Bearer $TOKEN" "$RULES/projects/$PROJECT/releases/cloud.firestore" | jq -r .rulesetName)
curl -s -H "Authorization: Bearer $TOKEN" "$RULES/$RS" | jq -r '.source.files[0].content' > ~/deployed.rules
git show cb9c6a130b1b3f321435c817fabb133d3742f27b:firestore.rules | diff -q - ~/deployed.rules && echo RULES_MATCH

# Existing IAM + user-managed keys (inventory only)
gcloud projects get-iam-policy "$PROJECT" --flatten=bindings --format='table(bindings.role,bindings.members)'
for sa in $(gcloud iam service-accounts list --format='value(email)'); do echo "== $sa";
  gcloud iam service-accounts keys list --iam-account "$sa" --managed-by=user --format='value(name.basename(),validAfterTime)'; done

# G1 Original monitors (fields only)
curl -s -H "Authorization: Bearer $TOKEN" "$FS/monitors?pageSize=300&mask.fieldPaths=status" \
 | jq -r '.documents[].name | split("/")[-1] | select(test("^(af1711d4|6150dc3b)"))'
export DOOMSDAY_ID=<paste the af1711d4… id>
export OVERLAP_ID=<paste the 6150dc3b… id>
curl -s -H "Authorization: Bearer $TOKEN" "$FS/monitors/$DOOMSDAY_ID" > ~/af1711d4-before.json
chmod 600 ~/af1711d4-before.json
jq '{status:.fields.status.stringValue, until:.fields.monitor_until.stringValue,
     interval:.fields.interval_minutes.integerValue,
     targets:[.fields.targets.arrayValue.values[].mapValue.fields | "\(.name.stringValue) · \(.fmt.stringValue)"]}' ~/af1711d4-before.json
curl -s -H "Authorization: Bearer $TOKEN" "$FS/monitors/$OVERLAP_ID" | jq -r .fields.status.stringValue
# Running monitors now
curl -s -H "Authorization: Bearer $TOKEN" -X POST "$FS:runQuery" -H 'Content-Type: application/json' \
 -d '{"structuredQuery":{"from":[{"collectionId":"monitors"}],"where":{"fieldFilter":{"field":{"fieldPath":"status"},"op":"EQUAL","value":{"stringValue":"ACTIVE"}}}}}' \
 | jq '[.[] | select(.document)] | length'
# Worker collections must not exist yet
curl -s -o /dev/null -w 'control=%{http_code}\n' -H "Authorization: Bearer $TOKEN" "$FS/worker_runtime/control"
```

* **Expected:** `RULES_MATCH`. `af1711d4` status `STOPPED` (or `EXPIRED`), interval `5`, targets:
  `ALLU Cinemas · Dolby Cinema`, `AMB Cinemas · HDR By Barco`,
  `PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction · PXL`, `Prasads Multiplex · PCX`,
  `Prasads Multiplex · HDR By Barco`. `6150dc3b` not `ACTIVE`. Running count known (expected 0). `control=404`.
* **If it fails:** stop (G1/G2). Nothing has been changed yet.

#### A-2 Backups in an isolated DR project — 35 min + export wait **[CHANGE]**

**Why a separate project.** A bucket's effective access is the **union** of its own policy and every IAM policy
above it: the project, any folder, and the organisation. Removing bindings from the bucket cannot subtract a
project-level `roles/storage.*` grant, an inherited folder/org grant, or a role held through a Google group or
service-account impersonation. `ticketradar03` carries identities this runbook cannot vet in advance: the
Firebase Admin SDK service account whose long-lived key is in GitHub (U7), default service accounts, and any
collaborators. So backups go to a **new project, `$DR_PROJECT`**, in which only you hold a role. The export is
written cross-project by `ticketradar03`'s **Firestore service agent**, which is granted on that one bucket only.
Nothing in `ticketradar03`'s IAM changes. Before anything is uploaded, A-2c proves with Policy Analyzer
(inherited policies, expanded groups, impersonation paths) that only approved identities can read or write the bucket.

##### A-2a DR project and APIs **[CHANGE]**

```bash
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com
gcloud projects create "$DR_PROJECT" --name="TicketRadar DR backups"
export DR_PROJECT_NUMBER=$(gcloud projects describe "$DR_PROJECT" --format='value(projectNumber)')
export BILLING=$(gcloud billing projects describe "$PROJECT" --format='value(billingAccountName)')
gcloud billing projects link "$DR_PROJECT" --billing-account="${BILLING#billingAccounts/}"
gcloud services enable storage.googleapis.com cloudasset.googleapis.com --project="$DR_PROJECT"
export BACKUP_ADMIN="user:$(gcloud config get-value account 2>/dev/null)"
export FIRESTORE_AGENT="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-firestore.iam.gserviceaccount.com"
echo "$DR_PROJECT ($DR_PROJECT_NUMBER)  admin=$BACKUP_ADMIN  agent=$FIRESTORE_AGENT"
```

* **Expected:** the project is created; billing is linked; both APIs are enabled.
* **If** `projects create` says the ID is taken: edit `DR_PROJECT` in `infra/production/env.sh` (for example
  `ticketradar03-dr-7k2q`), run `source infra/production/env.sh`, and repeat.
* No backup data exists anywhere yet.

##### A-2b Inherited access — ancestors and project policy **[READ]**

```bash
gcloud projects get-ancestors "$DR_PROJECT" --format=json > ~/dr-ancestors.json
python3 ~/radar/tools/verify_backup_access.py ancestors ~/dr-ancestors.json
gcloud projects get-ancestors-iam-policy "$DR_PROJECT" --include-deny --format=json > ~/dr-ancestor-policies.json
jq -r '.[] | .id as $id | (.policy.bindings // [])[] | "\($id)  \(.role)  \(.members|join(","))"' ~/dr-ancestor-policies.json
```

* **Expected:** `project <DR_PROJECT>` then `ANCESTORS_OK` (a personal-account project has no folder or organisation).
  The policy listing shows `roles/owner  user:<you>`, plus only `service-<DR_PROJECT_NUMBER>@…` Google-managed
  agents with `*.serviceAgent` roles.
* **Exit code 3 (folder or organisation found):** the organisation's policies apply to every project in it. Run
  A-2c with `--organization=<ORG_ID>` instead of `--project`; this needs `roles/cloudasset.viewer` at the organisation.
  Have the organisation admin review the listed ancestor bindings. Re-run the ancestors check with
  `--allow-ancestor organizations/<ORG_ID>` only after that review. **If you cannot, stop: upload nothing** (G3a).
* **Any other member on the DR project** (user, group, user-managed service account): remove it or stop (G3a).

##### A-2c Bucket with an exact policy, verified before any upload **[CHANGE, then READ]**

```bash
gcloud storage buckets create "$DR_BUCKET" --project="$DR_PROJECT" --location="$DR_LOCATION" \
  --uniform-bucket-level-access --public-access-prevention --soft-delete-duration=7d
umask 077
cat > ~/dr-bucket-policy.json <<POLICY
{"bindings": [
  {"role": "roles/storage.objectAdmin", "members": ["$BACKUP_ADMIN"]},
  {"role": "roles/storage.admin",       "members": ["$FIRESTORE_AGENT"]}
]}
POLICY
gcloud storage buckets set-iam-policy "$DR_BUCKET" ~/dr-bucket-policy.json      # replaces the default convenience bindings

# 1. The bucket's own policy is exactly the two bindings
gcloud storage buckets get-iam-policy "$DR_BUCKET" --format=json > ~/dr-bucket-iam.json
python3 ~/radar/tools/verify_backup_access.py bucket ~/dr-bucket-iam.json --admin "$BACKUP_ADMIN" --agent "$FIRESTORE_AGENT"
# 2. Effective access, including inherited project/folder/org policies, expanded groups and impersonation
gcloud asset analyze-iam-policy --project="$DR_PROJECT" --full-resource-name="$DR_BUCKET_RESOURCE" \
  --permissions=storage.objects.get,storage.objects.list,storage.objects.create,storage.objects.delete,storage.buckets.setIamPolicy \
  --expand-groups --analyze-service-account-impersonation --show-response --format=json > ~/dr-effective.json
python3 ~/radar/tools/verify_backup_access.py effective ~/dr-effective.json \
  --admin "$BACKUP_ADMIN" --agent "$FIRESTORE_AGENT" --dr-project-number "$DR_PROJECT_NUMBER"
# 3. Public access prevention and uniform access are on
gcloud storage buckets describe "$DR_BUCKET" --format='yaml(public_access_prevention,uniform_bucket_level_access,soft_delete_policy)'
```

* **Expected:**
  1. `BUCKET_POLICY_OK`.
  2. One line per identity with the role and where it is attached, then `EFFECTIVE_ACCESS_OK`. The only identities:
     `user:<you>` (bucket `objectAdmin`, project `owner`) and
     `serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-firestore…` (bucket `storage.admin`). Optionally there are lines marked
     `[Google-managed agent of the DR project]`.
  3. `public_access_prevention: enforced`, `uniform_bucket_level_access: true`.
* **`set-iam-policy` says the Firestore agent does not exist:** run
  `gcloud beta services identity create --service=firestore.googleapis.com --project="$PROJECT"`, then repeat.
  This creates Google's own agent and grants nothing in `ticketradar03`.
* **Any `UNEXPECTED`, `MISSING` or `ERROR` line, or exit ≠ 0:** **stop: upload nothing** (G3b). Fix the named
  binding and repeat A-2c. Analysis output with `PERMISSION_DENIED` means you lack Cloud Asset permission at that
  scope, so the check is unproven: stop.

##### A-2d Firestore export, PITR and schedule **[CHANGE — only after `EFFECTIVE_ACCESS_OK`]**

```bash
export EXPORT_PATH="$DR_BUCKET/firestore/$(date -u +%Y%m%dT%H%MZ)"
gcloud firestore export "$EXPORT_PATH" --project="$PROJECT" --database='(default)' --async
gcloud firestore databases update --database='(default)' --enable-pitr
gcloud firestore backups schedules create --database='(default)' --recurrence=daily --retention=14d
# [READ] repeat until SUCCESSFUL
gcloud firestore operations list --database='(default)' --format='table(name.basename(),metadata.operationState,metadata.endTime)' --limit=3
gcloud storage ls "$EXPORT_PATH/**" | grep overall_export_metadata
```

* **Expected:** `SUCCESSFUL`, and one `…overall_export_metadata` object. That proves the Firestore service agent can
  write cross-project with exactly the bucket-scoped grant.
* **`PERMISSION_DENIED` / `FAILED`:** the agent binding is wrong. Re-run A-2c checks 1–2. **Do not widen access** with
  project-level roles. Stop (G3c).
* PITR versions and managed backups stay inside `ticketradar03`. They are reachable only by identities that already
  hold Firestore permissions there, the same population that can read the live database. They do not widen access.

##### A-2e Auth export — encrypted in memory, then uploaded **[CHANGE]**

```bash
umask 077
npx -y firebase-tools@13 login --no-localhost
npx -y firebase-tools@13 auth:export /dev/shm/auth.json --format=json --project "$PROJECT"   # RAM only, never the persistent home disk
jq '.users | length' /dev/shm/auth.json                                                       # = Console → Authentication → Users count
gpg --pinentry-mode loopback --symmetric --cipher-algo AES256 --output /dev/shm/auth-$(date -u +%Y%m%d).json.gpg /dev/shm/auth.json
shred -u /dev/shm/auth.json
gcloud storage cp /dev/shm/auth-*.json.gpg "$DR_BUCKET/auth/" && shred -u /dev/shm/auth-*.json.gpg
ls /dev/shm/ | grep -c auth || true                                                           # 0
```

Choose a long passphrase and store it in your password manager. **[UI]** Firebase console → Authentication →
Users → ⋮ → **Password hash parameters**: store them next to the passphrase. Both stay outside Google Cloud.

##### A-2f Final backup verification — G3d **[READ]**

```bash
gcloud storage ls -l "$EXPORT_PATH/" "$DR_BUCKET/auth/"
gcloud storage buckets get-iam-policy "$DR_BUCKET" --format=json > ~/dr-bucket-iam.json
python3 ~/radar/tools/verify_backup_access.py bucket ~/dr-bucket-iam.json --admin "$BACKUP_ADMIN" --agent "$FIRESTORE_AGENT"
gcloud asset analyze-iam-policy --project="$DR_PROJECT" --full-resource-name="$DR_BUCKET_RESOURCE" \
  --permissions=storage.objects.get,storage.objects.list,storage.objects.create,storage.objects.delete,storage.buckets.setIamPolicy \
  --expand-groups --analyze-service-account-impersonation --show-response --format=json > ~/dr-effective.json
python3 ~/radar/tools/verify_backup_access.py effective ~/dr-effective.json \
  --admin "$BACKUP_ADMIN" --agent "$FIRESTORE_AGENT" --dr-project-number "$DR_PROJECT_NUMBER"
gcloud firestore databases describe --database='(default)' --format='value(pointInTimeRecoveryEnablement)'
gcloud firestore backups schedules list --database='(default)'
```

* **G3d pass:** both objects are listed (the export folder and one `.json.gpg`); `BUCKET_POLICY_OK`; `EFFECTIVE_ACCESS_OK`;
  `POINT_IN_TIME_RECOVERY_ENABLED`; one daily schedule.
* **Optional restore drill (+20 min):** `gcloud firestore databases create --database=restore-drill --location="$FS_LOCATION"`,
  then `gcloud firestore import "$EXPORT_PATH" --database=restore-drill` (the same agent reads the bucket). Compare
  the `monitors` count, then `gcloud firestore databases delete --database=restore-drill`.

#### A-3 Identities and registry — 5 min **[CHANGE]**

```bash
gcloud iam service-accounts create "$WORKER_SA_NAME"  --display-name="Radar monitor worker (Cloud Run)"
gcloud iam service-accounts create "$INVOKER_SA_NAME" --display-name="Radar scheduler invoker"
gcloud iam service-accounts create "$PROBE_SA_NAME"   --display-name="Radar BookMyShow probe (no data access)"
gcloud iam service-accounts add-iam-policy-binding "$INVOKER_SA" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountTokenCreator
gcloud artifacts repositories create "$AR_REPO" --repository-format=docker --location="$REGION" \
  --description="TicketRadar monitor worker images"
```

No keys are created, and the worker gets **no** data role yet.

#### A-4 Build and push — 10 min **[CHANGE]**

```bash
cd ~/radar && python3 tools/materialize_worker_candidate.py ~/radar-worker-$BUILD_SHA && cd ~/radar-worker-$BUILD_SHA
export IMAGE=${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/${IMAGE_NAME}:${BUILD_SHA:0:12}
gcloud auth configure-docker ${REGION}-docker.pkg.dev --quiet
docker build --file Dockerfile.worker --tag "$IMAGE" . && docker push "$IMAGE"
export IMAGE_DIGEST=${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/${IMAGE_NAME}@$(gcloud artifacts docker images describe "$IMAGE" --format='value(image_summary.digest)')
echo "$IMAGE_DIGEST" | tee ~/image-digest.txt
```

* **Expected:** pip finishes `Successfully installed …curl-cffi-0.17.0…`; the digest ends in `@sha256:<64 hex>`.
* **If it fails:** nothing is deployed. Re-run. Cloud Shell's disk is 5 GB; run `docker system prune -f` if it is full.

#### A-5 Shadow deploy, tests, scheduler — 20 min **[CHANGE]**

```bash
gcloud run deploy "$SERVICE" --image="$IMAGE_DIGEST" --region="$REGION" --service-account="$WORKER_SA" \
  --no-allow-unauthenticated --ingress=all --max-instances=1 --min-instances=0 --concurrency=4 \
  --timeout=55 --cpu=1 --memory=512Mi --set-env-vars=WORKER_MODE=shadow --revision-suffix=shadow-${BUILD_SHA:0:7}
export URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')
gcloud run services add-iam-policy-binding "$SERVICE" --region="$REGION" --member="serviceAccount:$INVOKER_SA" --role=roles/run.invoker
gcloud run services get-iam-policy "$SERVICE" --region="$REGION" --format=json | jq -r '.bindings[] | "\(.role) \(.members|join(","))"'

ID=$(gcloud auth print-identity-token)
curl -s -o /dev/null -w 'unauth=%{http_code}\n' "$URL/healthz"                                       # 403
curl -s -H "Authorization: Bearer $ID" "$URL/healthz"                                                # {"ready": true, "mode": "shadow"}
curl -s -H "Authorization: Bearer $ID" -H 'Content-Type: application/json' --data '{"monitor_id":"fixture-monitor","force":true}' "$URL/check"   # {"slots": [{"checked": 1, "changes": 0}]}
curl -s -H "Authorization: Bearer $ID" -X POST "$URL/tick"                                           # 200, 2 slots
curl -s -o /dev/null -w 'bad=%{http_code}\n' -H "Authorization: Bearer $ID" -H 'Content-Type: application/json' --data '{"x":1}' "$URL/tick"   # 400
( curl -s -o /dev/null -w 'p1=%{http_code}\n' -H "Authorization: Bearer $ID" -X POST "$URL/tick" &
  curl -s -o /dev/null -w 'p2=%{http_code}\n' -H "Authorization: Bearer $ID" -X POST "$URL/tick" & wait )   # one 200, one 503

gcloud scheduler jobs create http "$SCHEDULER_JOB" --location="$REGION" --schedule='* * * * *' --time-zone='Etc/UTC' \
  --uri="$URL/tick" --http-method=POST --oidc-service-account-email="$INVOKER_SA" --oidc-token-audience="$URL" \
  --attempt-deadline=60s --max-retry-attempts=0
sleep 300
gcloud logging read "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND jsonPayload.worker_result:*" \
  --freshness=6m --format='value(timestamp,jsonPayload.worker_result)' --limit=10
gcloud logging read "resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"$SCHEDULER_JOB\"" --freshness=6m --limit=10 \
  --format='value(timestamp,httpRequest.status,jsonPayload.status)'
gcloud scheduler jobs pause "$SCHEDULER_JOB" --location="$REGION"
gcloud scheduler jobs describe "$SCHEDULER_JOB" --location="$REGION" --format='value(state)'          # PAUSED
```

* **G4:** IAM shows `roles/run.invoker serviceAccount:radar-monitor-invoker@…` and no `allUsers`/`allAuthenticatedUsers`;
  unauthenticated request → 403.
* **G5:** the outputs above match, ≥4 `complete`, scheduler HTTP 200, then `PAUSED`.
* **If it fails:** the scheduler stays paused. Shadow mode touches no data, so it is safe to leave while you fix the problem.

#### A-6 BookMyShow probe (exactly one request) — 5 min **[CHANGE, temporary]**

```bash
gcloud run jobs create "$PROBE_JOB" --region="$REGION" --image="$IMAGE_DIGEST" --service-account="$PROBE_SA" \
  --max-retries=0 --task-timeout=120s --command=python --args=run_bms_probe.py
gcloud run jobs execute "$PROBE_JOB" --region="$REGION" --wait
sleep 20
gcloud logging read "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$PROBE_JOB\"" --freshness=10m \
  --format='value(textPayload)' | grep -E 'probe|BMS_PROBE'
gcloud run jobs delete "$PROBE_JOB" --region="$REGION" --quiet
```

* **Expected:** `[probe] requests=1 status=200` and `BMS_PROBE_OK movies=<40–60>`.
* **G6 fail** (`BLOCKED`/`RATE_LIMITED`/other): **stop the migration.** Leave the scheduler paused, the legacy workflow
  untouched and `af1711d4` stopped. Do not retry repeatedly. Delete the job. You can delete the service later.

#### A-7 Disable legacy, lock `production` — 10 min **[UI]**

1. `https://github.com/manojanumolu/movie-ticket-radar/actions` → left list **BookMyShow monitor** → **⋯** (top right of
   the run list) → **Disable workflow**.
2. `https://github.com/manojanumolu/movie-ticket-radar/settings/environments` → **production** → tick **Required
   reviewers** → add yourself → **Save protection rules**. (`rules-audit` then also waits for approval. That is intended.)
3. Confirm **Catalogue sync** is still listed as enabled. **Do not touch it.**
4. **[READ]** In the BookMyShow monitor run list, filter "Status: In progress" and "Queued" → both empty.

* **G7 fail:** do not continue to A-8.

#### A-8 Worker authority and fenced sink — 15 min **[CHANGE]**

```bash
for s in "$SECRET_GMAIL_ADDRESS" "$SECRET_GMAIL_PASSWORD"; do
  echo "Enter the value for $s, then press Ctrl-D twice:"; gcloud secrets create "$s" --replication-policy=automatic --data-file=-
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$WORKER_SA" --role=roles/secretmanager.secretAccessor; done
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$WORKER_SA" --role=roles/datastore.user --condition=None
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$WORKER_SA" --role=roles/firebaseauth.viewer --condition=None

TOKEN=$(gcloud auth print-access-token)
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
 "$FS/worker_runtime/control?currentDocument.exists=false" \
 -d "{\"fields\":{\"authority\":{\"stringValue\":\"$AUTHORITY\"},\"epoch\":{\"integerValue\":\"1\"},\"holder\":{\"stringValue\":\"\"},\"lease_until\":{\"stringValue\":\"\"}}}" | jq -c .fields

gcloud run deploy "$SERVICE" --image="$IMAGE_DIGEST" --region="$REGION" --service-account="$WORKER_SA" \
  --no-allow-unauthenticated --ingress=all --max-instances=1 --min-instances=0 --concurrency=4 \
  --timeout=55 --cpu=1 --memory=512Mi --revision-suffix=fenced-sink-${BUILD_SHA:0:7} \
  --set-env-vars=WORKER_MODE=fenced,FIREBASE_CREDENTIAL_MODE=adc,FIREBASE_PROJECT_ID=$PROJECT,FIREBASE_EXPECTED_PROJECT=$PROJECT,WORKER_AUTHORITY=$AUTHORITY,TZ=Asia/Kolkata
ID=$(gcloud auth print-identity-token)
curl -s -H "Authorization: Bearer $ID" "$URL/healthz"                                                # {"ready": true, "mode": "fenced"}
curl -s -H "Authorization: Bearer $ID" -X POST "$URL/tick"                                           # 200
gcloud logging read "resource.labels.service_name=\"$SERVICE\" AND jsonPayload.worker_start=\"fenced\"" --freshness=10m --limit=1 --format=json | jq -c '.[0].jsonPayload'
curl -s -H "Authorization: Bearer $TOKEN" "$FS/worker_runtime/control" | jq -c '.fields'
```

* **Expected `jq -c .fields`:** `epoch` shows `{"integerValue":"1"}`.
* **G8:** start-up line `{"worker_start":"fenced","authority":"ticketradar03-cloudrun-v1","credential_mode":"adc","project_pinned":true,"sender_configured":false,"legacy_key_present":false}`.
  The tick returns 200. Afterwards `holder` and `lease_until` are `""`.
* **If the tick returns 503:** read
  `gcloud logging read "resource.labels.service_name=\"$SERVICE\"" --freshness=5m --limit=20 --format='value(textPayload,jsonPayload)'`.
  A Firestore 403 means the IAM bindings above are missing or have not propagated (wait 2 min). `LeaseLost: worker
  authority is disabled` means the control document is wrong.

#### A-9 Log metrics and alerts — 15 min **[CHANGE]**

```bash
while IFS='|' read -r name desc filter; do case "$name" in \#*|'') continue;; esac
  gcloud logging metrics create "$name" --description="$desc" --log-filter="$filter"; done < ~/radar/infra/production/logging-metrics.txt
```

**[UI]** Console → **Monitoring → Alerting → Edit notification channels → Email → Add new** → your address →
**Save**. Copy the channel ID from **Monitoring → Alerting → Edit notification channels**.

```bash
export CHANNEL_ID=<numeric id>
for f in ~/radar/infra/production/alerts/*.json; do
  sed "s#CHANNEL_ID#$CHANNEL_ID#" "$f" > /tmp/policy.json && gcloud monitoring policies create --policy-from-file=/tmp/policy.json; done
gcloud monitoring policies list --format='value(displayName,enabled)'
```

* **G9:** 4 metrics and 4 policies exist. The "silent for 15 min" policy fires while the scheduler is paused. That is expected now; it confirms delivery.

#### A-10 Controlled notification test (one email to you) — 25 min **[CHANGE]**

1. **[UI]** In the TicketRadar app, signed in as yourself, create **Test-A**: Hyderabad → a film with shows today that
   are bookable now → one theatre → **Any format** → interval **5 min** → end in 2 h → **Start monitoring**.
   A red "first check could not be started" banner is **expected** (legacy disabled, F6).
2. Sink check (no sender mounted):
   ```bash
   TOKEN=$(gcloud auth print-access-token); ID=$(gcloud auth print-identity-token)
   export TEST_ID=$(curl -s -H "Authorization: Bearer $TOKEN" -X POST "$FS:runQuery" -H 'Content-Type: application/json' \
    -d '{"structuredQuery":{"from":[{"collectionId":"monitors"}],"where":{"fieldFilter":{"field":{"fieldPath":"status"},"op":"EQUAL","value":{"stringValue":"ACTIVE"}}}}}' \
    | jq -r '[.[] | select(.document)] | if length==1 then .[0].document.name | split("/")[-1] else "STOP: \(length) active" end')
   echo "$TEST_ID"                       # must be one id; anything else → STOP
   curl -s -H "Authorization: Bearer $ID" -H 'Content-Type: application/json' --data "{\"monitor_id\":\"$TEST_ID\",\"force\":true}" "$URL/check"
   outbox(){ curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" -X POST "$FS:runQuery" -H 'Content-Type: application/json' \
    -d "{\"structuredQuery\":{\"from\":[{\"collectionId\":\"monitor_outbox\"}],\"where\":{\"fieldFilter\":{\"field\":{\"fieldPath\":\"monitor_id\"},\"op\":\"EQUAL\",\"value\":{\"stringValue\":\"$TEST_ID\"}}}}}" \
    | jq -r '.[] | select(.document) | .document.fields | [.status.stringValue, .attempts.integerValue, .subject.stringValue] | @tsv'; }
   outbox                                # exactly: failed  1  <subject>   — and NO email arrives
   ```
3. Mount the sender and repeat once:
   ```bash
   gcloud run services update "$SERVICE" --region="$REGION" --revision-suffix=fenced-${BUILD_SHA:0:7} \
     --set-secrets=GMAIL_ADDRESS=${SECRET_GMAIL_ADDRESS}:1,GMAIL_APP_PASSWORD=${SECRET_GMAIL_PASSWORD}:1
   gcloud logging read "resource.labels.service_name=\"$SERVICE\" AND jsonPayload.worker_start=\"fenced\"" --freshness=5m --limit=1 --format=json | jq -c '.[0].jsonPayload.sender_configured'   # true
   ID=$(gcloud auth print-identity-token)
   curl -s -H "Authorization: Bearer $ID" -H 'Content-Type: application/json' --data "{\"monitor_id\":\"$TEST_ID\",\"force\":true}" "$URL/check"
   outbox                                # failed 1 … AND accepted 1 …
   curl -s -H "Authorization: Bearer $ID" -H 'Content-Type: application/json' --data "{\"monitor_id\":\"$TEST_ID\",\"force\":true}" "$URL/check"
   outbox                                # still exactly one accepted, no new pending
   ```
4. Your inbox: **exactly one** TicketRadar email from the usual sender (check spam). Its headers show `Message-ID: <…@ticket-radar.invalid>`.
5. **[UI]** App → Monitors → Test-A card → **Delete**. Then `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $(gcloud auth print-access-token)" "$FS/monitors/$TEST_ID"` → `404`.

* **G10 fail:**
  `gcloud run services update "$SERVICE" --region="$REGION" --remove-secrets=GMAIL_ADDRESS,GMAIL_APP_PASSWORD`.
  Keep the scheduler paused and do not extend `af1711d4`. `uncertain` instead of `accepted`: check the sender's Gmail
  *Sent* folder for that Message-ID; do not resend.

#### A-11 Activate and reactivate `af1711d4` — 15 min **[CHANGE]**

```bash
gcloud scheduler jobs resume "$SCHEDULER_JOB" --location="$REGION"
date -u +%Y-%m-%dT%H:%M:%SZ | tee ~/extend-time.txt
```

**[UI]** App, signed in as the owner of `af1711d4` → **Monitors** → **Finished** → *Avengers: Doomsday* card →
**Extend by 24 hours**. Expected message: *"Extended by 24 hours — monitoring is active again."* Do **not** press
*Check now* or *Retry* until Stage 2 is applied. Do **not** touch `6150dc3b`.

```bash
sleep 120; TOKEN=$(gcloud auth print-access-token)
curl -s -H "Authorization: Bearer $TOKEN" "$FS/monitors/$DOOMSDAY_ID" > ~/af1711d4-after.json
strip='del(.updateTime) | .fields |= del(.status, .monitor_until, .stopped_at, .stopped_reason, .problem, .first_check_requested_at)'
diff <(jq -S "$strip" ~/af1711d4-before.json) <(jq -S "$strip" ~/af1711d4-after.json) && echo MONITOR_PRESERVED
jq -r '.fields.status.stringValue, .fields.monitor_until.stringValue, (.fields.targets.arrayValue.values|length)' ~/af1711d4-after.json
curl -s -H "Authorization: Bearer $TOKEN" "$FS/monitor_state/$DOOMSDAY_ID" \
 | jq -r '.fields.last_check_at.stringValue, (.fields.targets.mapValue.fields | to_entries[] | "\(.key)  \(.value.mapValue.fields.availability.stringValue)")'
gcloud logging read "resource.labels.service_name=\"$SERVICE\" AND textPayload:\"${DOOMSDAY_ID:0:8}\"" --freshness=5m --limit=3 --format='value(textPayload)'
gcloud logging read "resource.labels.service_name=\"$SERVICE\" AND textPayload:\" -> \"" --freshness=5m --limit=10 --format='value(textPayload)'
curl -s -H "Authorization: Bearer $TOKEN" "$FS/monitors/$OVERLAP_ID" | jq -r .fields.status.stringValue
```

* **G11:** `MONITOR_PRESERVED`; `ACTIVE`; a new end time; `5`; `last_check_at` later than `~/extend-time.txt`;
  5 target keys with availability values; logs show `[checker] af1711d4 — Avengers: Doomsday (5 target(s))` and the 5
  target lines (ALLU·Dolby Cinema, AMB·HDR By Barco, PVR Lakeshore·PXL, Prasads·PCX, Prasads·HDR By Barco); `6150dc3b`
  not `ACTIVE`.
* If the diff shows only new sibling event codes under `movie` (discovery found a new format listing), that is
  the worker's normal behaviour and passes. Any change to `targets`, `notify_email`, `interval_minutes`, `owner_uid` or
  `created_at` fails G11.
* AMB was already announced by the legacy worker, so **no email is expected** unless a genuinely new showtime or transition has appeared.
* **If it fails:** App → **Stop** on the monitor; `gcloud scheduler jobs pause …`; investigate.

#### A-12 First hour — 60 min elapsed, ~10 min active **[READ]**

```bash
gcloud logging read "resource.labels.service_name=\"$SERVICE\" AND jsonPayload.worker_result:*" --freshness=60m \
  --format='value(jsonPayload.worker_result)' | sort | uniq -c                                       # ~60 complete
gcloud logging read "resource.type=\"cloud_scheduler_job\" AND severity>=ERROR" --freshness=60m --limit=10
TOKEN=$(gcloud auth print-access-token)
curl -s -H "Authorization: Bearer $TOKEN" -X POST "$FS:runQuery" -H 'Content-Type: application/json' \
 -d '{"structuredQuery":{"from":[{"collectionId":"monitor_outbox"}],"where":{"fieldFilter":{"field":{"fieldPath":"status"},"op":"IN","value":{"arrayValue":{"values":[{"stringValue":"pending"},{"stringValue":"sending"},{"stringValue":"retry"},{"stringValue":"uncertain"}]}}}}}}' \
 | jq '[.[] | select(.document)] | length'                                                            # 0 (or transient)
gcloud logging read "resource.labels.service_name=\"$SERVICE\" AND (textPayload:\"403\" OR textPayload:\"429\" OR textPayload:\"refused\")" --freshness=60m --limit=5
```

* **G12/G13:** as in §7. Also confirm Catalogue sync is still enabled in the Actions list. Then **Extend** daily (24 h per press, ≤30 days).

### 8.3 Daily operation

* *Extend by 24 hours* once a day until the 17 Dec show.
* Alerts arrive by email (A-9). A `failed` or `cancelled` mail row is re-detected automatically (F1). An `uncertain` row
  needs the Gmail *Sent* check before any resend.

### 8.4 Stage 2 — "Check now" / no false banner (optional, after A-12; 45–60 min)

Requires two merges you approve. Production effect: the legacy workflow file becomes relay-only.

1. **[UI]** Open a PR from `claude/charming-bardeen-9al13c` to `main`. Merge once CI is green. Nothing in the live
   runtime changes; Streamlit redeploys identical app code.
2. In Cloud Shell, run the four `git`/`cp` lines in `worker_candidate/cutover/README.md` on a new branch, then push and
   open a PR. CI must be green. Merge.
3. Federation and relay identity **[CHANGE]**:
   ```bash
   source ~/radar/infra/production/env.sh
   gcloud iam workload-identity-pools create "$WIF_POOL" --location=global --display-name="GitHub Actions"
   gcloud iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" --location=global --workload-identity-pool="$WIF_POOL" \
     --issuer-uri=https://token.actions.githubusercontent.com \
     --attribute-mapping="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref,attribute.environment=assertion.environment" \
     --attribute-condition="assertion.repository_id=='$GITHUB_REPO_ID' && assertion.repository_owner_id=='$GITHUB_OWNER_ID' && assertion.ref=='refs/heads/main' && assertion.environment=='monitor-dispatch' && assertion.job_workflow_ref.startsWith('$GITHUB_REPO/.github/workflows/monitor-worker-relay.yml@refs/heads/main')"
   gcloud iam service-accounts create "$RELAY_SA_NAME" --display-name="Radar GitHub relay invoker"
   gcloud iam service-accounts add-iam-policy-binding "$RELAY_SA" --role=roles/iam.workloadIdentityUser \
     --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/attribute.repository_id/${GITHUB_REPO_ID}"
   gcloud run services add-iam-policy-binding "$SERVICE" --region="$REGION" --member="serviceAccount:$RELAY_SA" --role=roles/run.invoker
   echo "projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}"
   ```
4. **[UI]** `Settings → Environments → New environment` → `monitor-dispatch` → *Deployment branches and tags* →
   *Selected branches* → add `main`. Then `Settings → Secrets and variables → Actions → Variables → New repository variable`:
   `RADAR_WORKER_SERVICE_URL` = `$URL`, `RADAR_RELAY_WIF_PROVIDER` = the printed provider path,
   `RADAR_RELAY_INVOKER` = `$RELAY_SA`, `RADAR_ENABLE_MONITOR_RELAY` = `true`.
5. **[UI]** Actions → *BookMyShow monitor* → **Enable workflow**. It is now relay-only. Old legacy runs cannot obtain
   secrets: the `production` environment requires your approval, so **never approve a `production` request from a
   "BookMyShow monitor" run.**
6. Test: App → `af1711d4` card → *Check now*. Expect the Actions run *BookMyShow monitor* → job `relay / invoke` to go
   green in < 1 min, and a worker log `"slots": [...]` with `checked: 1`.

---

## 9. Time estimate (tomorrow)

| Step | Hands-on | Elapsed |
|---|---|---|
| A-0 setup + G0 | 5 min | 5 min |
| A-1 pre-flight | 15 | 15 |
| A-2 DR project, access proofs, backups + Auth | 35 | 35–50 (export) |
| A-3 identities | 5 | 5 |
| A-4 build | 5 | 10 |
| A-5 shadow + scheduler | 15 | 20 |
| A-6 probe | 5 | 5 |
| A-7 GitHub | 10 | 10 |
| A-8 fenced sink | 15 | 15 |
| A-9 alerts | 15 | 15 |
| A-10 notification test | 20 | 25 |
| A-11 activate `af1711d4` | 10 | 15 |
| A-12 first hour | 10 | 60 |
| **Total** | **≈ 2 h 50 min – 3 h 40 min** | **≈ 4 h 25 min – 5 h 10 min** |

Add about 30 min of contingency for the first use of Cloud Shell or Firebase CLI login. Stage 2 is an extra 45–60 min (optional).

---

## 10. Rollback and recovery

| Situation | Command | Effect |
|---|---|---|
| Stop all checks | `gcloud scheduler jobs pause "$SCHEDULER_JOB" --location="$REGION"` | ≤1 min |
| Stop sending only | `gcloud run services update "$SERVICE" --region="$REGION" --remove-secrets=GMAIL_ADDRESS,GMAIL_APP_PASSWORD` | Checks continue. New rows end `failed` and are re-detected once the sender returns (F1). |
| Fence an in-flight invocation | bump epoch (below) | The old holder's next write raises `LeaseLost` |
| Kill switch | set authority (below) | Every invocation refuses |
| Bad revision | `gcloud run revisions list --service "$SERVICE" --region "$REGION"` then `gcloud run services update-traffic "$SERVICE" --region="$REGION" --to-revisions=<good>=100` | Instant |
| Stop one monitor | App → **Stop** | ID/history kept |
| Data damage | Import the export **into a new database** first (`gcloud firestore databases create --database=restore-…` + `gcloud firestore import`), or PITR read; production restore is a separate decision | — |

```bash
TOKEN=$(gcloud auth print-access-token)
# bump epoch (use the next integer each time)
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
 "$FS/worker_runtime/control?updateMask.fieldPaths=epoch&updateMask.fieldPaths=holder&updateMask.fieldPaths=lease_until" \
 -d '{"fields":{"epoch":{"integerValue":"2"},"holder":{"stringValue":""},"lease_until":{"stringValue":""}}}'
# kill switch, and its undo
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
 "$FS/worker_runtime/control?updateMask.fieldPaths=authority" -d '{"fields":{"authority":{"stringValue":"disabled-20261126"}}}'
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
 "$FS/worker_runtime/control?updateMask.fieldPaths=authority" -d "{\"fields\":{\"authority\":{\"stringValue\":\"$AUTHORITY\"}}}"
```

**Re-enabling the legacy monitor workflow is not a rollback.** It runs unfenced and recreates the Actions usage pattern.
The `production` reviewer gate keeps it from running unattended.

---

## 11. Security review

| Area | Finding |
|---|---|
| Credentials in repository | None (tracked-file scan; only placeholders and test literals) |
| Worker identity | Attached service account, ADC, project-pinned; fenced mode refuses legacy keys. **No downloadable key is created anywhere in this runbook.** |
| Network exposure | Cloud Run private (IAM invoker only); ingress `all` is needed for Scheduler and the relay |
| Least privilege | Worker: `datastore.user`, `firebaseauth.viewer`, per-secret accessor. Invoker: `run.invoker` on one service. Probe: nothing. Relay: `workloadIdentityUser`, condition-pinned to repository ID, owner ID, `main`, environment, workflow. |
| Firebase Rules | Unchanged. Worker-only collections are client-denied (emulator-verified). IAM bypasses Rules by design. |
| Secrets | Gmail app password in Secret Manager, version-pinned, readable only by the worker SA. Values never printed; the start-up log reports booleans only. |
| Backups | **Dedicated DR project** (no inherited access from `ticketradar03`). Bucket policy is exactly owner `objectAdmin` + source Firestore agent `storage.admin`, both bucket-scoped. Public access prevented, uniform access, soft delete 7 d. Effective access proven by Policy Analyzer (inheritance, groups, impersonation) before and after upload. Auth export encrypted in RAM (AES-256); passphrase and hash parameters kept outside Google Cloud. |
| Logs | Monitor details move from public Actions logs to private Cloud Logging. Addresses are never printed. |
| Dependencies | Hash-locked; pip-audit clean (worker and dev locks) |
| Static analysis | bandit: B104 only (required); actionlint clean |
| Residual | U6, U7 (§2.4) |

---

## 12. GitHub Actions after migration (facts, no speculation)

* Disabled: *BookMyShow monitor* (A-7); with Stage 2 it becomes relay-only (< 1 min per user click).
* Unchanged: *Catalogue sync* `0 1,7,13,19 * * *` (~3 min, 4×/day); *Tests* on push/PR; manual diagnostic workflows.
* No continuous or self-dispatching runner remains. Whether GitHub would consider any remaining usage objectionable is
  not something this report can determine.

---

## 13. Backup procedure — safety verdict

**Safe to execute tomorrow, as written in A-2, with its gates.** Conditions:

* Backup data never lands in `ticketradar03` storage. It goes to a dedicated project whose only principals are you
  and Google-managed agents, so inherited `ticketradar03` access (including the GitHub-held Admin SDK key) cannot reach it.
* No byte is uploaded until G3a (no unreviewed folder/organisation) and G3b (Policy Analyzer: only you and the
  Firestore service agent, counting inheritance, groups and impersonation) pass. Both are re-checked after upload (G3d).
* The Firestore service agent's ability to export is proven by the export itself (G3c), with only the bucket-scoped
  grant. If it fails, the procedure stops rather than widening access.
* The Auth export is encrypted before upload, and its plaintext lives only in RAM. Even an access mistake would expose ciphertext only.
* No IAM in `ticketradar03` is changed by A-2.

**What remains unproven until you run it:** the real Policy Analyzer result (U12). With an organisation above
your account, the analysis must run at organisation scope, or the backups stop. That is the one possible blocker,
and A-2b reports it before any data moves.

