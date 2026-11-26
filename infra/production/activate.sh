#!/usr/bin/env bash
# TicketRadar — interactive, resumable production activation (Cloud Shell).
#
#   bash infra/production/activate.sh            run the next unfinished phases, in order
#   bash infra/production/activate.sh status     show progress and saved values
#   bash infra/production/activate.sh rollback pause|resume|nosend|kill|unkill|epoch
#
# Every phase verifies its own gate and STOPS (exit 1) on any failure; nothing
# after a failed gate runs. Sensitive or hard-to-reverse actions wait for you to
# type a confirmation word. Manual browser steps (GitHub settings, the app) are
# requested explicitly and then verified.
#
# Credentials: none are stored here. You type the Gmail address and app
# password into hidden prompts; they go straight to Secret Manager.
#
# The script never: writes to the monitors/history/users collections, extends
# or stops a monitor, edits a workflow, touches the catalogue sync, creates a
# service-account key, or makes the Cloud Run service public.
set -Eeuo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
# shellcheck source=./env.sh
source "$HERE/env.sh"
STATE_DIR=${RADAR_STATE_DIR:-$HOME/.radar-activation}
STATE=$STATE_DIR/state
GH_API=https://api.github.com/repos/$GITHUB_REPO
BASELINE=cb9c6a130b1b3f321435c817fabb133d3742f27b
ORIGINAL_PREFIX=af1711d4
OVERLAP_PREFIX=6150dc3b
EXPECTED_TARGETS=$'ALLU Cinemas · Dolby Cinema\nAMB Cinemas · HDR By Barco\nPVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction · PXL\nPrasads Multiplex · HDR By Barco\nPrasads Multiplex · PCX'
CI_JOBS=$'candidate-pytest\nfirebase-emulators\npytest\nworker-image'
PHASES=(preflight dr_isolation backups identities build shadow probe legacy fenced alerts notify activate watch)
ACCESS_PERMS=storage.objects.get,storage.objects.list,storage.objects.create,storage.objects.delete,storage.buckets.setIamPolicy

# ── output and control ─────────────────────────────────────────────────────
say()  { printf '%s\n' "$*"; }
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die()  {  # die GATE "what failed" "what stays paused"
  printf '\n\033[31mSTOP [%s]\033[0m %s\n' "$1" "$2" >&2
  [ -n "${3:-}" ] && printf '  Stays as is: %s\n' "$3" >&2
  printf '  Nothing after this gate was run. Fix the cause, then re-run the script.\n' >&2
  exit 1
}
confirm() {  # confirm WORD "question"
  local answer
  printf '\n\033[33m%s\033[0m\nType %s to continue (anything else stops): ' "$2" "$1"
  read -r answer
  [ "$answer" = "$1" ] || die "operator" "not confirmed" "everything from this step on"
}
pause_for() {  # manual action in a browser
  printf '\n\033[36mMANUAL STEP\033[0m\n%s\n' "$1"
  confirm DONE "When you have finished the step above:"
}

# ── state (only values this script writes; validated before use) ──────────
save() {
  [[ "$1" =~ ^[A-Z_]+$ && "$2" =~ ^[A-Za-z0-9_@:./=+-]*$ ]] || die "state" "refusing to store $1"
  mkdir -p "$STATE_DIR" && chmod 700 "$STATE_DIR"
  touch "$STATE" && chmod 600 "$STATE"
  grep -v "^$1=" "$STATE" > "$STATE.tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
  export "$1=$2"
}
load() {
  [ -f "$STATE" ] || return 0
  while IFS='=' read -r k v; do
    [[ "$k" =~ ^[A-Z_]+$ && "$v" =~ ^[A-Za-z0-9_@:./=+-]*$ ]] && export "$k=$v"
  done < "$STATE"
}
done_phase() { save "DONE_${1^^}" 1; ok "phase $1 complete"; }
is_done()    { local v="DONE_${1^^}"; [ "${!v:-}" = 1 ]; }

