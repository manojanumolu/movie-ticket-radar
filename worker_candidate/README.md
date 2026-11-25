# Monitoring candidate

The regular checkout is the live application's source layout. The legacy
monitor starts at `run_monitor.py` on its existing GitHub workflow. That
workflow uses the default-branch checkout, pulls the branch during its loop,
and dispatches the same workflow on the same ref for handover. The storage,
checker, providers, email, web entry point and existing workflow definitions
in the integration tree match the production baseline at
`1f4650104bc153a8bab794ddd82d55da8cb86e70`.

The candidate patch contains the reviewed edits to shared modules. The
overlay contains candidate-only files, including the relay definition.
The live checkout's Python imports and GitHub workflow discovery cannot
resolve these as root modules or workflows. Terraform remains an unapplied
staging template with its scheduler paused and backup creation opt-in.

To exercise the candidate, use a fresh path outside this checkout:

```text
python tools/materialize_worker_candidate.py <new-worktree-path>
```

Run tests, emulator commands or `docker build -f Dockerfile.worker .` from
that detached worktree. The materializer reads only committed candidate
content. It applies it solely inside the new worktree and changes neither
the originating checkout nor the source branch. The CI workflow tests the
production layout and the candidate layout separately, and builds the
candidate image in shadow mode.

## Consolidated release

The final release contains the effective changes from PRs #1 through #5 in a
single commit on `main`'s current baseline. Candidate code is already isolated
in that commit. Publish only the consolidated PR after approval. The original
drafts retain their review history and source branches.

The catalogue workflow retains its existing six-hour cron
(`0 1,7,13,19 * * *` UTC). The BookMyShow monitor retains its current
workflow, dispatch path, dependency lockfile, and executable code. Test CI is
the only changed workflow under `.github/workflows`. The worker image stays
in test CI, and production activation remains a separate release.

On 26 November 2026, the live Streamlit app's public context reported
`manojanumolu/movie-ticket-radar` and branch `main`. The public
[branch link](https://share.streamlit.io/manojanumolu/movie-ticket-radar/main/app.py)
also resolved to the live app. Streamlit Community Cloud synchronizes
the configured branch independently of GitHub Actions. Require separate
production approval before an integration updates `main`, even while
the application source and dependencies match the production baseline.
[Streamlit source updates](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/edit-your-app)


## Rollback after an approved release

1. Identify the consolidated release commit that landed on `main`. Review
   intervening commits and retain later catalogue and monitor state updates.
2. With rollback approval, create a revert PR against current `main` that
   reverses the single release commit. Verify the revert touches only the
   release paths. Run CI and merge the revert PR.
3. Streamlit synchronizes the revert from `main`. Verify the public sign-in
   page and the existing workflow schedule. The isolated candidate release
   creates no cloud resources and requires no data restore or worker cutover.
