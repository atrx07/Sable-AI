# Troubleshooting and FAQ

Start with `sable doctor .` before reporting anything. It is offline and
read-only, it never prints a credential, and its `NOT READY` / `READY` result
plus its exit code (2 on a critical failure, 0 when ready) are the fastest
way to separate a configuration problem from a provider or task problem.

## `doctor` reports NOT READY: API key not configured

Sable needs a hosted provider key for live tasks. It does not ship one, and
there is no free built-in model.

| Provider (default: Groq) | Environment variable |
| --- | --- |
| Groq | `GROQ_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |

Set the variable, or run `sable .` and use `/keys`. Environment keys are
resolved at use time and are not copied into the saved config; keys entered
through `/keys` are stored as plaintext in the private local config by
documented design, so prefer the environment variable on shared machines.

To switch providers, run `/config` first (the provider prompt follows the two
model prompts), then `/keys`, then `/models`, then `/config` again if you need
to replace the main/fast model IDs with models your account actually offers.
OpenAI, Anthropic, and DeepSeek adapters are beta: they have mocked-test
coverage only and no live paid-API smoke tests yet, so a provider-specific
failure on those adapters is a real possibility — report it with the provider
name, model ID, HTTP status/error code, and a redacted response body, never
with an API key. See [model providers](providers.md).

## Verification ends INCOMPLETE, or doctor says "Python syntax: unavailable"

`INCOMPLETE` is not a pass, and it is not Sable painting a failure green. It
means a required check could not run — most commonly a missing tool.

- Check the `Verification` section of `sable doctor .`: it lists each
  discovered check and whether it is available.
- Python checks need a `python` or `python3` interpreter on PATH. Hosts with
  only `python3` and no `python` alias used to fail here; verification now
  falls back to `python3` automatically. If the check is still unavailable,
  install or symlink an interpreter on PATH and re-run `doctor`.
- A missing optional tool (for example Ruff when no Ruff configuration is
  present) does not fail doctor and does not block verification of the checks
  that are available.

## A task aborts with "rate-limited" or a provider error

This is a provider/quota failure (exit code 30), not a Sable code failure.
Wait for the quota window, then retry a smaller task. If the same task keeps
failing, try another configured provider key or provider, and run the task
first in `plan` mode (`sable run "…" . --mode plan`) to confirm the context
and plan are sound before spending live calls. `/usage` reports token counts
only; Sable has no pricing source and does not estimate currency cost.

A known instance of this pattern: a model repeatedly emitting an unsupported
patch dialect (for example a Codex-style `*** Begin Patch` wrapper) was
correctly rejected by Sable's patch validation, retried the same invalid
format, and eventually hit a rate limit before the task could recover. If you
see repeated `Invalid patch` failures, stop the task, retry with a smaller
edit, and prefer `patch_file` for a simple exact-text replacement over a
large hand-written diff. Sable deliberately does not auto-convert unsupported
patch dialects into unified diffs.

## `install.sh` refuses to run

`bash install.sh` is Termux-only and rejects non-Termux hosts before doing
anything. On Linux, macOS, or Windows, use the generic source installation
instead: clone, create a virtual environment, and run
`python -m pip install -e .`. See [platform support](platforms.md).

## `pip install sableai` fails

There is no public PyPI release yet, and no tag or GitHub Release is implied
by the `2.0.0` version in the source. Install from a source checkout or a
locally built wheel only, as described in the README quick start.

## `sable`, `sable .`, and `sable chat .` — which one?

- `sable .` and `sable chat .` open an interactive session in the current
  directory (or in the explicit path you give).
- `sable run "task" .` runs exactly one task and exits; add `--json` for the
  schema-versioned machine result.
- Bare `sable` is a legacy project-slot shell. Prefer the explicit forms
  above for new work. See [CLI usage](cli.md).

## Approvals, modes, and `yolo`

- `plan` is read-only: model-originated mutation cannot be approved in plan
  mode, even by an approval callback that would otherwise allow it.
- `build` (the default) allows workspace edits and restricted project
  processes; destructive, network, package, raw-shell, and publish actions
  are denied.
- `yolo` only makes elevated actions *requestable* through a scoped human
  approval. It never removes workspace or protected-path boundaries.
- An elevated prompt accepts `a` / `allow` / `once` (allow once),
  `s` / `session` (allow that exact action for this session), or
  `d` / `deny` / Enter (deny). Invalid input asks again and never approves;
  EOF and Ctrl+C deny. There is no approve-all flag, and in a one-shot run
  whose stdin is not a TTY, interactive approvals are disabled so the
  command cannot hang or grant an implicit capability.

## Undo reports a conflict / PARTIAL_ROLLBACK

That is the designed behaviour, not data loss. Undo restores a path only
when its current fingerprint still matches Sable's recorded post-state. If
you edited the file after Sable did, your later edit is preserved and
reported as a conflict instead of being overwritten. Undo changes the
working tree only; it never resets, cleans, checks out, or rewrites Git
history. Subprocess side effects outside the file-tool transaction are not
reversible by undo. See [transactions](transactions.md).

## No streaming output / no task resume

Both are current limitations, not bugs to work around:

- The CLI does not stream model tokens. Progress goes to stderr in `--json`
  mode; the final result is one JSON document on stdout.
- Sessions reopen summaries and traces after a restart; they do not resume
  an interrupted task, restore its model conversation, or restore capability
  grants. Transaction checkpoints support file recovery, not execution
  replay. See [sessions](sessions.md).

## FAQ

**Is Sable offline?** No. Tools, policy, transactions, and evidence run on
your machine, but model inference uses a hosted provider and sends selected
repository context to that provider. Local-first does not mean offline
inference.

**Is Sable a sandbox?** No. Native processes keep the OS user's permissions
and Termux/PRoot is best-effort filesystem remapping, not a kernel security
boundary. Use a separately configured container, VM, restricted OS account,
or kernel sandbox for genuinely hostile code. See [security](../SECURITY.md).

**Which languages work best?** Repository context is symbol/import/test
aware for Python (AST-based); other languages are profiled by extension and
manifest. Verification discovers checks per manifest across Python, Node,
Rust, Go, Java, and related toolchains, but Python verification is the most
complete. Affected-test selection and integrity checks are heuristics in
every language.

**What should I include in a bug report?** Sable/Python versions, platform,
installation method, execution backend, reproduction steps, and redacted
output. Do not attach real keys, private repository contents, config,
sessions, or traces without reviewing them. See [support](../SUPPORT.md).
