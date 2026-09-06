# Sessions and runtime traces

Sable stores a small, workspace-keyed session record under
`~/.sable/sessions/<workspace-hash>/` (with a private temporary fallback when
the config directory is unavailable). The storage contains session metadata,
one bounded JSON file per retained task, and a redacted JSONL event trace.

Sessions resume after a normal CLI restart. `/session` prints the active
summary, `/session list` lists retained sessions, and `/session show <id>`
shows a selected summary. `/trace` prints the current session's compact event
timeline; `/trace <task-id>` filters it to one task.

Persistence is best-effort observability. A storage or serialization failure is
reported in the task's `trace_errors` field and never changes the coding task's
success, verification, rollback, or policy outcome. Values are recursively
bounded and passed through the same secret redaction rules used by the runtime.
Trace retention is bounded by event count and bytes; old sessions and task
summaries are pruned deterministically.
