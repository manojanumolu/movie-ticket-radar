# TicketRadar production monitoring worker — the ONE place resource names live.
# Source it in Cloud Shell before every runbook step:   source infra/production/env.sh
# Defining variables changes nothing in Google Cloud.

export PROJECT=ticketradar03
# Cloud Run, Cloud Scheduler and Artifact Registry. Mumbai: closest to
# BookMyShow and to the users; independent of where Firestore lives.
export REGION=asia-south1
export SERVICE=radar-monitor-worker
export SCHEDULER_JOB=radar-worker-tick
export AR_REPO=radar
export IMAGE_NAME=worker
export WORKER_SA_NAME=radar-monitor-worker
export INVOKER_SA_NAME=radar-monitor-invoker
export PROBE_SA_NAME=radar-bms-probe
export PROBE_JOB=radar-bms-probe
export WORKER_SA=${WORKER_SA_NAME}@${PROJECT}.iam.gserviceaccount.com
export INVOKER_SA=${INVOKER_SA_NAME}@${PROJECT}.iam.gserviceaccount.com
export PROBE_SA=${PROBE_SA_NAME}@${PROJECT}.iam.gserviceaccount.com
# Worker authority written to worker_runtime/control. [A-Za-z0-9_-]{1,128}
export AUTHORITY=ticketradar03-cloudrun-v1
# Secret Manager names (values are entered interactively, never here).
export SECRET_GMAIL_ADDRESS=radar-gmail-address
export SECRET_GMAIL_PASSWORD=radar-gmail-app-password
# Restricted disaster-recovery bucket: Firestore export + encrypted Auth export.
export DR_BUCKET=gs://${PROJECT}-dr-restricted
# Firestore REST root (read-only inspection and the control document).
export FS="https://firestore.googleapis.com/v1/projects/${PROJECT}/databases/(default)/documents"
# GitHub identifiers for the optional "Check now" relay (Stage 2).
export GITHUB_REPO=manojanumolu/movie-ticket-radar
export GITHUB_REPO_ID=1368780302
export GITHUB_OWNER_ID=242191838
export RELAY_SA_NAME=radar-relay-invoker
export RELAY_SA=${RELAY_SA_NAME}@${PROJECT}.iam.gserviceaccount.com
export WIF_POOL=github
export WIF_PROVIDER=radar-relay

gcloud config set project "$PROJECT" >/dev/null 2>&1 || true