# ── API helpers ────────────────────────────────────────────────────────────
token()    { gcloud auth print-access-token; }
idtoken()  { gcloud auth print-identity-token; }
fs_get()   { curl -sS -H "Authorization: Bearer $(token)" "$FS/$1"; }
fs_code()  { curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $(token)" "$FS/$1"; }
fs_query() { curl -sS -X POST -H "Authorization: Bearer $(token)" -H 'Content-Type: application/json' "$FS:runQuery" -d "$1"; }
gh_get()   { curl -sS -H 'Accept: application/vnd.github+json' "$GH_API/$1"; }
eq_query() {  # eq_query COLLECTION FIELD VALUE
  printf '{"structuredQuery":{"from":[{"collectionId":"%s"}],"where":{"fieldFilter":{"field":{"fieldPath":"%s"},"op":"EQUAL","value":{"stringValue":"%s"}}}}}' "$1" "$2" "$3"
}
active_ids() { fs_query "$(eq_query monitors status ACTIVE)" | jq -r '.[] | select(.document) | .document.name | split("/")[-1]'; }
worker() {  # worker METHOD PATH [BODY] -> prints "HTTP_CODE BODY"
  local out code
  out=$(mktemp)
  if [ -n "${3:-}" ]; then
    code=$(curl -sS -o "$out" -w '%{http_code}' -X "$1" -H "Authorization: Bearer $(idtoken)" \
      -H 'Content-Type: application/json' --data "$3" --max-time 70 "$URL$2")
  else
    code=$(curl -sS -o "$out" -w '%{http_code}' -X "$1" -H "Authorization: Bearer $(idtoken)" --max-time 70 "$URL$2")
  fi
  printf '%s %s\n' "$code" "$(cat "$out")"; rm -f "$out"
}
log_json() {  # log_json FILTER FRESHNESS
  gcloud logging read "$1" --freshness="$2" --limit=20 --format=json
}
wait_for() {  # wait_for TRIES SECONDS command...  (retries while the command fails)
  local tries=$1 pause=$2; shift 2
  for ((i = 1; i <= tries; i++)); do "$@" && return 0; sleep "$pause"; done
  return 1
}
monitor_status() { fs_get "monitors/$1" | jq -r '.fields.status.stringValue // "MISSING"'; }
target_labels()  { jq -r '.fields.targets.arrayValue.values[]?.mapValue.fields | "\(.venue_name.stringValue) · \(.fmt.stringValue)"' "$1" | LC_ALL=C sort; }
outbox_rows()    { fs_query "$(eq_query monitor_outbox monitor_id "$1")" | jq -r '.[] | select(.document) | .document.fields | "\(.status.stringValue) \(.attempts.integerValue)"' | LC_ALL=C sort; }
verify_access() {  # bucket + effective-access proofs (G3b / G3d)
  gcloud storage buckets get-iam-policy "$DR_BUCKET" --format=json > "$STATE_DIR/dr-bucket-iam.json"
  python3 "$ROOT/tools/verify_backup_access.py" bucket "$STATE_DIR/dr-bucket-iam.json" \
    --admin "$BACKUP_ADMIN" --agent "$FIRESTORE_AGENT" || die "$1" "bucket policy is not exactly the two approved bindings" "no backup uploaded"
  gcloud asset analyze-iam-policy "${ANALYSIS_SCOPE[@]}" --full-resource-name="$DR_BUCKET_RESOURCE" \
    --permissions="$ACCESS_PERMS" --expand-groups --analyze-service-account-impersonation \
    --show-response --format=json > "$STATE_DIR/dr-effective.json" \
    || die "$1" "Policy Analyzer could not run at this scope (permission?)" "no backup uploaded"
  python3 "$ROOT/tools/verify_backup_access.py" effective "$STATE_DIR/dr-effective.json" \
    --admin "$BACKUP_ADMIN" --agent "$FIRESTORE_AGENT" --dr-project-number "$DR_PROJECT_NUMBER" \
    || die "$1" "an identity outside the allow-list can reach the DR bucket (see lines above)" "no backup uploaded"
  local pap ubla
  pap=$(gcloud storage buckets describe "$DR_BUCKET" --format='value(public_access_prevention)')
  ubla=$(gcloud storage buckets describe "$DR_BUCKET" --format='value(uniform_bucket_level_access)')
  [ "$pap" = enforced ] && [ "${ubla,,}" = true ] || die "$1" "public access prevention or uniform access is off" "no backup uploaded"
  ok "only approved identities can reach $DR_BUCKET"
}
deploy_worker() {  # deploy_worker REVISION_SUFFIX ENV_VARS
  gcloud run deploy "$SERVICE" --image="$IMAGE_DIGEST" --region="$REGION" --service-account="$WORKER_SA" \
    --no-allow-unauthenticated --ingress=all --max-instances=1 --min-instances=0 --concurrency=4 \
    --timeout=55 --cpu=1 --memory=512Mi --revision-suffix="$1" --set-env-vars="$2" --quiet
}
startup_line() {  # latest start-up summary of the current revision
  log_json "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND resource.labels.revision_name=\"$1\" AND jsonPayload.worker_start:*" 15m \
    | jq -c '.[0].jsonPayload // empty'
}
# ── Firestore Rules comparison (G2) ────────────────────────────────────────
# Writes the deployed source to $1 byte-for-byte. `jq -j` adds nothing (`jq -r`
# appended a line feed, which made identical Rules look different). Fails closed
# on an HTTP error, a missing release/ruleset, anything but exactly one source
# file, or a non-string/empty source.
fetch_deployed_rules() {
  local api=https://firebaserules.googleapis.com/v1 rs
  rs=$(curl -fsS -H "Authorization: Bearer $(token)" "$api/projects/$PROJECT/releases/cloud.firestore" \
       | jq -er '.rulesetName | select(type == "string" and startswith("projects/"))') || return 1
  curl -fsS -H "Authorization: Bearer $(token)" "$api/$rs" \
    | jq -ej '.source.files | if (type == "array" and length == 1 and (.[0].content | type) == "string")
                             then .[0].content else error("unexpected ruleset source") end' > "$1" || return 1
  [ -s "$1" ]
}
# Pass only if the two files are byte-identical once TRAILING LINE FEEDS ALONE are
# removed from each end. Every other byte — including whitespace inside or before
# the final line, carriage returns and comments — must match. Prints hashes, and a
# unified diff on mismatch. Missing, unreadable or empty input fails closed.
rules_equivalent() {
  python3 - "$1" "$2" <<'PY'
import difflib, hashlib, sys
try:
    base, deployed = (open(p, "rb").read() for p in sys.argv[1:3])
except OSError as exc:
    print(f"  rules: unreadable ({exc.__class__.__name__})"); sys.exit(1)
a, b = base.rstrip(b"\n"), deployed.rstrip(b"\n")
for name, raw, core in (("baseline", base, a), ("deployed", deployed, b)):
    print(f"  {name}: {len(raw)} bytes, sha256 {hashlib.sha256(raw).hexdigest()}, "
          f"without trailing LF {hashlib.sha256(core).hexdigest()}")
if not a or not b:
    print("  rules: empty source"); sys.exit(1)
if a == b:
    sys.exit(0)
sys.stdout.writelines(difflib.unified_diff(a.decode("utf-8", "replace").splitlines(True),
                                           b.decode("utf-8", "replace").splitlines(True),
                                           "baseline", "deployed", n=2))
print(); sys.exit(1)
PY
}
current_revision() { gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.latestReadyRevisionName)'; }
# Predicates polled by wait_for.
tick_ok()        { [[ "$(worker POST /tick)" == 200* ]]; }
startup_ready()  { STARTUP=$(startup_line "$(current_revision)"); [ -n "$STARTUP" ]; }
export_done()    { gcloud storage ls "$EXPORT_PATH/**" 2>/dev/null | grep -q overall_export_metadata; }
legacy_idle()    {
  [ "$(gh_get 'actions/workflows/bookmyshow-monitor.yml/runs?status=in_progress' | jq .total_count)" = 0 ] &&
  [ "$(gh_get 'actions/workflows/bookmyshow-monitor.yml/runs?status=queued' | jq .total_count)" = 0 ]
}
original_active() { [ "$(monitor_status "$DOOMSDAY_ID")" = ACTIVE ]; }
original_checked_since() {  # a fenced check of the original monitor logged after $1, and state updated after $1
  local c
  gcloud logging read "resource.labels.service_name=\"$SERVICE\" AND textPayload:\"[checker] ${DOOMSDAY_ID:0:8} — \" AND timestamp>=\"$1\"" \
    --limit=1 --format='value(textPayload)' | grep -q . || return 1
  c=$(fs_get "monitor_state/$DOOMSDAY_ID" | jq -r '.fields.last_check_at.stringValue // empty')
  [ -n "$c" ] && [ "$(date -d "$c" +%s)" -ge "$(date -d "$1" +%s)" ]
}

# ── phases ─────────────────────────────────────────────────────────────────
phase_preflight() {
  step "1/13 Pre-flight (read-only): tools, identity, CI, Rules, original monitors"
  local cmd
  for cmd in gcloud jq curl python3 git docker gpg npx shred sort; do
    command -v "$cmd" >/dev/null || die "G0" "missing tool: $cmd (use Google Cloud Shell)"
  done
  local account; account=$(gcloud config get-value account 2>/dev/null)
  [ -n "$account" ] || die "G0" "no active gcloud account"
  save BACKUP_ADMIN "user:$account"
  gcloud projects describe "$PROJECT" --format='value(projectId)' >/dev/null || die "G0" "cannot read project $PROJECT"
  save PROJECT_NUMBER "$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
  save FIRESTORE_AGENT "serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-firestore.iam.gserviceaccount.com"

  local sha dirty jobs
  sha=$(git -C "$ROOT" rev-parse HEAD)
  dirty=$(git -C "$ROOT" status --porcelain | grep -v ' infra/production/env.sh$' || true)
  [ -z "$dirty" ] || die "G0" "the checkout has local changes other than env.sh: $dirty"
  jobs=$(gh_get "commits/$sha/check-runs" | jq -r '.check_runs[] | select(.conclusion=="success") | .name' | LC_ALL=C sort -u)
  [ "$jobs" = "$CI_JOBS" ] || die "G0" "CI is not green on $sha (need: $(echo "$CI_JOBS" | tr '\n' ' '))" "everything"
  save BUILD_SHA "$sha"; ok "CI green on ${sha:0:12}"

  save FS_LOCATION "$(gcloud firestore databases describe --database='(default)' --format='value(locationId)')"
  case "$FS_LOCATION" in nam5) save DR_LOCATION US ;; eur3) save DR_LOCATION EU ;; *) save DR_LOCATION "$FS_LOCATION" ;; esac
  ok "Firestore $FS_LOCATION → DR bucket location $DR_LOCATION; Cloud Run $REGION"

  fetch_deployed_rules "$STATE_DIR/deployed.rules" \
    || die "G2" "could not read the deployed Firestore Rules (release, ruleset or source missing)" "everything"
  git -C "$ROOT" show "$BASELINE:firestore.rules" > "$STATE_DIR/baseline.rules" \
    || die "G2" "could not read the baseline firestore.rules" "everything"
  rules_equivalent "$STATE_DIR/baseline.rules" "$STATE_DIR/deployed.rules" \
    || die "G2" "deployed Firestore Rules differ from the repository baseline (diff above)" "everything"
  ok "deployed Rules = repository (identical apart from trailing line feeds)"

  local names original overlap
  names=$(fs_get "monitors?pageSize=300&mask.fieldPaths=status" | jq -r '.documents[]?.name | split("/")[-1]')
  original=$(grep "^$ORIGINAL_PREFIX" <<<"$names" || true)
  overlap=$(grep "^$OVERLAP_PREFIX" <<<"$names" || true)
  [ "$(wc -l <<<"$original")" = 1 ] && [ -n "$original" ] || die "G1" "expected exactly one $ORIGINAL_PREFIX… monitor" "everything"
  [ "$(wc -l <<<"$overlap")" = 1 ] && [ -n "$overlap" ] || die "G1" "expected exactly one $OVERLAP_PREFIX… monitor" "everything"
  save DOOMSDAY_ID "$original"; save OVERLAP_ID "$overlap"
  fs_get "monitors/$DOOMSDAY_ID" > "$STATE_DIR/af1711d4-before.json"; chmod 600 "$STATE_DIR/af1711d4-before.json"
  [ "$(monitor_status "$DOOMSDAY_ID")" != ACTIVE ] || die "G1" "$ORIGINAL_PREFIX is already ACTIVE (who reactivated it?)" "everything"
  [ "$(monitor_status "$OVERLAP_ID")" != ACTIVE ] || die "G1" "$OVERLAP_PREFIX is ACTIVE — it must stay stopped" "everything"
  [ "$(target_labels "$STATE_DIR/af1711d4-before.json")" = "$EXPECTED_TARGETS" ] \
    || die "G1" "$ORIGINAL_PREFIX targets differ from the five expected targets" "everything"
  ok "$ORIGINAL_PREFIX: $(monitor_status "$DOOMSDAY_ID"), 5 expected targets; $OVERLAP_PREFIX: $(monitor_status "$OVERLAP_ID")"
  say "  running monitors right now: $(active_ids | grep -c . || true)"
  [ "$(fs_code worker_runtime/control)" = 404 ] || is_done fenced || die "G1" "worker_runtime/control already exists" "everything"
  done_phase preflight
}

