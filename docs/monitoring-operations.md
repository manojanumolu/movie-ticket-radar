# Dedicated worker review and cutover

The current GitHub workflow continues to own production checks. The Terraform
configuration targets a separate staging project and defines a paused Scheduler
job. The image accepts only an explicitly selected mode. Shadow mode runs
synthetic checks without data and mail access.

## Repository checks

- Run the Python suite and the Firebase emulator suite from a clean checkout.
- Build the worker image with the hash-locked dependencies and smoke-test
  `GET /healthz`, `POST /check` for `fixture-monitor`, and `POST /tick`.
- Compare fixture traces with the existing runner before selecting a production
  cadence. Include due times, first checks, shared fetches, time budgets,
  unavailable catalogue, partially failed reads, city discovery, and disabled
  platforms. The service attempts slots at the start and around 30 seconds.
  The 45-second budget controls when new work starts. Existing network calls
  can outlive it. Measure load, deadlines and catch-up before a live rollout.

## Identity and boundaries

Use the attached service account for the Cloud Run worker. Supply the target
project as both `FIREBASE_PROJECT_ID` and `FIREBASE_EXPECTED_PROJECT`.
Set `FIREBASE_CREDENTIAL_MODE=adc`. The worker needs record access and Auth
account lookup. Give the scheduler and GitHub relay identities invoker access
on this service alone. Keep the owner-claims administrator and Rules auditor
on separate identities. Grant claim management only to the reviewed admin job.
The Rules auditor needs read-only Firestore access. Cloud IAM identities bypass
Firebase Security Rules. Verify effective grants independently.

For GitHub federation, pin the Google provider condition to the numeric
repository and owner IDs, the main ref, and the intended environment subject.
Separate the relay, operator, and audit identities. In `federated` mode,
select a generated external-account configuration and pin
`FIREBASE_FEDERATED_AUDIENCE` and `FIREBASE_FEDERATED_ACCOUNT`.
Reference the exact workflow for each identity in the provider condition.
Store no long-lived account key in the federation configuration. Check
effective access with an approved short-lived identity and document any
legacy workflow dependency before retiring a key.

## Staged rollout and rollback

1. Confirm the active workflow revision, production data destinations, deployed
   Rules, IAM and backup readiness through approved read-only inspection.
2. After staging budget approval, deploy the shadow service with IAM invoker
   enforcement and synthetic data. Compare check and email behavior using a
   mail sink. Rehearse promotion and rollback. Validate the latest catalogue
   refresh and authorize importing the discovery clock into the private
   `worker_runtime/discovery` document if required.
3. After production release approval, deploy the candidate privately with
   scheduling paused. Review the existing `bookmyshow-monitor.yml` inputs.
   Cutover must retain that workflow ID for the app. Wire its targeted manual
   route to the reusable `monitor-worker-relay.yml` and explicitly preserve
   the approved behavior for untargeted and dry-run invocations. The reusable
   workflow has no caller in this branch and needs an approved main-only
   `monitor-dispatch` environment and a Cloud Run invoker identity.
4. At the approved window, drain legacy worker jobs and address queued jobs and
   reruns of old revisions. Confirm their write and sender capabilities are
   unavailable and no invocation or SMTP command is in flight. A legacy job
   can run for up to 65 minutes. Expect a check gap during the handoff. Record
   start time and abort if the approved lag budget is exceeded.
5. Reconcile all sending or uncertain outbox rows. After separate approval,
   initialize `worker_runtime/control` with the named authority, positive
   monotonically increasing integer epoch, empty holder, and empty lease.
   Configure the new revision with `WORKER_AUTHORITY` and `WORKER_MODE=fenced`.
   Verify the target DB, active revision, authority, and legacy capability
   removal, then enable the schedule, relay and sender in the approved order.
6. Monitor scheduler failures and lag, successful checks, policy deferrals,
   interrupted or failed email, and the backlog's age. The service reports
   aggregate counts. An SMTP `accepted` row represents server acceptance.
   Inbox delivery requires separate evidence. Retry only when SMTP explicitly
   refused the transaction or failed before send. Investigate `uncertain`
   or stranded `sending` using the stable Message-ID, sender evidence, and
   timestamps. The scoped reconciliation method stores a digest of verified
   evidence without sending mail. Record the decision before any manual resend.
7. For rollback, pause scheduling and disable relay calls. Stop new sender
   claims, drain the active worker and reconcile in-flight messages. Replace
   the authority with a new epoch and select a known-good fenced revision.
   Test exclusive control before re-enabling checks. A previous unfenced CLI
   revision needs its own explicit release decision after state reconciliation
   and exclusive sender proof. Preserve the ledger and existing records. Review how long to retain manual
   dispatch receipts before adding automatic cleanup.

No production identity, environment gate, outbox cleanup policy or alerting
resource is created during repository review. Decide retention for recipient
digests, rendered mail, and reconciliation evidence before activation.
