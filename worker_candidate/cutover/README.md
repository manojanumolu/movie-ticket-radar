# Staged cutover files (inactive)

Nothing here is read by GitHub Actions, Streamlit or the worker image.

| File | Becomes | When |
|---|---|---|
| `bookmyshow-monitor.yml` | `.github/workflows/bookmyshow-monitor.yml` | Stage 2 of the runbook, after the Cloud Run worker is live |
| `../overlay/.github/workflows/monitor-worker-relay.yml` | `.github/workflows/monitor-worker-relay.yml` | same PR |
| `test_release_isolation.py` | `tests/test_release_isolation.py` | same PR |

Applying them replaces the legacy GitHub monitoring engine with a relay that
only forwards a "Check now" for one monitor to the private worker. The app is
unchanged: it dispatches the same workflow file with the same inputs.

The cutover PR must also update `tests/test_release_isolation.py`, whose job
is to pin the legacy workflow until that deliberate release. Apply it after
this branch has been merged into `main`:

```bash
git switch -c cutover/relay-only origin/main
cp worker_candidate/cutover/bookmyshow-monitor.yml .github/workflows/bookmyshow-monitor.yml
cp worker_candidate/overlay/.github/workflows/monitor-worker-relay.yml .github/workflows/monitor-worker-relay.yml
cp worker_candidate/cutover/test_release_isolation.py tests/test_release_isolation.py
python -m pytest -q tests/test_release_isolation.py tests/test_cutover_staging.py
```