phase_dr_isolation() {
  step "2/13 Isolated DR project and bucket, proven before any upload (G3a, G3b)"
  confirm ISOLATE "Create project $DR_PROJECT (billing linked to $PROJECT's account) and bucket $DR_BUCKET?"
  gcloud services enable run.googleapis.com cloudscheduler.googleapis.com artifactregistry.googleapis.com \
    secretmanager.googleapis.com iamcredentials.googleapis.com --project="$PROJECT"
  gcloud projects describe "$DR_PROJECT" >/dev/null 2>&1 \
    || gcloud projects create "$DR_PROJECT" --name="TicketRadar DR backups" \
    || die "G3a" "could not create $DR_PROJECT (ID taken? edit DR_PROJECT in env.sh)" "no backup uploaded"
  save DR_PROJECT_NUMBER "$(gcloud projects describe "$DR_PROJECT" --format='value(projectNumber)')"
  local billing; billing=$(gcloud billing projects describe "$PROJECT" --format='value(billingAccountName)')
  gcloud billing projects link "$DR_PROJECT" --billing-account="${billing#billingAccounts/}" >/dev/null
  gcloud services enable storage.googleapis.com cloudasset.googleapis.com --project="$DR_PROJECT"

  gcloud projects get-ancestors "$DR_PROJECT" --format=json > "$STATE_DIR/dr-ancestors.json"
  local org=""
  if ! python3 "$ROOT/tools/verify_backup_access.py" ancestors "$STATE_DIR/dr-ancestors.json"; then
    org=$(jq -r '.[] | select(.type=="organization") | .id' "$STATE_DIR/dr-ancestors.json")
    [ -n "$org" ] || die "G3a" "the DR project sits in a folder without an organisation id" "no backup uploaded"
    gcloud projects get-ancestors-iam-policy "$DR_PROJECT" --include-deny --format=json \
      | jq -r '.[] | .id as $id | (.policy.bindings // [])[] | "  \($id)  \(.role)  \(.members|join(","))"'
    confirm ORG-REVIEWED "An organisation ($org) is above $DR_PROJECT. Its grants are listed above and apply here. The access proof will run at organisation scope. Reviewed with the organisation admin?"
  fi
  if [ -n "$org" ]; then ANALYSIS_SCOPE=(--organization="$org"); else ANALYSIS_SCOPE=(--project="$DR_PROJECT"); fi
  save ANALYSIS_SCOPE_ARG "${ANALYSIS_SCOPE[0]}"
  local extra
  extra=$(gcloud projects get-iam-policy "$DR_PROJECT" --flatten=bindings --format='value(bindings.role,bindings.members)' \
    | grep -v "user:${BACKUP_ADMIN#user:}" | grep -vE "serviceAccount:service-${DR_PROJECT_NUMBER}@(gcp-sa-[a-z0-9-]+|gs-project-accounts)\.iam\.gserviceaccount\.com" || true)
  [ -z "$extra" ] || die "G3a" "unexpected members on $DR_PROJECT: $extra" "no backup uploaded"
  ok "DR project holds only you and its own Google-managed agents"

  gcloud storage buckets describe "$DR_BUCKET" >/dev/null 2>&1 \
    || gcloud storage buckets create "$DR_BUCKET" --project="$DR_PROJECT" --location="$DR_LOCATION" \
         --uniform-bucket-level-access --public-access-prevention --soft-delete-duration=7d
  (umask 077; jq -n --arg a "$BACKUP_ADMIN" --arg f "$FIRESTORE_AGENT" \
    '{bindings: [{role: "roles/storage.objectAdmin", members: [$a]}, {role: "roles/storage.admin", members: [$f]}]}' \
    > "$STATE_DIR/dr-bucket-policy.json")
  gcloud storage buckets set-iam-policy "$DR_BUCKET" "$STATE_DIR/dr-bucket-policy.json" >/dev/null \
    || die "G3b" "could not set the bucket policy (Firestore agent missing? run: gcloud beta services identity create --service=firestore.googleapis.com --project=$PROJECT)" "no backup uploaded"
  verify_access G3b
  done_phase dr_isolation
}

