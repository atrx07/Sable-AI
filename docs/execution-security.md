# Execution security and capability lifecycle

Sable routes subprocess launches through `ExecutionBackend`. The current implementations are `NativeExecutionBackend` and the Termux-aware `ProotExecutionBackend`; `BackendGuarantees` reports each isolation property as `ENFORCED`, `BEST_EFFORT`, or `NOT_SUPPORTED`.

## Action lifecycle

1. `ToolExecutor` validates hard mode, command, workspace, and protected-path boundaries.
2. The runtime classifies the action into one or more capabilities.
3. Baseline capabilities proceed. Elevated model capabilities are denied in `plan`/`build` and become requestable in `yolo`.
4. The runtime creates an exact-action approval request. A human handler chooses allow once, allow for this exact action in the current session, or deny.
5. After authorization, the selected backend launches the process and normalizes output, timeout, exit, and guarantee metadata.
6. The runtime persists redacted security and process events. Authorization objects themselves are never recovered from persistence.

Repository content and model tool arguments are explanatory/untrusted input. They cannot create a valid approval, change provenance, or widen approval scope.

## Approval lifetime and scope

Allow-once is consumed by the request that received it. Session approval is keyed by session ID, capability, tool, and a digest of the relevant action arguments. A Git push grant does not approve raw shell; one delete target does not approve another. Session grants live only in the `ApprovalEngine` instance and are cleared when the session changes or the CLI restarts.

Direct slash commands have `USER` provenance and may treat the command itself as explicit intent. Hard validation still runs.

## Child environment

Normal project and verification commands receive a new private HOME and temp directory per execution. Sable strips known and secret-shaped environment names without logging their values. Dedicated Git operations opt into `AMBIENT` policy so existing Git/SSH authentication continues to work.

Environment filtering reduces accidental credential inheritance. It does not hide files from a native subprocess, remove secrets stored under ordinary variable names, or isolate the process from the OS user account.

## Backend selection

```json
{
  "execution_backend": "auto",
  "proot_rootfs": ""
}
```

`auto` prefers a usable Termux/PRoot configuration and otherwise uses native execution. `native` is always explicit. `proot` requires Termux, a `proot` executable, and an existing configured rootfs; absence is an error, not a native fallback. Sable never downloads a distribution automatically.

Use `/sandbox` to see the selected backend, availability reason, and every current guarantee level.

## Timeout, cleanup, and limits

The native backend uses a separate POSIX session/process group and terminates the group on timeout, escalating from TERM to KILL. On Windows it creates a process group and attempts CTRL_BREAK then `taskkill /T /F`. Windows tree cleanup remains best effort.

On POSIX, conservative CPU-time, file-size, and open-file limits are applied where the host supports them. They intentionally remain loose enough for ordinary builds. Process-count limits are omitted because reliable cross-platform semantics are not available here. Resource-limit status is reported rather than assumed.

Output is bounded at the backend and tool layers. Timeout, termination method, duration, exit code, environment policy, backend name, and guarantee levels flow into structured results/events.

## PRoot on Termux

PRoot launches inside a caller-supplied rootfs with the workspace mapped to `/workspace` and a private HOME mapped to `/home/sable`. This produces a controlled, predictable view for ordinary execution.

PRoot is not a kernel security boundary. Sable reports path remapping as best effort and explicitly reports filesystem namespaces, network isolation, and process isolation as unsupported. Do not use PRoot alone to execute hostile code that requires strong containment.

## Network limitation

The capability layer detects common commands such as curl/wget, Git remotes, and package installs. Denial stops those Sable-controlled invocations before launch. Arbitrary project programs and unrecognized wrappers can still use the network on both current backends.

## Transactions and observability

File-tool mutations remain covered by M2 snapshots and conflict-aware undo. Subprocess side effects are not transaction-aware. M4 records that a process ran and how it ended, but it does not claim to enumerate or reverse every file the process changed.

See [the security model](../SECURITY.md) for the backend matrix and threat-model summary.
