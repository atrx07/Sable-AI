# Sable task transactions

Sable's transaction layer makes native file-tool changes inspectable and reversible without relying on Git. It is deterministic runtime infrastructure; the model does not decide how rollback works.

## Lifecycle

1. The orchestrator opens a transaction before the agent runs.
2. Git metadata is captured when the workspace is inside a repository: repository root, HEAD, branch, staged paths, unstaged paths, and untracked paths.
3. Before the first mutation of each path, Sable stores its pre-task state once.
4. Successful mutations record the resulting path fingerprint and action metadata.
5. The orchestrator creates bounded checkpoints after initial edits and repair passes.
6. Verification results are reduced to safe status metadata and attached to the transaction.
7. Successful tasks complete; permanently failing verification produces a `FAILED` but rollback-eligible transaction.
8. Unexpected orchestration failures attempt a conflict-aware rollback and report whether it was complete or partial.

Transaction states are `OPEN`, `VERIFIED`, `COMPLETED`, `FAILED`, `ROLLED_BACK`, `PARTIAL_ROLLBACK`, and `ABORTED`.

## Snapshots and storage

Snapshots include only paths touched by Sable file tools. Existing files and directories are copied with their useful mode metadata; created paths are recorded as previously missing; deleted paths retain their prior data. Symlinks are preserved rather than dereferenced.

The default limits are:

- 8 MiB per snapshotted file
- 25 MiB per transaction, including checkpoints
- 4,000 filesystem entries per captured tree
- 4 checkpoints per transaction
- 10 retained transactions
- 100 MiB total retained transaction storage

The primary storage location is `~/.sable/transactions/<workspace-id>/`. If that control directory is not writable, Sable uses a workspace-keyed directory under the system temporary location. Metadata writes are atomic. Task summaries and verification summaries pass through secret redaction; protected paths are rejected before snapshotting.

## Conflict-aware rollback

Each baseline snapshot stores a digest of the pre-Sable state. After mutation, Sable stores a digest of the state it produced. Undo compares the current state with those digests:

- current equals Sable's post-state: restore the baseline
- current already equals the baseline: report it as already restored
- current matches neither: preserve it and report a conflict

Safe paths can still be restored when another path conflicts, producing `PARTIAL_ROLLBACK`. The result lists restored paths, removed task-created paths, recreated task-deleted paths, conflicts, and errors.

Files already staged, unstaged, or untracked at task start are marked conflict-sensitive. Their snapshot still contains the user's pre-Sable state, so immediate undo can safely restore it when the post-state fingerprint proves no later change occurred. Auto-commit is skipped for these paths because path-level staging could absorb unrelated user hunks.

## Commands

```text
/txn
/txn list
/txn show <transaction-id>
/undo
/undo --dry-run
/undo <transaction-id>
```

Transaction inspection reports metadata and changed paths, never snapshot contents. `/undo` changes the working tree only; it does not reset, clean, checkout, or rewrite Git history.

## Unified patches

The `apply_patch` model tool accepts conventional unified diffs for UTF-8 text files. It supports multiple files and hunks plus file creation, update, and deletion. Every target and hunk is validated before mutation. If a prepared multi-file operation encounters a runtime write failure, files already applied by that operation are restored from operation-local originals.

Renames, binary patches, quoted Git paths, and fuzzy/context-free application are intentionally unsupported. The model can use `move_file` for renames or a full-file write when exact unified-diff semantics are unsuitable.

## Limitations

- Native transactions cover Sable file tools, not arbitrary filesystem or network side effects from tests, builds, project scripts, or raw shell commands.
- Fingerprint comparison detects later changes but does not merge them.
- A storage-limit refusal can make a requested mutation unavailable rather than non-reversible.
- Transactions are not an OS sandbox and do not replace backups or version control.
- Temporary-location fallback persistence depends on the host's temporary-file retention policy.