phase_backups() {
  step "3/13 Firestore export, PITR, daily schedule, encrypted Auth export (G3c, G3d)"
  confirm BACKUP "Export all Firestore data and all Firebase Auth users into $DR_BUCKET now?"
  save EXPORT_PATH "$DR_BUCKET/firestore/$(date -u +%Y%m%dT%H%MZ)"
  gcloud firestore export "$EXPORT_PATH" --project="$PROJECT" --database='(default)' --async
  say "  waiting for the export (up to 30 min)…"
  wait_for 120 15 export_done \
    || die "G3c" "export did not finish (check: gcloud firestore operations list --database='(default)')" "no real-data worker; do not widen access"
  ok "export written by the Firestore service agent with the bucket-scoped grant only"
  gcloud firestore databases update --database='(default)' --enable-pitr --quiet >/dev/null
  [ "$(gcloud firestore databases describe --database='(default)' --format='value(pointInTimeRecoveryEnablement)')" = POINT_IN_TIME_RECOVERY_ENABLED ] \
    || die "G3d" "PITR is not enabled" "no real-data worker"
  gcloud firestore backups schedules list --database='(default)' --format='value(name)' | grep -q . \
    || gcloud firestore backups schedules create --database='(default)' --recurrence=daily --retention=14d >/dev/null
  ok "PITR on; daily backup schedule present"

  say "  Auth export: plaintext only in RAM (/dev/shm), encrypted before upload."
  say "  Choose a long passphrase and save it in your password manager first."
  (
    umask 077
    npx -y firebase-tools@13 login --no-localhost
    npx -y firebase-tools@13 auth:export /dev/shm/radar-auth.json --format=json --project "$PROJECT"
    say "  exported users: $(jq '.users | length' /dev/shm/radar-auth.json)  (compare with Authentication → Users)"
    gpg --pinentry-mode loopback --symmetric --cipher-algo AES256 --output "/dev/shm/radar-auth-$(date -u +%Y%m%d).json.gpg" /dev/shm/radar-auth.json
    shred -u /dev/shm/radar-auth.json
    gcloud storage cp /dev/shm/radar-auth-*.json.gpg "$DR_BUCKET/auth/"
    shred -u /dev/shm/radar-auth-*.json.gpg
  ) || { shred -u /dev/shm/radar-auth* 2>/dev/null || true; die "G3d" "Auth export failed (plaintext removed)" "no real-data worker"; }
  ! ls /dev/shm/radar-auth* >/dev/null 2>&1 || die "G3d" "plaintext remains in /dev/shm" "no real-data worker"
  gcloud storage ls "$DR_BUCKET/auth/" | grep -q '\.json\.gpg$' || die "G3d" "encrypted Auth export missing" "no real-data worker"
  pause_for "Firebase console → Authentication → Users → ⋮ → Password hash parameters: copy them into your password manager next to the passphrase."
  verify_access G3d
  done_phase backups
}

phase_identities() {
  step "4/13 Service accounts (no keys) and Artifact Registry"
  local name
  for name in "$WORKER_SA_NAME" "$INVOKER_SA_NAME" "$PROBE_SA_NAME"; do
    gcloud iam service-accounts describe "$name@$PROJECT.iam.gserviceaccount.com" >/dev/null 2>&1 \
      || gcloud iam service-accounts create "$name" --display-name="Radar $name"
  done
  gcloud iam service-accounts add-iam-policy-binding "$INVOKER_SA" \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
    --role=roles/iam.serviceAccountTokenCreator >/dev/null
  gcloud artifacts repositories describe "$AR_REPO" --location="$REGION" >/dev/null 2>&1 \
    || gcloud artifacts repositories create "$AR_REPO" --repository-format=docker --location="$REGION" --description="TicketRadar monitor worker images"
  for name in "$WORKER_SA" "$INVOKER_SA" "$PROBE_SA"; do
    [ -z "$(gcloud iam service-accounts keys list --iam-account "$name" --managed-by=user --format='value(name)')" ] \
      || die "security" "$name has a user-managed key; this design uses none" "everything after identities"
  done
  done_phase identities
}

