# Firebase backup and recovery drill

Firestore and Authentication are separate recovery streams. A managed Firestore
backup covers documents and indexes. Capture Security Rules, TTL configuration,
IAM grants, database location, Identity Platform provider and tenant settings,
and OAuth client configuration separately. A Firestore backup alone cannot
restore Firebase Authentication UIDs or provider links.

## Local drill

Run the pinned Firebase emulator suite. It exercises two signed-in users and
client denials for worker-only collections. The synthetic recovery test takes
an Auth and Firestore snapshot and replays it into another demo project.
It compares UIDs, operator claims, preferences, monitor IDs, stopped status,
observations and history, then checks a fixture password sign-in.

`python -m tools.export_auth_backup --project demo-ticketradar-source --output snapshot.json`
reads loopback demo emulators. Its Auth manifest contains identity metadata
suitable for comparison. The password and MFA recovery material needed for a
real backup has different permissions and storage requirements. The emulator
drill sets a synthetic password. The test supplies no evidence about
production Google sign-in or existing password hashes.

`python -m tools.verify_recovery --source before.json --restored after.json`
compares private snapshots with the exporter schema. It reports counts,
including missing and changed records and broken owner links. It rejects
live destinations and same-project comparisons. Keep manifests private and
outside source control. The command operates on files and performs no writes
to Firebase.

## Approvals before a production backup or restore

1. Inventory current backup schedules and retention, most recent successful
   backup, Rules release and checksum, database region, TTL, IAM, Auth account
   types and active identity providers. Use an approved read-only operator.
2. Decide RPO, RTO, cost, and retention. Review the proposed daily Firestore
   schedule with 14 days of retention, importing an existing schedule into
   management if appropriate. Review an independent daily encrypted Auth
   export and a dedicated exporter identity with read and destination object
   creation grants. Grant password-hash access only if required for the
   identified providers and recovery plan. Treat export files, salts and hash
   configuration as private credentials. Approve endpoint, bucket, encryption,
   retention, and restore principals separately.
3. Approve a distinct isolated restore target and budget. Restore Firestore
   into a new database using the selected backup. Apply the approved Rules and
   other configuration to the isolated target. Import Auth into the isolated
   project using original UIDs and provider links, with the approved private
   credentials. Configure OAuth for the isolated domain and leave monitoring
   and email disabled throughout the drill.
4. Compare manifests privately, verify sign-in and owner-isolation behavior
   with approved test identities, and verify monitor eligibility and history
   with provider fixtures. Record actual RPO and RTO and investigate any
   record or ownership change. Use the results for a separate production
   recovery decision. A production restore or backup deletion requires its
   own explicit approval.

[Taking and restoring Firestore backups](https://docs.cloud.google.com/firestore/native/docs/backups)
and [Firebase Auth export and import](https://firebase.google.com/docs/cli/auth)
describe the managed operations. This repository includes a disabled staging
Firestore schedule. The production backup service, Auth exporter, credentials,
and recovery actions need separate review and approval.
