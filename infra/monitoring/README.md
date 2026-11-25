# Monitoring worker infrastructure

This configuration describes an isolated Cloud Run shadow service and a paused
Cloud Scheduler job. Cloud Run must enforce IAM invoker authentication. The
runtime service account has no data or mail permissions. The scheduler uses
an ID token with the service URL as its audience.

Provisioning and invocation require separate approval. Start by reviewing the
project, billing, API enablement, region, image digest and Terraform state
backend. Use a reviewed plan for the isolated project. Guard any future
production configuration in a separate state and review.

The current production workflow ID and its legacy scheduling are unchanged in
this branch. Expect one nominal 30-second slot and a possible second slot per
scheduled minute in the proposed runtime. Compare queueing, missing slots,
manual dispatch, and latency under realistic fixture workloads before adopting
this cadence.
