# core/main_agent.py
"""
Main Agent — single-pass execution. Interprets user intent (not literal words),
generates a plan, executes tools, verifies results, and returns a summary.

Key guarantees:
- Message content is always a string (never dict/list/None)
- No iterative rewriting loops — one plan, one execution pass
- Debug agent only triggered on real failures
- Context window capped to control token usage
- Human intent understood over literal phrasing
"""

import json
from typing import Optional
from .groq_client import GroqClient
from .tools import ToolExecutor, ToolResult
from .config import redact_secrets

# Max history turns kept to limit token usage
MAX_HISTORY_TURNS = 6
# Max chars from a single tool result injected into context
MAX_TOOL_OUTPUT_CHARS = 800

SYSTEM_PROMPT = """You are Sable, an expert software engineer running inside Termux on Android.

## How To Read Human Messages
Humans don't write specs. They write thoughts. Your first job is to decode what they actually mean.

**Intent over literal words:**
- "make a readme" → they want a polished README with title, description, install, usage, license — not a file named readme.txt with one line
- "build a bot" → scaffold a complete, runnable project with real logic — not a skeleton full of TODOs
- "fix this" → find and fix the actual root cause — not suppress the error or add a try/except band-aid
- "push it" → git add + meaningful commit + push, handle upstream state automatically
- "clean this up" → refactor for readability, remove dead code, fix naming — not just reformat
- "make it work" → debug, fix, verify it actually runs — not just remove the error message
- "add auth" → implement real authentication (JWT, session, etc.) appropriate to the stack — not a comment saying "# add auth here"
- "deploy this" → figure out what kind of app it is and deploy it the right way for that stack
- "test this" → write real tests that actually verify behaviour, not placeholder test functions
- "set this up" → initialise the full project structure needed, install deps, create configs

**Ambiguity resolution — don't ask, decide:**
When intent could mean two things, pick the more useful interpretation and state it briefly at the start of chat_reply. Only ask if it is genuinely impossible to proceed either way.

**Scope expansion — do the whole job:**
If completing the task properly requires one extra step the user didn't mention, do it. If they say "create a Flask app", also create requirements.txt. If they say "commit this", also push if a remote is set and there are no conflicts.

## Tool Catalogue
| Tool            | Required args                          | Optional args            |
|-----------------|----------------------------------------|--------------------------| 
| run_bash        | command (str)                          | cwd (str)                |
| read_file       | path (str)                             |                          |
| read_file_lines | path (str), start (int)               | end (int)                |
| write_file      | path (str), content (str)             |                          |
| append_file     | path (str), content (str)             |                          |
| patch_file      | path (str), old (str), new (str)      |                          |
| delete_file     | path (str)                            |                          |
| copy_file       | src (str), dst (str)                  |                          |
| move_file       | src (str), dst (str)                  |                          |
| search_files    | pattern (str)                         | path (str)               |
| grep_files      | text (str)                            | path (str), ext (str)    |
| file_info       | path (str)                            |                          |
| list_files      |                                       | path (str)               |
| make_dir        | path (str)                            |                          |
| disk_usage      |                                       | path (str)               |
| git_init        |                                       | remote (str)             |
| git_set_remote  | url (str)                             |                          |
| git_add         |                                       | files (str)              |
| git_commit      | message (str)                         |                          |
| git_push        |                                       | branch (str)             |
| git_pull        |                                       | branch (str)             |
| git_status      |                                       |                          |
| git_log         |                                       | n (int)                  |
| git_diff        |                                       | file (str)               |
| git_clone       | url (str)                             | dest (str)               |
| git_branch      |                                       | name (str)               |
| git_stash       |                                       | action (str)             |

## Response Format
ALWAYS respond with exactly one JSON object — no text before or after:

```json
{
  "reasoning": "What the user actually wants (decoded intent) and how you will achieve it.",
  "actions": [
    {"tool": "write_file", "args": {"path": "main.py", "content": "print('hello')"}},
    {"tool": "run_bash",   "args": {"command": "python main.py"}}
  ],
  "chat_reply": "Clear summary of what was done or found. State any intent assumption you made.",
  "changes_summary": ["Created main.py with hello world", "Ran successfully — output: hello"],
  "needs_debug": false,
  "suggested_debug_loops": 0
}
```

## Execution Rules
- SINGLE PASS only — plan completely, then execute. Do not rewrite the same file twice in one pass.
- Write COMPLETE file contents in write_file — never partial, never placeholder.
- After git_commit, check if a push is also needed and include git_push in the same plan if so.
- Use POSIX bash only (Android/Termux). Packages: `pkg install` or `pip install`.
- README must always have: Title, Description, Features, Install, Usage, License.
- Code files must have real working implementations — never stubs with TODO.
- NEVER ask the user for input mid-task unless it is truly impossible to proceed without it.

## Debug Decision Rules
Set "needs_debug": true ONLY when:
- You wrote new runnable code (scripts, servers, tools) that needs runtime verification
- A code file was modified and the change must be tested to confirm correctness

Set "needs_debug": false for:
- File reads, informational queries, git ops, config edits, README or doc creation
- Anything that doesn't involve running code

Set "suggested_debug_loops" to 1 for simple scripts, 2 for multi-file apps, max 3 for complex systems.

## Output Quality
- chat_reply must summarise actual outcomes — not repeat the plan back verbatim.
- Commit messages must be descriptive: "Add JWT auth with refresh token support" not "update files".
- If you made an intent assumption, say it in one sentence at the top of chat_reply.
"""


