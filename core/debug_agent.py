# core/debug_agent.py
"""
Debug Agent — triggered ONLY on actual runtime failures, never for refinement.
Reads project files, runs the project, checks against user intent,
and returns a structured fix prompt for the main agent.
"""

import json
import os
from .groq_client import GroqClient
from .tools import ToolExecutor
from .config import redact_secrets

# Cap snapshot size to avoid blowing the context window
MAX_SNAPSHOT_CHARS = 5000
MAX_FILE_CHARS = 2000
MAX_RUN_OUTPUT_CHARS = 1500

DEBUG_SYSTEM_PROMPT = """You are DebugAgent, a senior QA engineer.

You will receive:
- The original user intent
- A directory listing and key file contents
- The output/errors from running the project

Your job:
1. Determine if the project actually works and matches user intent.
2. List only REAL bugs and errors — do not suggest style improvements or refactoring.
3. If the project works, set status to "pass" immediately.
4. Produce a precise fix prompt with specific file names and changes needed.

Respond ONLY with a JSON object:

```json
{
  "status": "pass" | "fail" | "partial",
  "issues": ["Issue 1", "Issue 2"],
  "fix_prompt": "Specific instructions to fix all issues. Name files and exact changes.",
  "summary": "One sentence verdict."
}
```

Rules:
- "pass" = project runs and satisfies intent. Set issues=[] and fix_prompt="".
- "partial" = runs but has minor gaps in functionality.
- "fail" = crashes, missing files, or completely wrong output.
- DO NOT suggest improvements for passing code.
- Be direct and technical. The main agent acts on fix_prompt literally.
"""


class DebugAgent:
    def __init__(self, client: GroqClient, executor: ToolExecutor):
        self.client = client
        self.executor = executor

    def _collect_project_snapshot(self) -> str:
        snapshot_parts = []

        tree_result = self.executor.list_files(".")
        snapshot_parts.append(f"=== Directory Tree ===\n{tree_result.output}")

        READABLE_EXTS = {
            ".py", ".js", ".ts", ".jsx", ".tsx", ".sh", ".bash",
            ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini",
            ".html", ".css", ".md", ".txt",
        }
        # Explicitly exclude .env and secret files
        SKIP_FILES = {".env", ".env.local", ".env.production", "config.json",
                      "secrets.json", "credentials.json"}

        total_chars = 0
        for root, dirs, files in os.walk(self.executor.project_dir):
            dirs[:] = [d for d in dirs if d not in (
                "node_modules", "__pycache__", "venv", ".git", ".mypy_cache"
            ) and not d.startswith(".")]
            for fname in files:
                if fname in SKIP_FILES:
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext in READABLE_EXTS:
                    rel = os.path.relpath(os.path.join(root, fname), self.executor.project_dir)
                    r = self.executor.read_file(rel)
                    if r.success:
                        content = redact_secrets(r.output[:MAX_FILE_CHARS])
                        snapshot_parts.append(f"\n=== {rel} ===\n{content}")
                        total_chars += len(content)
                        if total_chars >= MAX_SNAPSHOT_CHARS:
                            snapshot_parts.append("\n[Snapshot truncated to save tokens]")
                            return "\n".join(snapshot_parts)

        return "\n".join(snapshot_parts)

    def _detect_run_command(self) -> str:
        project_dir = self.executor.project_dir
        candidates = [
            ("package.json", "npm start"),
            ("manage.py",    "python manage.py runserver --noreload &"),
            ("main.py",      "python main.py"),
            ("app.py",       "python app.py"),
            ("index.py",     "python index.py"),
            ("server.py",    "python server.py"),
            ("index.js",     "node index.js"),
            ("app.js",       "node app.js"),
            ("main.sh",      "bash main.sh"),
            ("Makefile",     "make"),
        ]
        for filename, cmd in candidates:
            if os.path.exists(os.path.join(project_dir, filename)):
                return cmd

        py_files = [f for f in os.listdir(project_dir) if f.endswith(".py")]
        if py_files:
            return f"python {py_files[0]}"

        return "echo 'No entry point detected'"

    def run(self, user_intent: str, run_command: str = None) -> dict:
        """
        Run the debug cycle.
        Only called when main agent reported a real execution failure.
        """
        cmd = run_command or self._detect_run_command()
        safe_cmd = f"timeout 30 {cmd} 2>&1 || true"
        run_result = self.executor.run_bash(safe_cmd)
        run_output = redact_secrets(
            (run_result.output or run_result.error or "(no output)")[:MAX_RUN_OUTPUT_CHARS]
        )

        snapshot = self._collect_project_snapshot()

        user_msg = f"""USER INTENT:
{user_intent}

PROJECT SNAPSHOT:
{snapshot[:MAX_SNAPSHOT_CHARS]}

RUN COMMAND: {cmd}
RUN OUTPUT:
{run_output}
"""
        messages = [
            {"role": "system", "content": DEBUG_SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ]
        raw = self.client.chat(messages, max_tokens=1500)
        result = self.client.extract_json(raw)

        if result is None:
            return {
                "status": "fail",
                "issues": ["Debug agent could not parse project output."],
                "fix_prompt": (
                    f"Project produced this output:\n{run_output}\n"
                    f"Fix any errors and ensure it matches: {user_intent}"
                ),
                "summary": "Debug model returned unparseable response.",
                "run_output": run_output,
            }

        result["run_output"] = run_output
        return result
