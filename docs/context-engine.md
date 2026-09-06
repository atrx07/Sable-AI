# Repository context engine

`sable/context` builds a bounded, deterministic repository view before the
agent's first reasoning turn. It reports the workspace profile, languages and
frameworks, package manifests, major directories, entry points, tests, Git
state, recent files/commits, a compact repository map, Python symbols, imports,
and likely test relationships.

The scanner excludes `.git`, `.sable`, virtual environments, build/cache
directories, protected credential paths, and symlinks that escape the workspace.
Python AST parsing is best-effort: syntax-invalid files are reported as context
errors rather than crashing the task. Selection is deterministic and bounded by
character budget, with filename/symbol/Git/recent matches first and bounded
import/test expansion afterward.

The read-only tools `repo_map`, `list_symbols`, `find_symbol`,
`find_references`, `read_symbol`, `find_tests_for_file`, and `recent_changes`
are available to the agent. A cached context snapshot invalidates when relevant
file metadata, the working directory, or Git HEAD changes.
