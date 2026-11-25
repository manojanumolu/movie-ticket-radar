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

## Integration order

The four original review branches retain their individual diffs. Their
earlier tips put candidate edits directly in shared runtime files. Bringing
those commits to the default branch separately would expose intermediate
trees before isolation landed. An authorized integration must publish the
final isolated tree to the default branch in a single update. Review this
branch against the production baseline for that operation.

Streamlit Community Cloud synchronizes its configured source branch
independently of GitHub Actions. Verify the live app's repository, branch and
entry point in the owner workspace before authorizing any default-branch
update. Even a runtime-identical commit could be reflected by its source
integration. This code does not change the deployment configuration.
