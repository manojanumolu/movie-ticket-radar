# Production Migration Runbook — GitHub Actions monitor → private Cloud Run + Cloud Scheduler

| | |
|---|---|
| Prepared | 25 November 2026 |
| Source of truth | `main` = `cb9c6a130b1b3f321435c817fabb133d3742f27b` (PR #6 merged 19:40:49Z, head `f8e7c91`) |
| Project | `ticketradar03` |
| Companion | [`2026-11-25-doomsday-reactivation-safety-audit.md`](2026-11-25-doomsday-reactivation-safety-audit.md) |
| Status | **Prepared only.** Nothing in this document has been executed. No resource, credential, IAM binding, Rule, backup, workflow, monitor or email was created or changed while preparing it. |

Every command is labelled:

* **`[READ]`** — read-only inspection, safe to run at any time.
* **`[CHANGE]`** — creates, changes or deletes a production resource. Run only inside the step it belongs to.
* **`[GITHUB-UI]`** — a manual GitHub web-UI action.

---

## Part A — Verification of the audit against the code

Verified by reading `cb9c6a1`, materialising the candidate (`tools/materialize_worker_candidate.py`) and
running both test suites locally (2,144 and 2,180 passed; the 4+4 failures need full git history and pass in CI).

### A.1 Findings confirmed

| # | Audit claim | Code evidence |
|---|---|---|
| C1 | Legacy GitHub workflow is the only deployed engine; worker is repo-only | `bookmyshow-monitor.yml` unchanged; candidate lives in `worker_candidate/` and is invisible to imports/workflow discovery |
| C2 | Terraform is not production | **Stronger than stated:** `variables.tf` *rejects* any `project_id` not matching `^radar-staging-`. Production must be built with explicit `gcloud` (Part C). |
| C3 | Durable lease + epoch fencing | `overlay/monitor/execution.py`: `worker_runtime/control` {authority, epoch, holder, lease_until}, 3-min lease, every write calls `assert_lease` |
| C4 | One instance, no overlap | Cloud Run `max-instances=1`, in-process `busy` lock → second request gets `503 worker busy`, plus the lease |
| C5 | Durable outbox | `overlay/notifications/outbox.py`: `pending → sending → accepted / retry / failed / uncertain / cancelled`; `MAX_ATTEMPTS=3`; backoff 2ⁿ min; stable `Message-ID <item_id@ticket-radar.invalid>` |
| C6 | Legacy runner has no fencing | No reference to `worker_runtime` anywhere in the live `monitor/`, `config/`, `run_monitor.py` |
| C7 | *Extend by 24 hours* keeps the monitor ID | `app.py:893`, `monitor/state.py:1105` → `Monitor.extend()` sets `ACTIVE`, `monitor_until = max(old, now) + 24h` |
| C8 | Zero active monitors → no BookMyShow request | Same `run_once` path; only running monitors are loaded and grouped |
| C9 | Catalogue sync untouched | `catalogue-sync.yml` cron `0 1,7,13,19 * * *` unchanged; nothing in the candidate schedules a catalogue sync |
| C10 | BookMyShow collection behaviour preserved | Same `platforms/` code and same hash-locked `ci/requirements-worker.lock` (incl. `curl-cffi==0.17.0`) baked into the image |

### A.2 Incorrect assumptions and missing steps in the audit

| # | Issue | Consequence | Handled in |
|---|---|---|---|
| X1 | Audit said the app would "commit and dispatch" on Extend. **Wrong:** for signed-in users on Firestore nothing is committed to git (`app.py:529`); only a `workflow_dispatch` is attempted. | After the legacy workflow is disabled, Extend shows *"…monitoring is active again."* without "a check is running now". Harmless. | C-7 |
| X2 | **Missing — UX regression.** Creating a *new* monitor with "start now" calls the dispatch; when it fails the app sets `problem = FIRST_CHECK_NOT_STARTED` and shows a red **PROBLEM** banner (`app.py:546–553`). *Retry* fails the same way. The Cloud Run worker still checks the monitor within ≤1 min. | False alarm for any user creating a monitor after cutover, until the "Check now" relay is wired (Part E) or the app is changed. | C-5, E |
| X3 | **Missing IAM.** The worker calls Firebase Auth `accounts:lookup` every tick to re-verify owners (`monitor/policy.py`, patched `worker_resolver`). Without `roles/firebaseauth.viewer` it fails safe: **checks nothing and sends nothing, silently**. | Must grant. | C-2 |
| X4 | **Email-sink test is not possible against the real image.** `SMTP_HOST="smtp.gmail.com"`, `SMTP_PORT=465` are hard-coded (`notifications/email.py:31–32`). | Replaced by (a) a no-credential "sink" run that drives an outbox row to `failed` without sending, then (b) one owner-addressed real email. | C-6 |
| X5 | **Missing:** `epoch` must be a Firestore **integer** (`type(epoch) is not int` → `LeaseLost`). The Firebase console may store a typed `1` as a double. | Create the control document through the REST API with `integerValue`. | C-5 |
| X6 | **Missing dependency.** The fenced worker reads the catalogue from `https://raw.githubusercontent.com/manojanumolu/movie-ticket-radar/main/data/catalogue.json` (ETag, 30-s cache; `monitor/catalogue_refresh.py`). | The repo must stay **public** and catalogue sync must keep running on GitHub. If GitHub disables the repo, monitoring stops (fail-safe, no emails). Not a new scheduler. | Risk register |
| X7 | Audit asked for "one real BookMyShow read from Cloud Run", but **no endpoint does that**. Shadow mode is synthetic and never touches BookMyShow. | One-off Cloud Run **Job** from the same image calling `BookMyShowProvider().list_movies('hyderabad')`, the same call discovery makes every ~15 min. | C-4 |
| X8 | **The audit's four-theatre verification (reading `[checker] af1711d4 … 5 target(s)` from logs) does not work in fenced mode.** The fenced branch of `checker.run_once` prints no per-target lines. | Verify from `monitor_state/af1711d4…` in Firestore (C-7). | C-7 |
| X9 | **Ordering.** The audit disabled legacy just before Extend. Legacy must be disabled before **any** fenced run that touches real data, including the test monitors. Otherwise both engines check the same monitor and both can send. | Legacy disabled at C-5.1, before the control document exists. | C-5 |
| X10 | **Missed-alert semantics.** In fenced mode a change is marked notified in `monitor_execution.detection` when the outbox row is created. A row ending `failed` (3 SMTP refusals) or `cancelled` (older than 20 min, or target no longer AVAILABLE) is **not regenerated** until the next availability transition. | At-most-once per transition: duplicates are avoided, but an outage or SMTP failure longer than 20 min can lose an alert. Alerting on `failed/cancelled/uncertain` rows is required (C-8). | C-8 |
| X11 | `gcloud scheduler jobs create` has no create-paused option. | Create the job while the service is in **shadow** mode (synthetic, no data), then pause. | C-3 |
| X12 | The "Check now" relay (`overlay/.github/workflows/monitor-worker-relay.yml`) is `workflow_call` only. Wiring it requires editing `bookmyshow-monitor.yml`, Workload Identity Federation, a `monitor-dispatch` environment and repo variables. | Not needed for monitoring to work; deferred to Part E. | E |
| X13 | Correction: the BookMyShow client is the TLS-impersonating `curl-cffi` session (`platforms/http.py`), not plain HTTP. | No technical change. Relevant to the provider-authorization question: it is designed to pass bot checks. | — |
| X14 | Cloud Run IPs differ from GitHub runners; BookMyShow may treat them differently. | C-4 is a go/no-go gate. | C-4 |

**Verdict on the candidate:** fit for production with the steps below. **No repository code change is required** for
GREEN. X2 is a known, cosmetic regression with a mitigation.

---

## Part B — Target design

```
Cloud Scheduler "radar-worker-tick"  (* * * * *, UTC, OIDC as radar-monitor-invoker, 0 retries)
        │ POST /tick  (Authorization: Bearer <ID token, aud = service URL>)
        ▼
Cloud Run "radar-monitor-worker"   private (no allUsers), ingress all + IAM, max-instances 1,
        │                          concurrency 4 (busy-lock), timeout 55 s, image pinned by digest
        │ attached identity radar-monitor-worker@  (ADC, no key)
        ├── Firestore (default)  — monitors, monitor_state, history, monitor_execution,
        │                         monitor_outbox, worker_runtime/{control,discovery}
        ├── Firebase Auth accounts:lookup (owner verification)
        ├── BookMyShow (unchanged checker, curl-cffi)
        ├── raw.githubusercontent.com …/data/catalogue.json (read)
        └── smtp.gmail.com:465 — GMAIL_ADDRESS / GMAIL_APP_PASSWORD from Secret Manager

GitHub Actions: catalogue-sync.yml unchanged (6-hourly). bookmyshow-monitor.yml DISABLED.
Streamlit app unchanged.
```

Each monitor is still checked only when its own `interval_minutes` is due. A tick runs at most 2 slots (0 s and ~30 s)
within a 45-s budget.

### Identities and least-privilege IAM

| Principal | Role | Scope | Why |
|---|---|---|---|
| `radar-monitor-worker@ticketradar03.iam.gserviceaccount.com` | `roles/datastore.user` | project | Firestore read/write (IAM bypasses Rules) |
| same | `roles/firebaseauth.viewer` | project | `accounts:lookup` owner verification (X3) |
| same | `roles/secretmanager.secretAccessor` | **each Gmail secret only** | sender credentials |
| `radar-monitor-invoker@…` | `roles/run.invoker` | **service `radar-monitor-worker` only** | Scheduler OIDC calls |
| Cloud Scheduler service agent `service-<PROJECT_NUMBER>@gcp-sa-cloudscheduler.iam.gserviceaccount.com` | `roles/iam.serviceAccountTokenCreator` | **on `radar-monitor-invoker` only** | mint OIDC tokens (often implicit; made explicit) |
| `radar-bms-probe@…` | *(none)* | — | connectivity Job runs with no data access |
| Operator (you) | Owner, or: `run.admin`, `iam.serviceAccountAdmin`, `iam.serviceAccountUser`, `artifactregistry.admin`, `cloudscheduler.admin`, `secretmanager.admin`, `datastore.importExportAdmin`, `storage.admin`, `resourcemanager.projectIamAdmin` | project | performing this runbook |

No service-account key is created. `FIREBASE_SERVICE_ACCOUNT` is never given to Cloud Run. Fenced mode refuses
`FIREBASE_CREDENTIAL_MODE=legacy` at start-up.

---

## Part C — Runbook

Run from a workstation with `gcloud` (≥ 480), `git`, `python3`, `jq`, `curl`, and either Docker or permission to
use Cloud Build. Use `bash`.

### C-0 Variables `[READ]`

```bash
export PROJECT=ticketradar03
export REGION=asia-south1          # confirm against Firestore location in C-1.2
export SERVICE=radar-monitor-worker
export AR_REPO=radar
export WORKER_SA=radar-monitor-worker@${PROJECT}.iam.gserviceaccount.com
export INVOKER_SA=radar-monitor-invoker@${PROJECT}.iam.gserviceaccount.com
export PROBE_SA=radar-bms-probe@${PROJECT}.iam.gserviceaccount.com
export AUTHORITY=ticketradar03-cloudrun-v1      # [A-Za-z0-9_-]{1,128}
export BACKUP_BUCKET=gs://${PROJECT}-premigration-20261125
export FS="https://firestore.googleapis.com/v1/projects/${PROJECT}/databases/(default)/documents"
gcloud config set project "$PROJECT"
```

### C-1 Pre-flight inspection — all `[READ]` (≈ 30 min)

```bash
# 1. Identity and project
gcloud auth list
gcloud projects describe "$PROJECT" --format='value(projectNumber,lifecycleState)'
export PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')

# 2. Firestore database, location, PITR, existing backups/schedules
gcloud firestore databases describe --database='(default)' \
  --format='yaml(locationId,type,pointInTimeRecoveryEnablement,deleteProtectionState)'
gcloud firestore backups schedules list --database='(default)'
gcloud firestore backups list --format='table(name,state,snapshotTime)'

# 3. Deployed Firestore Rules vs repository
TOKEN=$(gcloud auth print-access-token)
RULES_API=https://firebaserules.googleapis.com/v1
curl -s -H "Authorization: Bearer $TOKEN" "$RULES_API/projects/$PROJECT/releases" | jq -r '.releases[] | [.name,.rulesetName,.updateTime] | @tsv'
RULESET=$(curl -s -H "Authorization: Bearer $TOKEN" "$RULES_API/projects/$PROJECT/releases/cloud.firestore" | jq -r .rulesetName)
curl -s -H "Authorization: Bearer $TOKEN" "$RULES_API/$RULESET" | jq -r '.source.files[0].content' > /tmp/deployed.rules
git -C /path/to/movie-ticket-radar show cb9c6a1:firestore.rules | diff - /tmp/deployed.rules && echo RULES_MATCH

# 4. IAM: who can do what today; existing keys on every SA
gcloud projects get-iam-policy "$PROJECT" --format='table(bindings.role,bindings.members)' --flatten=bindings
for sa in $(gcloud iam service-accounts list --format='value(email)'); do
  echo "== $sa"; gcloud iam service-accounts keys list --iam-account "$sa" --managed-by=user \
    --format='table(name.basename(),validAfterTime)'; done

# 5. Nothing already deployed / enabled
gcloud services list --enabled --format='value(config.name)' | grep -E 'run|cloudscheduler|artifactregistry|secretmanager|cloudbuild' || true
gcloud run services list --region "$REGION" 2>/dev/null
gcloud scheduler jobs list --location "$REGION" 2>/dev/null

# 6. The two Doomsday monitors (fields only, no addresses printed)
TOKEN=$(gcloud auth print-access-token)
curl -s -H "Authorization: Bearer $TOKEN" \
 "$FS/monitors?pageSize=300&mask.fieldPaths=status&mask.fieldPaths=monitor_until&mask.fieldPaths=interval_minutes&mask.fieldPaths=targets&mask.fieldPaths=owner_uid&mask.fieldPaths=stopped_reason" \
 | jq '[.documents[] | select(.name|test("/monitors/(af1711d4|6150dc3b)"))
       | {id:(.name|split("/")[-1]), status:.fields.status.stringValue,
          until:.fields.monitor_until.stringValue, interval:.fields.interval_minutes.integerValue,
          stopped:.fields.stopped_reason.stringValue,
          owner_prefix:(.fields.owner_uid.stringValue[0:6]),
          targets:[.fields.targets.arrayValue.values[]?.mapValue.fields | {name:.name.stringValue, fmt:.fmt.stringValue}]}]'
# Export the full IDs for later steps:
export DOOMSDAY_ID=<full af1711d4… id from output>
export OVERLAP_ID=<full 6150dc3b… id from output>

# 7. Running monitors right now (expect 0) and prior state of the Doomsday monitor
curl -s -H "Authorization: Bearer $TOKEN" -X POST "$FS:runQuery" -H 'Content-Type: application/json' \
 -d '{"structuredQuery":{"from":[{"collectionId":"monitors"}],"where":{"fieldFilter":{"field":{"fieldPath":"status"},"op":"EQUAL","value":{"stringValue":"ACTIVE"}}},"select":{"fields":[{"fieldPath":"monitor_until"}]}}}' \
 | jq '[.[] | select(.document)] | length'
curl -s -H "Authorization: Bearer $TOKEN" "$FS/monitor_state/$DOOMSDAY_ID" \
 | jq '.fields.targets.mapValue.fields | to_entries[] | {key, availability:.value.mapValue.fields.availability.stringValue, notified:.value.mapValue.fields.notified_availability.stringValue, last:.value.mapValue.fields.last_check_at.stringValue}'

# 8. Worker-only collections must not already exist
for p in worker_runtime/control monitor_outbox monitor_execution; do
  curl -s -o /dev/null -w "$p %{http_code}\n" -H "Authorization: Bearer $TOKEN" "$FS/$p?pageSize=1"; done

# 9. History for the five AMB alerts (resolves the audit's open question)
curl -s -H "Authorization: Bearer $TOKEN" -X POST "$FS:runQuery" -H 'Content-Type: application/json' \
 -d "{\"structuredQuery\":{\"from\":[{\"collectionId\":\"history\"}],\"where\":{\"fieldFilter\":{\"field\":{\"fieldPath\":\"monitor_id\"},\"op\":\"EQUAL\",\"value\":{\"stringValue\":\"$DOOMSDAY_ID\"}}}}}" \
 | jq -r '.[] | .document.fields | select(.message.stringValue|test("AMB")) | [.at.stringValue,.kind.stringValue,.message.stringValue] | @tsv'

# 10. GitHub: no legacy run in flight
gh run list --repo manojanumolu/movie-ticket-radar --workflow bookmyshow-monitor.yml --status in_progress
gh run list --repo manojanumolu/movie-ticket-radar --workflow bookmyshow-monitor.yml --status queued
```

**Go / no-go:**
* `af1711d4` exists and is `STOPPED` (or `EXPIRED`) with the 5 targets: ALLU·Dolby Cinema, AMB·HDR By Barco,
  PVR Lakeshore·PXL, Prasads·PCX, Prasads·HDR By Barco.
* `6150dc3b` is not running.
* Active count is known (expected 0).
* `RULES_MATCH`.
* `worker_runtime/control` returns 404.
* Choose `REGION` = the Firestore location's region (Firestore `asia-south1` → keep; `nam5`/`eur3` → still fine, but latency is higher).

### C-2 Backups, then foundations (≈ 45 min)

**C-2.1 Immediate backup `[CHANGE]`**

```bash
gcloud storage buckets create "$BACKUP_BUCKET" --location="$REGION" --uniform-bucket-level-access \
  --public-access-prevention
gcloud firestore export "$BACKUP_BUCKET/firestore" --database='(default)' --async
gcloud firestore operations list --format='table(name.basename(),metadata.operationState,metadata.endTime)'   # [READ] until SUCCESSFUL
gcloud firestore databases update --database='(default)' --enable-pitr                      # 7-day point-in-time recovery
gcloud firestore backups schedules create --database='(default)' --recurrence=daily --retention=14d
```

**C-2.2 Auth export `[READ on Auth, CHANGE on local disk]`** — the file contains password hashes; treat it as a secret.

```bash
npx -y firebase-tools@13 login
npx -y firebase-tools@13 auth:export auth-ticketradar03-20261125.json --format=json --project "$PROJECT"
jq '.users | length' auth-ticketradar03-20261125.json          # compare with console user count
gcloud storage cp auth-ticketradar03-20261125.json "$BACKUP_BUCKET/auth/"  && shred -u auth-ticketradar03-20261125.json
```

Also record the password-hash parameters: Firebase console → Authentication → Users → ⋮ → *Password hash parameters*.
Store them privately with the export. They are needed for any import.

**C-2.3 Enable APIs `[CHANGE]`**

```bash
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com cloudbuild.googleapis.com iamcredentials.googleapis.com
```

**C-2.4 Service accounts and IAM `[CHANGE]`** — no keys.

```bash
gcloud iam service-accounts create radar-monitor-worker  --display-name="Radar monitor worker (Cloud Run)"
gcloud iam service-accounts create radar-monitor-invoker --display-name="Radar monitor scheduler invoker"
gcloud iam service-accounts create radar-bms-probe       --display-name="Radar BookMyShow probe (no data access)"
# The worker's DATA roles are granted later (C-5.3), not now: shadow mode needs none.
gcloud iam service-accounts add-iam-policy-binding "$INVOKER_SA" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountTokenCreator
gcloud artifacts repositories create "$AR_REPO" --repository-format=docker --location="$REGION" \
  --description="TicketRadar monitor worker images"
```

### C-3 Build, deploy shadow, test, then pause scheduler (≈ 60 min)

**C-3.1 Materialise exactly `cb9c6a1` `[READ]`** (local only)

```bash
git clone https://github.com/manojanumolu/movie-ticket-radar.git radar-src && cd radar-src
git checkout --detach cb9c6a130b1b3f321435c817fabb133d3742f27b
python3 tools/materialize_worker_candidate.py ../radar-worker-cb9c6a1
cd ../radar-worker-cb9c6a1 && git -C ../radar-src log -1 --format=%H
```

**C-3.2 Build and push `[CHANGE]`** — choose one path.

```bash
export IMAGE=${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/worker:cb9c6a1
# (a) Local Docker
gcloud auth configure-docker ${REGION}-docker.pkg.dev
docker build --file Dockerfile.worker --tag "$IMAGE" . && docker push "$IMAGE"
# (b) Cloud Build (config kept outside the source tree)
cat > ../cloudbuild.worker.yaml <<'EOF'
steps:
- name: gcr.io/cloud-builders/docker
  args: ["build", "--file", "Dockerfile.worker", "--tag", "${_IMAGE}", "."]
images: ["${_IMAGE}"]
EOF
gcloud builds submit . --config=../cloudbuild.worker.yaml --substitutions=_IMAGE="$IMAGE"

export IMAGE_DIGEST=${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/worker@$(gcloud artifacts docker images describe "$IMAGE" --format='value(image_summary.digest)')
echo "$IMAGE_DIGEST"     # record it: every deploy below uses the digest, never the tag
```

**C-3.3 Deploy in shadow mode `[CHANGE]`** — synthetic data only, no data roles, no secrets.

```bash
gcloud run deploy "$SERVICE" --image="$IMAGE_DIGEST" --region="$REGION" \
  --service-account="$WORKER_SA" --no-allow-unauthenticated --ingress=all \
  --max-instances=1 --min-instances=0 --concurrency=4 --timeout=55 --cpu=1 --memory=512Mi \
  --set-env-vars=WORKER_MODE=shadow --revision-suffix=shadow-cb9c6a1
export URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')
gcloud run services add-iam-policy-binding "$SERVICE" --region="$REGION" \
  --member="serviceAccount:$INVOKER_SA" --role=roles/run.invoker
gcloud run services get-iam-policy "$SERVICE" --region="$REGION"    # [READ] must NOT contain allUsers/allAuthenticatedUsers
```

**C-3.4 Shadow tests `[READ]`** (synthetic; invoke the private service as yourself)

```bash
ID=$(gcloud auth print-identity-token)
curl -s -o /dev/null -w 'unauth=%{http_code}\n' "$URL/healthz"                                    # expect 403
curl -s -H "Authorization: Bearer $ID" "$URL/healthz"                                             # {"ready": true}
curl -s -H "Authorization: Bearer $ID" -H 'Content-Type: application/json' \
  --data '{"monitor_id":"fixture-monitor","force":true}' "$URL/check"                             # {"slots": [{"checked": 1, "changes": 0}]}
curl -s -H "Authorization: Bearer $ID" -X POST "$URL/tick"                                        # 200, 1–2 slots
curl -s -o /dev/null -w 'bad-body=%{http_code}\n' -H "Authorization: Bearer $ID" \
  -H 'Content-Type: application/json' --data '{"x":1}' "$URL/tick"                                # expect 400
( curl -s -o /dev/null -w 'p1=%{http_code}\n' -H "Authorization: Bearer $ID" -X POST "$URL/tick" &
  curl -s -o /dev/null -w 'p2=%{http_code}\n' -H "Authorization: Bearer $ID" -X POST "$URL/tick" & wait )  # one 200, one 503 (busy)
```

**C-3.5 Create the scheduler against shadow, observe, pause `[CHANGE]`**

```bash
gcloud scheduler jobs create http radar-worker-tick --location="$REGION" \
  --schedule='* * * * *' --time-zone='Etc/UTC' --uri="$URL/tick" --http-method=POST \
  --oidc-service-account-email="$INVOKER_SA" --oidc-token-audience="$URL" \
  --attempt-deadline=60s --max-retry-attempts=0
# watch 5 minutes   [READ]
gcloud logging read "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND jsonPayload.worker_result:*" \
  --freshness=6m --format='value(timestamp,jsonPayload.worker_result)' | head
gcloud logging read "resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"radar-worker-tick\"" \
  --freshness=6m --format='value(timestamp,jsonPayload.status,httpRequest.status)' | head
gcloud scheduler jobs pause radar-worker-tick --location="$REGION"                                 # [CHANGE]
gcloud scheduler jobs describe radar-worker-tick --location="$REGION" --format='value(state)'      # PAUSED
```

Pass criteria: ≥4 consecutive `complete` results, Scheduler HTTP 200, no 401/403.

### C-4 BookMyShow connectivity from Cloud Run — go/no-go (≈ 15 min)

One call, the same `list_movies` the worker's discovery already makes every ~15 min. The quickbook strategy is tried
first; the fallbacks only if it fails. No Firestore, no secrets, no email. **`[CHANGE]`** (temporary Job)

```bash
gcloud run jobs create radar-bms-probe --region="$REGION" --image="$IMAGE_DIGEST" \
  --service-account="$PROBE_SA" --max-retries=0 --task-timeout=120s --command=python \
  --args='^@^-c@from platforms.bookmyshow import BookMyShowProvider as P; m=P().list_movies("hyderabad"); print("BMS_OK", len(m))'
gcloud run jobs execute radar-bms-probe --region="$REGION" --wait
gcloud logging read "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"radar-bms-probe\"" \
  --freshness=10m --format='value(textPayload)' | grep -E 'BMS_OK|catalogue via|Could not|refused|403|429'   # [READ]
gcloud run jobs delete radar-bms-probe --region="$REGION" --quiet
```

* **GO:** `[bookmyshow] catalogue via 'quickbook': N movie(s)` and `BMS_OK N` with N ≈ 45–55.
* **NO-GO:** `PlatformBlocked`/403. Stop here. Keep the scheduler paused and the legacy workflow as is (idle). The fix
  (egress through a different network path) is a separate decision. **Do not retry in a loop.**

### C-5 Cutover — legacy off, worker fenced (≈ 30 min)

**C-5.1 Disable legacy monitor `[GITHUB-UI]`**

1. github.com/manojanumolu/movie-ticket-radar → **Actions** → left sidebar **BookMyShow monitor** → **⋯** (top right) → **Disable workflow**.
2. In the same list confirm **Catalogue sync** still shows as enabled. Do **not** touch it.
3. `[READ]` confirm:
   ```bash
   gh api repos/manojanumolu/movie-ticket-radar/actions/workflows/bookmyshow-monitor.yml --jq .state   # disabled_manually
   gh api repos/manojanumolu/movie-ticket-radar/actions/workflows/catalogue-sync.yml   --jq .state     # active
   gh run list --repo manojanumolu/movie-ticket-radar --workflow bookmyshow-monitor.yml --status in_progress  # empty
   ```
   A disabled workflow cannot be started by schedule, push or `workflow_dispatch`. Do not use *Re-run* on any old monitor run from now on.

**C-5.2 Gmail secrets `[CHANGE]`** — use the same Gmail sender as today (same From address users already know).

```bash
printf '%s' 'sender@gmail.com'         | gcloud secrets create radar-gmail-address --data-file=- --replication-policy=automatic
printf '%s' 'xxxx xxxx xxxx xxxx'      | gcloud secrets create radar-gmail-app-password --data-file=- --replication-policy=automatic
for s in radar-gmail-address radar-gmail-app-password; do
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$WORKER_SA" --role=roles/secretmanager.secretAccessor; done
```

(Type the values at the prompt, not into shell history. Prefix the commands with a space if `HISTCONTROL=ignorespace`.)

**C-5.3 Worker data roles `[CHANGE]`**

```bash
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$WORKER_SA" --role=roles/datastore.user --condition=None
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$WORKER_SA" --role=roles/firebaseauth.viewer --condition=None
```

**C-5.4 Control document — integer epoch `[CHANGE]`** (create-only; fails if it already exists)

```bash
TOKEN=$(gcloud auth print-access-token)
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
 "$FS/worker_runtime/control?currentDocument.exists=false" \
 -d "{\"fields\":{\"authority\":{\"stringValue\":\"$AUTHORITY\"},\"epoch\":{\"integerValue\":\"1\"},\"holder\":{\"stringValue\":\"\"},\"lease_until\":{\"stringValue\":\"\"}}}" | jq .
curl -s -H "Authorization: Bearer $TOKEN" "$FS/worker_runtime/control" | jq .fields     # [READ] epoch must show integerValue
```

**C-5.5 Fenced revision WITHOUT mail credentials ("sink" mode) `[CHANGE]`**

```bash
gcloud run deploy "$SERVICE" --image="$IMAGE_DIGEST" --region="$REGION" --service-account="$WORKER_SA" \
  --no-allow-unauthenticated --ingress=all --max-instances=1 --min-instances=0 --concurrency=4 --timeout=55 \
  --cpu=1 --memory=512Mi --revision-suffix=fenced-sink-cb9c6a1 \
  --set-env-vars=WORKER_MODE=fenced,FIREBASE_CREDENTIAL_MODE=adc,FIREBASE_PROJECT_ID=$PROJECT,FIREBASE_EXPECTED_PROJECT=$PROJECT,WORKER_AUTHORITY=$AUTHORITY,TZ=Asia/Kolkata
```

If the revision fails to start, read the logs. `fenced mode requires workload identity and a named authority` means
an environment variable is wrong; a credential error means the attached-identity or project pin is wrong. Fix and redeploy.

### C-6 Email tests and sender isolation (≈ 40 min)

**C-6.1 Sink test — proves detection → outbox → sender path with no email possible**

1. In the app, signed in as the owner (admin), create **Test-A**: a film playing today at one Hyderabad theatre that is
   currently bookable, *Any format*, end time today. Expect the red PROBLEM banner (X2). That is expected.
2. `[CHANGE]` Trigger one tick yourself (scheduler stays paused):
   `curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" -X POST "$URL/tick"`
3. `[READ]` Within 1 min:
   ```bash
   TOKEN=$(gcloud auth print-access-token)
   curl -s -H "Authorization: Bearer $TOKEN" -X POST "$FS:runQuery" -H 'Content-Type: application/json' \
    -d '{"structuredQuery":{"from":[{"collectionId":"monitor_outbox"}],"select":{"fields":[{"fieldPath":"monitor_id"},{"fieldPath":"status"},{"fieldPath":"attempts"},{"fieldPath":"subject"}]}}}' \
    | jq -r '.[] | .document.fields | [.monitor_id.stringValue[0:8], .status.stringValue, .attempts.integerValue, .subject.stringValue] | @tsv'
   ```
   Expect exactly **one** row for Test-A with status **`failed`**, attempts `1` (sender unavailable → non-retryable).
   `monitor_state/<Test-A>` shows the target `AVAILABLE`. No email arrives.
4. Delete Test-A in the app.

**C-6.2 Production sender isolation checklist `[READ]`**

* GitHub monitor workflow: `disabled_manually` (C-5.1).
* Only the Cloud Run revision will mount `radar-gmail-*`:
  `gcloud secrets get-iam-policy radar-gmail-app-password` lists only `$WORKER_SA`.
* The Streamlit app's Gmail credentials only serve the user-initiated *test email* button and account mail (`send_email`). They never send monitor alerts.
* Defer, after 7 stable days (credential change, needs separate approval): delete `GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD`
  from the GitHub `production` environment. GitHub → Settings → Environments → production → Secrets.

**C-6.3 Mount secrets — real sender `[CHANGE]`**

```bash
gcloud run services update "$SERVICE" --region="$REGION" --revision-suffix=fenced-cb9c6a1 \
  --set-secrets=GMAIL_ADDRESS=radar-gmail-address:1,GMAIL_APP_PASSWORD=radar-gmail-app-password:1
```

**C-6.4 One real end-to-end email (owner's own inbox only) `[CHANGE]`**

1. Create **Test-B** exactly like Test-A.
2. Trigger one tick: `curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" -X POST "$URL/tick"`.
3. Run the outbox query: one Test-B row → **`accepted`**, attempts `1`.
4. Inbox: one message from the usual sender, header `Message-ID: <…@ticket-radar.invalid>`. Check spam too.
   `accepted` = SMTP acceptance only; this inbox check is the delivery proof.
5. Run one more tick: **no second email**, and no new outbox row for Test-B.
6. App → Test-B history shows the alert. Delete Test-B.

### C-7 Scheduler on, reactivate `af1711d4`, verify four theatres (≈ 20 min)

```bash
gcloud scheduler jobs resume radar-worker-tick --location="$REGION"     # [CHANGE]
```

1. `[CHANGE]` App, signed in as the **owner of `af1711d4`**: Monitors → Finished → *Avengers: Doomsday* → **Extend by 24 hours**.
   Expected message: "Extended by 24 hours — monitoring is active again." **Do not** extend `6150dc3b…`.
2. `[READ]` Within its interval (≤ 5 min plus ≤ 1 min tick), verify:
   ```bash
   TOKEN=$(gcloud auth print-access-token)
   curl -s -H "Authorization: Bearer $TOKEN" "$FS/monitors/$DOOMSDAY_ID" \
     | jq '{status:.fields.status.stringValue, until:.fields.monitor_until.stringValue, n_targets:(.fields.targets.arrayValue.values|length)}'
   curl -s -H "Authorization: Bearer $TOKEN" "$FS/monitor_state/$DOOMSDAY_ID" \
     | jq -r '.fields.targets.mapValue.fields | to_entries[] | [.key, .value.mapValue.fields.availability.stringValue, .value.mapValue.fields.last_check_at.stringValue // "-"] | @tsv'
   curl -s -H "Authorization: Bearer $TOKEN" "$FS/monitors/$OVERLAP_ID" | jq -r .fields.status.stringValue   # still STOPPED
   ```
   **Pass:** status `ACTIVE`, same document ID, `n_targets = 5`, and all **5** target keys show a fresh `last_check_at`
   after the Extend, with plausible availability. As of 25 Nov: ALLU·Dolby `SHOW_NOT_AVAILABLE`, AMB·HDR By Barco
   `AVAILABLE`, PVR Lakeshore·PXL `SOLD_OUT`, Prasads·PCX / HDR By Barco `SHOW_NOT_AVAILABLE`.
3. AMB was already notified as AVAILABLE by the legacy worker (`notified_availability`). The worker starts from the stored
   state, so **no repeat email is expected** for that showtime. A new email arrives only on a genuine new showtime or transition.
4. App card for `af1711d4` shows a recent "last checked" time.

**Extension cadence:** *Extend* adds 24 h per press (policy cap 30 days). Extend daily, or until the 17 Dec show.

### C-8 First-hour watch and standing checks `[READ]`

```bash
# Every result in the last hour (expect `complete` once per minute; occasional `failed` LeaseLost after a crash is self-healing ≤3 min)
gcloud logging read "resource.labels.service_name=\"$SERVICE\" AND jsonPayload.worker_result:*" --freshness=60m \
  --format='value(timestamp,jsonPayload.worker_result,jsonPayload.kind)' | sort | uniq -c | tail
# Scheduler failures
gcloud logging read "resource.type=\"cloud_scheduler_job\" AND severity>=ERROR" --freshness=60m --limit=20
# Outbox rows needing attention (X10): failed / cancelled / uncertain / stuck sending
TOKEN=$(gcloud auth print-access-token)
curl -s -H "Authorization: Bearer $TOKEN" -X POST "$FS:runQuery" -H 'Content-Type: application/json' \
 -d '{"structuredQuery":{"from":[{"collectionId":"monitor_outbox"}],"where":{"fieldFilter":{"field":{"fieldPath":"status"},"op":"IN","value":{"arrayValue":{"values":[{"stringValue":"failed"},{"stringValue":"cancelled"},{"stringValue":"uncertain"},{"stringValue":"sending"}]}}}}}}' \
 | jq -r '.[] | .document.fields | [.monitor_id.stringValue[0:8], .status.stringValue, .created_at.stringValue, .subject.stringValue] | @tsv'
# BookMyShow refusals
gcloud logging read "resource.labels.service_name=\"$SERVICE\" AND (textPayload:\"403\" OR textPayload:\"429\" OR textPayload:\"refused\")" --freshness=60m --limit=20
```

Recommended alert, `[CHANGE]`, optional same day: a log-based metric on `jsonPayload.worker_result="failed"`, with an
email notification channel. The worker must also not be silent for >10 min: alert on the absence of
`worker_result="complete"`.

**Interpreting an `uncertain` row:** SMTP may have accepted it. Check the sender's Gmail *Sent* folder for the
`Message-ID <item_id@ticket-radar.invalid>` before any manual resend. Never resend blindly.

---

## Part D — Rollback

| Situation | Action | Effect |
|---|---|---|
| Stop all checks now | `gcloud scheduler jobs pause radar-worker-tick --location=$REGION` `[CHANGE]` | No new ticks within ≤1 min |
| Fence an in-flight request | Bump the epoch `[CHANGE]` (below) | Every subsequent write of the old holder raises `LeaseLost` |
| Hard kill switch | Set `authority` to `disabled-<date>` `[CHANGE]` | Every invocation refuses at `__enter__` |
| Bad image | `gcloud run services update-traffic $SERVICE --region=$REGION --to-revisions=<previous-revision>=100` `[CHANGE]` | Instant revert to a known-good revision |
| Stop sending only | `gcloud run services update $SERVICE --region=$REGION --remove-secrets=GMAIL_ADDRESS,GMAIL_APP_PASSWORD` `[CHANGE]` | Checks continue; new rows end `failed` without sending (see X10) |
| Stop one monitor | App → *Stop* | Status `STOPPED`, ID and history kept |
| Data damage | `gcloud firestore import $BACKUP_BUCKET/firestore` **into a new database first**, or PITR read; production restore needs separate approval | — |

```bash
TOKEN=$(gcloud auth print-access-token)
# bump epoch (2 → 3 … each time); preserves authority, clears holder/lease
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
 "$FS/worker_runtime/control?updateMask.fieldPaths=epoch&updateMask.fieldPaths=holder&updateMask.fieldPaths=lease_until" \
 -d '{"fields":{"epoch":{"integerValue":"2"},"holder":{"stringValue":""},"lease_until":{"stringValue":""}}}'
# kill switch
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
 "$FS/worker_runtime/control?updateMask.fieldPaths=authority" -d '{"fields":{"authority":{"stringValue":"disabled-20261125"}}}'
```

**Re-enabling the GitHub monitor workflow is not a rollback path.** It recreates the enforcement risk, and the legacy
runner ignores the fence. If it is ever chosen, first pause the scheduler, set the kill switch, reconcile all
`sending/uncertain` rows, and record the decision.

Rollback of the repository is not needed: nothing in this runbook changes repository code.

---

## Part E — Follow-ups (not required for GREEN)

1. **"Check now" relay (fixes X2).** Copy `worker_candidate/overlay/.github/workflows/monitor-worker-relay.yml` into
   `.github/workflows/`. Replace the job of `bookmyshow-monitor.yml` (keep the file name: the app dispatches it by name)
   with a `uses:` call of the relay for targeted dispatches. Remove its `schedule`, `push`, `contents: write` and
   `actions: write`. Create a Workload Identity pool/provider pinned to the repository ID, `refs/heads/main` and
   environment `monitor-dispatch`; a `radar-relay-invoker` SA with `run.invoker` on the service; and repo variables
   `RADAR_ENABLE_MONITOR_RELAY`, `RADAR_WORKER_SERVICE_URL`, `RADAR_RELAY_WIF_PROVIDER`, `RADAR_RELAY_INVOKER`.
   Separate PR + review. ≈ 2 h.
2. Retire the long-lived `FIREBASE_SERVICE_ACCOUNT` key after moving `rules-audit` and `grant-admin` to federated
   identities (their code already supports `federated`). Credential change, separate approval.
3. Branch protection on `main` requiring `pytest`, `candidate-pytest`, `firebase-emulators`, `worker-image`.
4. Pin `python:3.12-slim` in `Dockerfile.worker` by digest.
5. Provider authorization: written request to BookMyShow (contractual track, independent of this migration).

---

## Part F — Risk register after migration

| Risk | Level | Mitigation |
|---|---|---|
| GitHub enforcement from monitoring | **Removed** — no monitor runner time on Actions | Workflow disabled |
| Catalogue sync on Actions (unchanged by requirement) | Low | ≈12 min/day; untouched |
| Repo disabled/private → worker cannot read catalogue (X6) | Low/Medium | Fail-safe: no checks, no emails. Re-point `catalogue_refresh.BASE` later if needed. |
| Missed alert after SMTP failure or an outage >20 min (X10) | Low | C-8 outbox query, alerting |
| Duplicate alerts | Very low | Lease + fence + outbox + single sender |
| BookMyShow blocks Cloud Run egress | Unknown until C-4 | Go/no-go gate |
| False PROBLEM banner on new monitors (X2) | Cosmetic | Tell users, or do Part E.1 |
| Cost | ≈ US$1–5/month (estimate) | 1,440 short requests/day; Scheduler free tier; backup storage |

---

## Part G — Manual steps and time estimate

| Step | What you do | Type | Time |
|---|---|---|---|
| C-1 | Pre-flight reads; confirm `af1711d4` and its 5 targets, Rules match, 0 running | READ | 30 min |
| C-2 | Firestore export + PITR + daily schedule; Auth export; APIs; 3 SAs; Artifact Registry | CHANGE | 45 min (export completion may take longer) |
| C-3 | Materialise `cb9c6a1`, build, push, deploy shadow, 6 shadow tests, scheduler created → observed → paused | CHANGE | 60 min |
| C-4 | BookMyShow probe Job — **go/no-go** | CHANGE (temporary) | 15 min |
| C-5 | **GitHub UI: disable "BookMyShow monitor"**; Gmail secrets; worker data roles; control doc; fenced sink revision | GITHUB-UI + CHANGE | 30 min |
| C-6 | Sink test (Test-A → `failed`), mount secrets, real test (Test-B → `accepted` + inbox, no duplicate) | CHANGE | 40 min |
| C-7 | Resume scheduler; **Extend `af1711d4`**; verify 5 targets in `monitor_state`; confirm `6150dc3b` stopped | CHANGE + READ | 20 min |
| C-8 | First-hour watch; optional alert policy | READ (+CHANGE) | 60 min elapsed, ~15 min active |

**Realistic total: 4–5 hours of hands-on work, about 5½–6½ hours elapsed** including waits for the export, build
and first-hour watch. Add ~1 hour if this is your first time with Artifact Registry/Cloud Build permissions in this
project. Today is achievable if started within the next few hours; C-4 is the only external unknown.

**Hard stop points:** C-1 go/no-go, C-3 pass criteria, C-4 BookMyShow result, C-6.1 must end `failed` with no email,
C-6.4 must produce exactly one email.
