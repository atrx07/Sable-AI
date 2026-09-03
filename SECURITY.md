# Sable security model

Sable is a local coding-agent runtime that gives an LLM access to a deliberately limited set of tools. Its security model is based on **runtime-enforced capabilities**, not on trusting the model to follow a prompt.

Sable is **not an operating-system sandbox**. Treat repositories and commands that execute repository code as potentially hostile.

## Trust boundaries

Sable treats these as untrusted data:

- repository files and READMEs
- source comments and generated text
- test/build output
- Git output
- tool output
- content copied into the active workspace

Instructions found inside those sources do not have authority over Sable's system policy or the user's request.

## Runtime boundaries

### Workspace confinement

Sable's native file tools resolve paths against one workspace root and reject:

- parent traversal outside the workspace
- absolute paths outside the workspace
- symlink escapes outside the workspace
- protected credential paths such as `.env`, `.ssh`, and `.sable`

### Permission modes

- `plan` — inspection only
- `build` — workspace edits plus allow-listed command execution
- `yolo` — explicitly enables high-risk tools such as raw shell, delete and remote Git operations

`yolo` is a compatibility name and should be understood as **trusted/high-risk local execution**, not as a security boundary.

### Sequential tool execution

The runtime executes at most one real model-requested tool action per model turn. If a model response contains multiple tool calls, later calls are returned as deferred results and must be reconsidered after the first result is observed.

A separate configurable tool-call budget limits the number of real tool actions in one agent run.

### Command execution

Normal `run_command` execution uses argument arrays with `shell=False`. In `plan`/`build` modes, the policy layer rejects non-allow-listed executables, shell syntax, parent/absolute path arguments, package installation and dedicated high-risk operations.

Build/verification commands also run with common inherited credential environment variables stripped, including typical API tokens, private keys, SSH agent sockets and cloud credentials.

**Important limitation:** project code still runs with the operating-system permissions of the Sable process. A Python test or build script can perform arbitrary actions permitted to that OS user unless an external sandbox/container is used. Environment stripping is defense in depth, not process isolation.

### Git safety

Sable does not store GitHub personal access tokens. Git uses the user's normal ambient Git/SSH configuration.

The agent-facing tool catalogue does not expose `git add`; automatic staging is owned by the orchestrator. Before auto-commit, Sable checks for pre-existing staged user work. If staged changes already exist, auto-commit is skipped rather than risking an unrelated staged file being included in Sable's commit.

Auto-push defaults to off and requires high-risk mode when enabled.

### Verification

Automatic and custom verification commands pass through the same runtime permission policy as agent-requested commands. Verification is deterministic: the model only receives real check output after a command has run.

### Transaction and rollback safety

Sable snapshots each file-tool target before its first mutation in a task. Snapshot paths pass the workspace and protected-path checks, nested protected paths and escaping symlinks are rejected, and storage is bounded. Snapshot data is stored in Sable's private control area and is not exposed through model tools.

Undo is conflict-aware: Sable restores a path only if its current fingerprint still matches the post-mutation state recorded by the transaction. Later user changes are preserved and reported as conflicts. This protects filesystem changes made through Sable's native file tools; arbitrary side effects produced by executed project code or shell commands are outside the transaction guarantee.

## Prompt-injection terminology

Sable is **prompt-injection hardened**, not prompt-injection proof.

The runtime prevents repository text from directly changing hard permission checks, but malicious repository content can still influence model decisions inside actions that the policy allows. Stronger process isolation and adversarial evaluation are active roadmap items.

## Non-goals / current limitations

Sable currently does not guarantee:

- OS-level filesystem isolation for executed project code
- network isolation for executed project code
- kernel/container isolation
- protection from every secret format
- semantic immunity to prompt injection
- transactional Git worktrees for every task

Those limitations should be preserved in public documentation until the corresponding runtime controls exist.

## Reporting a security issue

Please avoid publishing a working exploit against a sensitive real repository. Open a GitHub issue with a minimal reproduction that uses dummy credentials/data, or contact the repository owner privately when disclosure would expose real secrets.