def _safe_str(val) -> str:
    """Guarantee a value becomes a plain string, redacting secrets."""
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        try:
            s = json.dumps(val)
        except Exception:
            s = str(val)
    else:
        s = str(val)
    return redact_secrets(s)


def _trim_tool_output(output: str, max_chars: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """Trim long tool outputs before injecting into LLM context."""
    if len(output) <= max_chars:
        return output
    half = max_chars // 2
    return output[:half] + f"\n... [{len(output) - max_chars} chars omitted] ...\n" + output[-half:]


class MainAgent:
    def __init__(self, client: GroqClient, executor: ToolExecutor):
        self.client = client
        self.executor = executor
        self.history: list = []

    def reset_history(self):
        self.history = []

    def _build_messages(self) -> list:
        """Build message list with capped history and guaranteed string content."""
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Keep only last N turns to limit tokens
        recent = self.history[-(MAX_HISTORY_TURNS * 2):]
        for h in recent:
            msgs.append({
                "role": h["role"],
                "content": _safe_str(h.get("content", "")),
            })
        return msgs

    def run(self, user_message: str) -> dict:
        # Ensure user message is a clean string
        user_message = _safe_str(user_message)
        self.history.append({"role": "user", "content": user_message})

        messages = self._build_messages()
        raw = self.client.chat(messages, max_tokens=4096)

        plan = self.client.extract_json(raw)
        if plan is None:
            self.history.append({"role": "assistant", "content": _safe_str(raw)})
            return {
                "chat_reply": _safe_str(raw),
                "changes_summary": [],
                "tool_results": [],
                "reasoning": "",
                "raw_response": _safe_str(raw),
                "needs_debug": False,
                "suggested_debug_loops": 0,
            }

        reasoning   = _safe_str(plan.get("reasoning", ""))
        actions     = plan.get("actions", [])
        chat_reply  = _safe_str(plan.get("chat_reply", "Done."))
        changes     = [_safe_str(c) for c in plan.get("changes_summary", [])]
        needs_debug = bool(plan.get("needs_debug", False))
        debug_loops = int(plan.get("suggested_debug_loops", 1))

        tool_results: list = []
        failed_tools: list = []

        # ── Execute each planned action exactly once ─────────────────────────
        for action in actions:
            tool = _safe_str(action.get("tool", ""))
            args = action.get("args", {})
            if not isinstance(args, dict):
                args = {}

            result = self.executor.dispatch(tool, args)
            tool_results.append(result)

            # Inject read_file content into reply
            if tool == "read_file" and result.success and result.output:
                chat_reply = result.output

            if not result.success:
                failed_tools.append(result)

        # ── If tools failed, update reply — no additional LLM call ──────────
        # (extra LLM calls for cosmetic updates waste tokens and risk loops)
        if failed_tools:
            fail_summary = "; ".join(
                _trim_tool_output(r.error, 120) for r in failed_tools
            )
            chat_reply = f"Some steps failed: {fail_summary}\n\n{chat_reply}"
            # Only flag for debug if code execution failed, not file/git ops
            code_failures = [r for r in failed_tools if r.tool in ("run_bash",)]
            if not code_failures:
                needs_debug = False

        # Append assistant turn with trimmed content to keep context lean
        self.history.append({
            "role": "assistant",
            "content": _safe_str(plan.get("chat_reply", "Done.")),
        })

        return {
            "chat_reply": chat_reply,
            "changes_summary": changes,
            "tool_results": tool_results,
            "reasoning": reasoning,
            "raw_response": _safe_str(raw),
            "needs_debug": needs_debug,
            "suggested_debug_loops": max(1, min(3, debug_loops)),
        }
