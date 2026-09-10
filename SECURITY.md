# Sable security model

Sable is a local coding-agent runtime with runtime-enforced capability checks. It is not an operating-system sandbox, container, or privilege boundary. Treat repositories and any project code you execute as potentially hostile.

## Trust boundaries

Repository files, comments, generated text, build/test output, Git output, and tool output are untrusted data. Instructions in those sources cannot grant capabilities, approve requests, change the active mode, or override protected-path checks.

The runtime—not the model—creates approval request IDs and exact-action scope hashes. Approval decisions come only from the configured human callback. `ALLOW_ONCE` is consumed once; `ALLOW_SESSION` applies only to the same capability and exact action scope in the current in-memory session. Session grants are cleared on session change and are never loaded from trace or configuration files.

## Capability policy

Sable classifies tool actions using these capabilities:

- `READ_WORKSPACE`, `WRITE_WORKSPACE`, `DELETE_WORKSPACE`
- `EXECUTE_PROCESS`, `EXECUTE_SHELL`
- `NETWORK_ACCESS`, `PACKAGE_INSTALL`
- `GIT_REMOTE_READ`, `GIT_PUBLISH`, `GIT_HISTORY_MUTATION`
- `EXTERNAL_FILESYSTEM`

Action provenance is recorded as `MODEL`, `USER`, `VERIFIER`, or `RUNTIME`.

| Mode/source | Baseline | Elevated behavior |
|---|---|---|
| `plan` model | workspace and local-Git reads | mutation cannot be approved; plan is a hard ceiling |
| `build` model/verifier | workspace reads/writes and restricted direct processes | delete, raw shell, known network/package actions, and remote/publishing actions are denied |
| `yolo` model | same baseline | elevated actions are requestable and still require human approval |
| direct user action | the explicitly requested capability | redundant approval may be skipped, but workspace, protected-path, and command validation still apply |

`yolo` is a compatibility name for trusted/high-risk operation. It does not mean unrestricted execution and does not weaken the hard workspace or secret boundaries.

`EXTERNAL_FILESYSTEM` remains hard-denied. Approval cannot authorize file-tool path traversal, symlink escape, protected credential paths, workspace-root deletion, or model-supplied authorization metadata.

## Execution backends

`execution_backend` accepts `auto`, `native`, or `proot`:

- `auto` selects PRoot only when Termux, `proot`, and a caller-configured rootfs are available; otherwise it selects native execution.
- `native` explicitly uses the portable native backend.
- `proot` requires a supported Termux/PRoot environment and configured rootfs. It fails closed when unavailable and never silently falls back to native.

Sable never downloads a rootfs automatically. Use `/sandbox` (or `/execution`) to inspect the active backend and its machine-readable guarantee levels.

### Capability and isolation matrix

The first row is enforced by Sable's file-tool layer and is not a subprocess-backend property.

| Guarantee | Native | PRoot |
|---|---|---|
| Sable file tools confined to workspace | `ENFORCED` | `ENFORCED` |
| Project-process working directory validated inside workspace | `ENFORCED` at launch | `ENFORCED` at launch |
| Project-process workspace visibility | `NOT_SUPPORTED` | `BEST_EFFORT` path/root remapping |
| Private HOME for project/build processes | `ENFORCED` | `ENFORCED` |
| Sanitized project-process environment | `ENFORCED` | `ENFORCED` |
| Kernel filesystem namespace | `NOT_SUPPORTED` | `NOT_SUPPORTED` |
| Network isolation | `NOT_SUPPORTED` | `NOT_SUPPORTED` |
| Process namespace/isolation | `NOT_SUPPORTED` | `NOT_SUPPORTED` |
| Resource limits | POSIX `BEST_EFFORT`; Windows `NOT_SUPPORTED` | host POSIX `BEST_EFFORT` |
| Descendant cleanup on timeout | POSIX process group; Windows `BEST_EFFORT` | `BEST_EFFORT` through PRoot and host process group |
| Shell disabled by default | `ENFORCED` | `ENFORCED` |

`ENFORCED` means the implemented layer applies the control for the stated operation. `BEST_EFFORT` means the mechanism is useful but not a security boundary. `NOT_SUPPORTED` means Sable makes no enforcement claim.