phase_build() {
  step "5/13 Build and push the worker image from ${BUILD_SHA:0:12}"
  local tree=$HOME/radar-worker-$BUILD_SHA
  [ -d "$tree" ] || python3 "$ROOT/tools/materialize_worker_candidate.py" "$tree"
  local image=${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/${IMAGE_NAME}:${BUILD_SHA:0:12}
  gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet >/dev/null
  (cd "$tree" && docker build --file Dockerfile.worker --tag "$image" . && docker push "$image") || die "build" "image build or push failed"
  save IMAGE_DIGEST "${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/${IMAGE_NAME}@$(gcloud artifacts docker images describe "$image" --format='value(image_summary.digest)')"
  [[ "$IMAGE_DIGEST" =~ @sha256:[0-9a-f]{64}$ ]] || die "build" "no image digest"
  ok "$IMAGE_DIGEST"
  done_phase build
}

phase_shadow() {
  step "6/13 Private shadow service and scheduler (G4, G5) — synthetic data only"
  deploy_worker "shadow-${BUILD_SHA:0:7}" WORKER_MODE=shadow
  save URL "$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
  gcloud run services add-iam-policy-binding "$SERVICE" --region="$REGION" --member="serviceAccount:$INVOKER_SA" --role=roles/run.invoker >/dev/null
  gcloud run services get-iam-policy "$SERVICE" --region="$REGION" --format=json | jq -e '[.bindings[]?.members[]] | any(. == "allUsers" or . == "allAuthenticatedUsers") | not' >/dev/null \
    || die "G4" "the service is public" "scheduler not created"
  [ "$(curl -sS -o /dev/null -w '%{http_code}' "$URL/healthz")" = 403 ] || die "G4" "unauthenticated request was not refused" "scheduler not created"
  ok "private: unauthenticated → 403"
  local r
  r=$(worker GET /healthz);  [ "$r" = '200 {"ready": true, "mode": "shadow"}' ] || die "G5" "healthz: $r"
  r=$(worker POST /check '{"monitor_id":"fixture-monitor","force":true}'); [ "$r" = '200 {"slots": [{"checked": 1, "changes": 0}]}' ] || die "G5" "check: $r"
  r=$(worker POST /tick); [[ "$r" == 200* ]] || die "G5" "tick: $r"
  r=$(worker POST /tick '{"x":1}'); [[ "$r" == 400* ]] || die "G5" "bad body accepted: $r"
  ok "shadow behaviour verified"
  gcloud scheduler jobs describe "$SCHEDULER_JOB" --location="$REGION" >/dev/null 2>&1 \
    || gcloud scheduler jobs create http "$SCHEDULER_JOB" --location="$REGION" --schedule='* * * * *' --time-zone='Etc/UTC' \
         --uri="$URL/tick" --http-method=POST --oidc-service-account-email="$INVOKER_SA" --oidc-token-audience="$URL" \
         --attempt-deadline=60s --max-retry-attempts=0
  gcloud scheduler jobs resume "$SCHEDULER_JOB" --location="$REGION" >/dev/null 2>&1 || true
  say "  observing 5 minutes of authenticated scheduler ticks…"; sleep 300
  local complete denied
  complete=$(log_json "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND jsonPayload.worker_result=\"complete\"" 6m | jq length)
  denied=$(log_json "resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"$SCHEDULER_JOB\" AND (httpRequest.status=401 OR httpRequest.status=403)" 6m | jq length)
  gcloud scheduler jobs pause "$SCHEDULER_JOB" --location="$REGION" >/dev/null
  [ "$(gcloud scheduler jobs describe "$SCHEDULER_JOB" --location="$REGION" --format='value(state)')" = PAUSED ] || die "G5" "scheduler not paused"
  [ "$complete" -ge 4 ] && [ "$denied" = 0 ] || die "G5" "scheduler ticks: $complete complete, $denied refused" "scheduler paused"
  ok "scheduler OIDC works ($complete ticks); scheduler now PAUSED"
  done_phase shadow
}

phase_probe() {
  step "7/13 BookMyShow connectivity from Cloud Run — exactly one request (G6)"
  confirm PROBE "Send ONE listing request to BookMyShow from Cloud Run?"
  gcloud run jobs describe "$PROBE_JOB" --region="$REGION" >/dev/null 2>&1 && gcloud run jobs delete "$PROBE_JOB" --region="$REGION" --quiet
  gcloud run jobs create "$PROBE_JOB" --region="$REGION" --image="$IMAGE_DIGEST" --service-account="$PROBE_SA" \
    --max-retries=0 --task-timeout=120s --command=python --args=run_bms_probe.py >/dev/null
  gcloud run jobs execute "$PROBE_JOB" --region="$REGION" --wait >/dev/null || true
  sleep 20
  local lines
  lines=$(log_json "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$PROBE_JOB\"" 15m | jq -r '.[].textPayload // empty')
  gcloud run jobs delete "$PROBE_JOB" --region="$REGION" --quiet >/dev/null
  say "$lines" | grep -E 'probe|BMS_PROBE' || true
  if ! { grep -q '\[probe\] requests=1 status=200' <<<"$lines" && grep -qE 'BMS_PROBE_OK movies=[0-9]+' <<<"$lines"; }; then
    die "G6" "BookMyShow did not answer Cloud Run normally — do not retry in a loop" \
           "migration stopped: scheduler paused, legacy workflow untouched, $ORIGINAL_PREFIX stopped"
  fi
  ok "BookMyShow answered one request from Cloud Run (technical reachability only — not permission)"
  done_phase probe
}

phase_legacy() {
  step "8/13 Legacy GitHub monitor off, production secrets gated (G7)"
  pause_for "1. https://github.com/$GITHUB_REPO/actions → left list 'BookMyShow monitor' → ⋯ (top right) → Disable workflow.
2. https://github.com/$GITHUB_REPO/settings/environments → production → tick 'Required reviewers' → add yourself → Save protection rules.
3. Do NOT touch 'Catalogue sync'."
  [ "$(gh_get actions/workflows/bookmyshow-monitor.yml | jq -r .state)" = disabled_manually ] \
    || die "G7" "BookMyShow monitor is not disabled" "no control document, no fenced worker"
  [ "$(gh_get actions/workflows/catalogue-sync.yml | jq -r .state)" = active ] || die "G13" "Catalogue sync is not active" "no fenced worker"
  curl -sS "https://raw.githubusercontent.com/$GITHUB_REPO/main/.github/workflows/catalogue-sync.yml" | grep -q 'cron: "0 1,7,13,19 \* \* \*"' \
    || die "G13" "catalogue schedule on main is not 0 1,7,13,19 * * *" "no fenced worker"
  say "  waiting for any legacy run already started to finish…"
  wait_for 80 15 legacy_idle \
    || die "G7" "a legacy run is still in progress or queued (cancel it in the Actions tab)" "no fenced worker"
  local rules
  rules=$(gh_get environments/production | jq -r '[.protection_rules[]?.type] | join(",")' 2>/dev/null || true)
  if [[ "$rules" == *required_reviewers* ]]; then ok "production environment requires a reviewer"
  else confirm REVIEWER-SET "GitHub did not confirm the reviewer rule anonymously. Is 'Required reviewers' saved on the production environment?"; fi
  ok "legacy engine disabled; no legacy run in flight; catalogue sync unchanged"
  done_phase legacy
}

phase_fenced() {
  step "9/13 Worker authority and fenced worker WITHOUT a mail sender (G8)"
  [ "$(gh_get actions/workflows/bookmyshow-monitor.yml | jq -r .state)" = disabled_manually ] || die "G7" "legacy workflow re-enabled" "no fenced worker"
  confirm FENCE "Store the Gmail sender in Secret Manager, grant the worker Firestore + Auth read, create worker_runtime/control and deploy the fenced worker (no sender mounted yet)?"
  local s value
  for s in "$SECRET_GMAIL_ADDRESS" "$SECRET_GMAIL_PASSWORD"; do
    if ! gcloud secrets describe "$s" >/dev/null 2>&1; then
      read -r -s -p "  Value for $s (hidden): " value; echo
      [ -n "$value" ] || die "G8" "empty value for $s"
      printf '%s' "$value" | gcloud secrets create "$s" --replication-policy=automatic --data-file=- >/dev/null
      value=""
    fi
    gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$WORKER_SA" --role=roles/secretmanager.secretAccessor >/dev/null
  done
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$WORKER_SA" --role=roles/datastore.user --condition=None >/dev/null
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$WORKER_SA" --role=roles/firebaseauth.viewer --condition=None >/dev/null
  if [ "$(fs_code worker_runtime/control)" = 404 ]; then
    curl -sS -X PATCH -H "Authorization: Bearer $(token)" -H 'Content-Type: application/json' \
      "$FS/worker_runtime/control?currentDocument.exists=false" \
      -d "{\"fields\":{\"authority\":{\"stringValue\":\"$AUTHORITY\"},\"epoch\":{\"integerValue\":\"1\"},\"holder\":{\"stringValue\":\"\"},\"lease_until\":{\"stringValue\":\"\"}}}" >/dev/null
  fi
  fs_get worker_runtime/control | jq -e --arg a "$AUTHORITY" '.fields.authority.stringValue == $a and (.fields.epoch.integerValue | tonumber) >= 1' >/dev/null \
    || die "G8" "control document is wrong (authority or integer epoch)" "scheduler paused"
  deploy_worker "fenced-sink-${BUILD_SHA:0:7}" \
    "WORKER_MODE=fenced,FIREBASE_CREDENTIAL_MODE=adc,FIREBASE_PROJECT_ID=$PROJECT,FIREBASE_EXPECTED_PROJECT=$PROJECT,WORKER_AUTHORITY=$AUTHORITY,TZ=Asia/Kolkata" \
    || die "G8" "fenced revision failed to start (see Cloud Run logs)" "scheduler paused"
  local r line
  r=$(worker GET /healthz); [ "$r" = '200 {"ready": true, "mode": "fenced"}' ] || die "G8" "healthz: $r" "scheduler paused"
  wait_for 8 15 tick_ok || die "G8" "fenced tick keeps failing (Firestore 403 → IAM not propagated yet; re-run in 2 min; or read Cloud Run logs)" "scheduler paused"
  wait_for 8 15 startup_ready || die "G8" "no start-up summary in the logs" "scheduler paused"
  line=$STARTUP
  [ "$(jq -c '{worker_start,authority,credential_mode,project_pinned,sender_configured,legacy_key_present}' <<<"$line")" \
    = "{\"worker_start\":\"fenced\",\"authority\":\"$AUTHORITY\",\"credential_mode\":\"adc\",\"project_pinned\":true,\"sender_configured\":false,\"legacy_key_present\":false}" ] \
    || die "G8" "start-up summary not as expected: $line" "scheduler paused"
  fs_get worker_runtime/control | jq -e '.fields.holder.stringValue == "" and .fields.lease_until.stringValue == ""' >/dev/null \
    || die "G8" "lease was not released after the tick" "scheduler paused"
  ok "fenced, keyless, project-pinned, no sender mounted; lease acquired and released"
  done_phase fenced
}

phase_alerts() {
  step "10/13 Log-based metrics and alert policies (G9)"
  local name desc filter
  while IFS='|' read -r name desc filter; do
    case "$name" in \#*|'') continue ;; esac
    gcloud logging metrics describe "$name" >/dev/null 2>&1 || gcloud logging metrics create "$name" --description="$desc" --log-filter="$filter" >/dev/null
  done < "$HERE/logging-metrics.txt"
  pause_for "Console → Monitoring → Alerting → Edit notification channels → Email → Add new → your address → Save. Then copy the channel's numeric ID."
  local channel; read -r -p "  Notification channel numeric ID: " channel
  [[ "$channel" =~ ^[0-9]+$ ]] || die "G9" "not a numeric channel id"
  local f display existing
  existing=$(gcloud monitoring policies list --format='value(displayName)')
  for f in "$HERE"/alerts/*.json; do
    display=$(jq -r .displayName "$f")
    grep -qxF "$display" <<<"$existing" && continue
    sed "s#CHANNEL_ID#$channel#" "$f" > "$STATE_DIR/policy.json"
    gcloud monitoring policies create --policy-from-file="$STATE_DIR/policy.json" >/dev/null || die "G9" "could not create '$display'"
  done
  [ "$(gcloud logging metrics list --format='value(name)' | grep -c '^radar_')" -ge 4 ] || die "G9" "metrics missing"
  ok "4 metrics and 4 alert policies"
  done_phase alerts
}

phase_notify() {
  step "11/13 One controlled notification to YOUR inbox (G10)"
  [ "$(active_ids | grep -c . || true)" = 0 ] || die "G10" "other monitors are running — the test must be the only active one" "scheduler paused"
  pause_for "In the TicketRadar app, signed in as yourself, create Test-A: Hyderabad → a film with shows bookable today → ONE theatre → Any format → every 5 min → ends in 2 hours → Start monitoring.
A red 'first check could not be started' banner is EXPECTED (legacy workflow disabled)."
  local ids; ids=$(active_ids)
  [ "$(grep -c . <<<"$ids")" = 1 ] || die "G10" "expected exactly one active monitor (the test), found: $ids" "scheduler paused"
  [ "$ids" != "$DOOMSDAY_ID" ] && [ "$ids" != "$OVERLAP_ID" ] || die "G10" "the active monitor is an original one" "scheduler paused"
  save TEST_ID "$ids"
  local r; r=$(worker POST /check "{\"monitor_id\":\"$TEST_ID\",\"force\":true}"); [[ "$r" == 200* ]] || die "G10" "check: $r"
  [ "$(outbox_rows "$TEST_ID")" = "failed 1" ] || die "G10" "sink check: expected one 'failed' row, got: $(outbox_rows "$TEST_ID" | tr '\n' ';') (is the film bookable now?)" "no sender mounted"
  ok "alert detected and stopped at the sender: no email possible"
  confirm SEND "Mount the Gmail sender and send ONE test alert to your own address?"
  gcloud run services update "$SERVICE" --region="$REGION" --revision-suffix="fenced-${BUILD_SHA:0:7}" \
    --set-secrets="GMAIL_ADDRESS=${SECRET_GMAIL_ADDRESS}:1,GMAIL_APP_PASSWORD=${SECRET_GMAIL_PASSWORD}:1" --quiet >/dev/null
  wait_for 8 15 startup_ready || die "G10" "no start-up summary for the new revision"
  [ "$(jq -r .sender_configured <<<"$STARTUP")" = true ] || die "G10" "sender not mounted: $STARTUP"
  r=$(worker POST /check "{\"monitor_id\":\"$TEST_ID\",\"force\":true}"); [[ "$r" == 200* ]] || die "G10" "check: $r"
  [ "$(outbox_rows "$TEST_ID")" = $'accepted 1\nfailed 1' ] || die "G10" "expected one accepted row, got: $(outbox_rows "$TEST_ID" | tr '\n' ';')" \
    "remove the sender: bash infra/production/activate.sh rollback nosend"
  r=$(worker POST /check "{\"monitor_id\":\"$TEST_ID\",\"force\":true}"); [[ "$r" == 200* ]] || die "G10" "check: $r"
  [ "$(outbox_rows "$TEST_ID")" = $'accepted 1\nfailed 1' ] || die "G10" "a further check created another alert (duplicate)" "rollback nosend"
  ok "exactly one alert accepted by SMTP; no duplicate on the next check"
  confirm ONE-EMAIL "Check your inbox (and spam): exactly ONE TicketRadar email arrived?"
  pause_for "In the app: Monitors → Test-A card → Delete."
  [ "$(fs_code "monitors/$TEST_ID")" = 404 ] || die "G10" "Test-A still exists" "scheduler paused"
  done_phase notify
}

phase_activate() {
  step "12/13 Scheduler on, then reactivate the ORIGINAL $ORIGINAL_PREFIX (G11)"
  [ "$(gh_get actions/workflows/bookmyshow-monitor.yml | jq -r .state)" = disabled_manually ] || die "G7" "legacy workflow re-enabled" "scheduler paused"
  [ "$(active_ids | grep -c . || true)" = 0 ] || die "G11" "unexpected active monitors before activation" "scheduler paused"
  confirm ACTIVATE "Resume the every-minute scheduler and then reactivate $ORIGINAL_PREFIX through the app?"
  gcloud scheduler jobs resume "$SCHEDULER_JOB" --location="$REGION" >/dev/null
  local since; since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  pause_for "In the app, signed in as the owner of $ORIGINAL_PREFIX: Monitors → Finished → Avengers: Doomsday → 'Extend by 24 hours'.
Do NOT press 'Check now' or 'Retry'. Do NOT touch the other Doomsday monitor ($OVERLAP_PREFIX)."
  wait_for 20 15 original_active \
    || die "G11" "$ORIGINAL_PREFIX did not become ACTIVE" "scheduler running with no original monitor active"
  fs_get "monitors/$DOOMSDAY_ID" > "$STATE_DIR/af1711d4-after.json"
  local strip='del(.updateTime) | .fields |= del(.status, .monitor_until, .stopped_at, .stopped_reason, .problem, .first_check_requested_at, .movie)'
  diff <(jq -S "$strip" "$STATE_DIR/af1711d4-before.json") <(jq -S "$strip" "$STATE_DIR/af1711d4-after.json") \
    || die "G11" "$ORIGINAL_PREFIX changed beyond status/end time (diff above) — stop it in the app" "pause: bash infra/production/activate.sh rollback pause"
  [ "$(jq -r '.fields.movie.mapValue.fields.title.stringValue' "$STATE_DIR/af1711d4-after.json")" = \
    "$(jq -r '.fields.movie.mapValue.fields.title.stringValue' "$STATE_DIR/af1711d4-before.json")" ] || die "G11" "movie changed"
  [ "$(target_labels "$STATE_DIR/af1711d4-after.json")" = "$EXPECTED_TARGETS" ] || die "G11" "targets changed"
  ok "same document $DOOMSDAY_ID, same five targets, email, interval, owner"
  say "  waiting for the first check (≤ 6 min)…"
  wait_for 30 15 original_checked_since "$since" || die "G11" "no fenced check of $ORIGINAL_PREFIX after reactivation" "stop the monitor in the app; rollback pause"
  local verdicts label
  verdicts=$(gcloud logging read "resource.labels.service_name=\"$SERVICE\" AND textPayload:\" -> \" AND timestamp>=\"$since\"" --limit=50 --format='value(textPayload)')
  while IFS= read -r label; do
    grep -qF "    $label -> " <<<"$verdicts" || die "G11" "no verdict logged for target: $label" "stop the monitor in the app; rollback pause"
  done <<<"$EXPECTED_TARGETS"
  ok "all 5 targets checked: $(grep -F ' -> ' <<<"$verdicts" | sort -u | sed 's/^ *//' | tr '\n' ';')"
  [ "$(fs_get "monitor_state/$DOOMSDAY_ID" | jq '.fields.targets.mapValue.fields | length')" = 5 ] || die "G11" "monitor_state does not hold 5 targets"
  fs_get "monitor_state/$DOOMSDAY_ID" | jq -r '"  last check: \(.fields.last_check_at.stringValue)", (.fields.targets.mapValue.fields | to_entries[] | "  \(.key)  \(.value.mapValue.fields.availability.stringValue)")'
  [ "$(monitor_status "$OVERLAP_ID")" != ACTIVE ] || die "G11" "$OVERLAP_PREFIX became ACTIVE — stop it in the app" "rollback pause"
  ok "$ORIGINAL_PREFIX ACTIVE and checked on all 5 targets by the fenced worker; $OVERLAP_PREFIX untouched"
  done_phase activate
}

phase_watch() {
  step "13/13 First hour (G12) — prints a health line every 10 minutes"
  local i complete failed open denied
  for i in 1 2 3 4 5 6; do
    sleep 600
    complete=$(log_json "resource.labels.service_name=\"$SERVICE\" AND jsonPayload.worker_result=\"complete\"" 10m | jq length)
    failed=$(log_json "resource.labels.service_name=\"$SERVICE\" AND jsonPayload.worker_result=\"failed\"" 10m | jq length)
    denied=$(log_json "resource.labels.service_name=\"$SERVICE\" AND (textPayload:\"HTTP 403\" OR textPayload:\"HTTP 429\" OR textPayload:\"refused\")" 10m | jq length)
    open=$(fs_query '{"structuredQuery":{"from":[{"collectionId":"monitor_outbox"}],"where":{"fieldFilter":{"field":{"fieldPath":"status"},"op":"IN","value":{"arrayValue":{"values":[{"stringValue":"sending"},{"stringValue":"uncertain"}]}}}}}}' | jq '[.[] | select(.document)] | length')
    say "  +$((i * 10)) min: complete=$complete failed=$failed bms_refusals=$denied mail_uncertain_or_sending=$open $ORIGINAL_PREFIX=$(monitor_status "$DOOMSDAY_ID")"
    [ "$complete" -ge 7 ] && [ "$failed" -le 3 ] && [ "$denied" = 0 ] && [ "$open" = 0 ] \
      || die "G12" "unhealthy (see line above)" "decide: rollback pause / nosend; never re-enable the legacy workflow"
  done
  ok "first hour healthy. Extend $ORIGINAL_PREFIX once a day in the app until the 17 Dec show."
  done_phase watch
}

# ── rollback ───────────────────────────────────────────────────────────────
rollback() {
  case "${1:-}" in
    pause)  gcloud scheduler jobs pause "$SCHEDULER_JOB" --location="$REGION" ;;
    resume) gcloud scheduler jobs resume "$SCHEDULER_JOB" --location="$REGION" ;;
    nosend) gcloud run services update "$SERVICE" --region="$REGION" --remove-secrets=GMAIL_ADDRESS,GMAIL_APP_PASSWORD --quiet ;;
    kill)   curl -sS -X PATCH -H "Authorization: Bearer $(token)" -H 'Content-Type: application/json' \
              "$FS/worker_runtime/control?updateMask.fieldPaths=authority" \
              -d "{\"fields\":{\"authority\":{\"stringValue\":\"disabled-$(date -u +%Y%m%d%H%M)\"}}}" | jq -c .fields ;;
    unkill) curl -sS -X PATCH -H "Authorization: Bearer $(token)" -H 'Content-Type: application/json' \
              "$FS/worker_runtime/control?updateMask.fieldPaths=authority" \
              -d "{\"fields\":{\"authority\":{\"stringValue\":\"$AUTHORITY\"}}}" | jq -c .fields ;;
    epoch)  local next; next=$(( $(fs_get worker_runtime/control | jq -r .fields.epoch.integerValue) + 1 ))
            curl -sS -X PATCH -H "Authorization: Bearer $(token)" -H 'Content-Type: application/json' \
              "$FS/worker_runtime/control?updateMask.fieldPaths=epoch&updateMask.fieldPaths=holder&updateMask.fieldPaths=lease_until" \
              -d "{\"fields\":{\"epoch\":{\"integerValue\":\"$next\"},\"holder\":{\"stringValue\":\"\"},\"lease_until\":{\"stringValue\":\"\"}}}" | jq -c .fields ;;
    *) die "usage" "rollback pause|resume|nosend|kill|unkill|epoch" ;;
  esac
}

main() {
  mkdir -p "$STATE_DIR" && chmod 700 "$STATE_DIR"
  load
  if [ -n "${ANALYSIS_SCOPE_ARG:-}" ]; then ANALYSIS_SCOPE=("$ANALYSIS_SCOPE_ARG"); else ANALYSIS_SCOPE=(--project="$DR_PROJECT"); fi
  case "${1:-run}" in
    status)   for p in "${PHASES[@]}"; do if is_done "$p"; then say "  done     $p"; else say "  pending  $p"; fi; done
              [ -f "$STATE" ] && grep -vE '^DONE_' "$STATE" | sed 's/^/  /' ;;
    rollback) shift; rollback "${1:-}" ;;
    run)      for p in "${PHASES[@]}"; do is_done "$p" || "phase_$p"; done
              step "All gates passed. $ORIGINAL_PREFIX is being monitored by the private Cloud Run worker." ;;
    *)        die "usage" "activate.sh [run|status|rollback …]" ;;
  esac
}

if [ "${RADAR_ACTIVATE_LIB_ONLY:-0}" != 1 ]; then main "$@"; fi