### Native backend

Normal project commands use `shell=False`, a controlled workspace cwd, closed inherited file descriptors, bounded time/output, a fresh private HOME and temporary directory, and a centrally sanitized environment. POSIX launches get a separate session/process group and conservative CPU, file-size, and open-file limits when the host exposes them safely. On timeout, Sable requests graceful group termination and then bounded hard termination. Windows uses a new process group and `taskkill /T /F` as a best-effort fallback.

Native execution does not restrict which files the OS user can access and does not block sockets. A project program can still use the full filesystem and network permissions of the Sable process.

### Termux/PRoot backend

The PRoot backend maps a caller-provided rootfs, binds the workspace at `/workspace`, binds a private directory at `/home/sable`, sets a predictable in-root cwd/PATH, and launches with a sanitized environment. Only those host paths are explicitly bound by Sable.

PRoot is user-space path remapping, not a kernel-enforced sandbox. It provides no network namespace, process namespace, privilege separation, container-grade boundary, or proven defense against malicious escape. Filesystem remapping is therefore reported only as `BEST_EFFORT`.

## Environment and Git authentication

Project/build subprocesses do not inherit the user's real HOME. Sable removes exact known credential variables and conservatively filters secret-shaped names such as tokens, passwords, credentials, private keys, auth fields, CI secrets, SSH agent sockets, and common cloud/package configuration pointers. Necessary variables such as PATH and ordinary locale/runtime settings remain.

This filtering is defense in depth, not proof that every secret format is detected. Secrets embedded in ordinary-looking variables, files visible to the OS user, parent-process memory, or external services remain outside this guarantee.

Dedicated Git operations are intentionally separate: they use an explicit `AMBIENT` environment policy so the user's normal Git/SSH credential setup can work. Sable does not store GitHub PATs or rewrite remotes with tokens. Project subprocesses do not receive ambient Git authentication merely because the dedicated Git tool does.

Local Git inspection is baseline read access. Remote read operations require `NETWORK_ACCESS` plus `GIT_REMOTE_READ`; push requires `NETWORK_ACCESS` plus `GIT_PUBLISH`. The model cannot stage arbitrary files directly, auto-commit refuses pre-existing staged work, and auto-push defaults off.

## Network and package policy

Sable capability-gates known direct network commands and common package-manager operations without contacting the public network during classification. This is command-policy enforcement only. Aliases, wrappers, custom binaries, and arbitrary Python/Node/project code cannot be perfectly classified and may open sockets because neither native nor PRoot provides network isolation.

## Transactions are not process isolation

M2 transactions capture mutations made through Sable's file tools and provide bounded, conflict-aware rollback. A subprocess can modify workspace or external files outside that layer. M4 records process/security events, but it does not claim full subprocess filesystem rollback.

## Runtime trace security

Tasks emit bounded structured events including capability requested/approved/denied, backend selected, process started/completed/timeout/terminated, verification, transactions, and terminal outcome. Security events contain capability, provenance, decision class, non-secret scope category, backend guarantees, timing, and exit state.

Traces do not contain child environments, approval request IDs, internal scope hashes, API keys, or auth headers. Trace persistence is observational: a write failure is reported separately and never changes the task outcome. Malformed or forged trace/session data is ignored and is never an authorization source.

Structured termination reasons include `CAPABILITY_DENIED`, `BACKEND_UNAVAILABLE`, `SANDBOX_POLICY_BLOCKED`, and `PROCESS_TIMEOUT` in addition to existing runtime limits and verification outcomes.

## Remaining limitations

Sable does not guarantee:

- OS-level filesystem isolation for arbitrary subprocesses
- arbitrary subprocess network blocking
- kernel/container isolation or privilege separation
- a process namespace, including complete Windows descendant cleanup
- that PRoot confines malicious code as a security boundary
- detection of every network-capable command or secret representation
- transactional reversal of arbitrary subprocess side effects
- semantic immunity to prompt injection

Use an independently configured container, VM, restricted OS account, or kernel sandbox when executing genuinely untrusted code.

## Reporting a security issue

Avoid publishing a working exploit against a sensitive real repository. Open a GitHub issue with a minimal reproduction using dummy credentials/data, or contact the repository owner privately when disclosure would expose real secrets.
