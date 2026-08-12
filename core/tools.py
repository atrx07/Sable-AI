# core/tools.py
"""
All tools the agent can invoke — with advanced file & directory management.
Includes: secret-path blocking, secret redaction in outputs, verified writes,
commit-message scanning, and auto-push when branch is ahead.
"""

import os
import subprocess
import shutil
import glob
from typing import Optional
from .config import (
    load_git_creds, save_git_creds, build_authenticated_remote,
    is_blocked_path, redact_secrets, contains_secret,
)


class ToolResult:
    def __init__(self, tool: str, success: bool, output: str = "", error: str = ""):
        self.tool = tool
        self.success = success
        self.output = str(output)   # always a string — never None/dict/list
        self.error  = str(error)

    def __str__(self):
        if self.success:
            return f"[{self.tool}] OK\n{self.output}".strip()
        return f"[{self.tool}] ERROR\n{self.error}".strip()

    def to_dict(self):
        return {"tool": self.tool, "success": self.success,
                "output": self.output, "error": self.error}


class ToolExecutor:
    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        os.makedirs(project_dir, exist_ok=True)

    # ── Bash ──────────────────────────────────────────────────────────────────
    def run_bash(self, command: str, cwd: Optional[str] = None) -> ToolResult:
        cwd = cwd or self.project_dir
        try:
            result = subprocess.run(
                command, shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=120
            )
            combined = redact_secrets((result.stdout + result.stderr).strip())
            if result.returncode == 0:
                return ToolResult("run_bash", True, output=combined)
            else:
                return ToolResult("run_bash", False, error=combined)
        except subprocess.TimeoutExpired:
            return ToolResult("run_bash", False, error="Command timed out after 120s")
        except Exception as e:
            return ToolResult("run_bash", False, error=str(e))

    # ── Path helpers ──────────────────────────────────────────────────────────
    def _abs(self, path: str) -> str:
        if os.path.isabs(path):
            return path
        return os.path.join(self.project_dir, path)

    def _rel(self, abs_path: str) -> str:
        try:
            return os.path.relpath(abs_path, self.project_dir)
        except ValueError:
            return abs_path

    # ── File read/write ───────────────────────────────────────────────────────
    def read_file(self, path: str) -> ToolResult:
        # Security: block sensitive paths
        if is_blocked_path(path):
            return ToolResult("read_file", False,
                              error=f"Access denied: '{path}' is a protected file.")
        try:
            abs_path = self._abs(path)
            if not os.path.exists(abs_path):
                return ToolResult("read_file", False, error=f"File not found: {path}")
            with open(abs_path, "r", errors="replace") as f:
                content = f.read()
            # Redact any secrets that slipped into a file
            content = redact_secrets(content)
            size = len(content)
            header = f"── {path} ({size} chars) ──\n"
            return ToolResult("read_file", True, output=header + content)
        except Exception as e:
            return ToolResult("read_file", False, error=str(e))

    def read_file_lines(self, path: str, start: int = 1, end: int = None) -> ToolResult:
        if is_blocked_path(path):
            return ToolResult("read_file_lines", False,
                              error=f"Access denied: '{path}' is a protected file.")
        try:
            abs_path = self._abs(path)
            with open(abs_path, "r", errors="replace") as f:
                lines = f.readlines()
            total = len(lines)
            s = max(0, start - 1)
            e = end if end else total
            selected = lines[s:e]
            numbered = "".join(f"{s+i+1:4d} │ {l}" for i, l in enumerate(selected))
            output = redact_secrets(f"Lines {start}–{min(e,total)} of {total}:\n{numbered}")
            return ToolResult("read_file_lines", True, output=output)
        except Exception as e:
            return ToolResult("read_file_lines", False, error=str(e))

    def write_file(self, path: str, content: str) -> ToolResult:
        """Write and VERIFY the file was actually written correctly."""
        # Never write tokens/keys into tracked files
        if contains_secret(content):
            return ToolResult("write_file", False,
                              error="Refused: content appears to contain secrets/tokens. "
                                    "Use environment variables or ~/.sable/ storage instead.")
        try:
            abs_path = self._abs(path)
            os.makedirs(os.path.dirname(abs_path) or abs_path, exist_ok=True)
            with open(abs_path, "w") as f:
                f.write(content)
            # ── Post-write verification ──────────────────────────────────────
            if not os.path.exists(abs_path):
                return ToolResult("write_file", False,
                                  error=f"Write appeared to succeed but file missing: {path}")
            actual_size = os.path.getsize(abs_path)
            if actual_size == 0 and len(content) > 0:
                return ToolResult("write_file", False,
                                  error=f"Write produced empty file: {path}")
            return ToolResult("write_file", True,
                              output=f"Written: {path} ({actual_size} bytes verified)")
        except Exception as e:
            return ToolResult("write_file", False, error=str(e))

    def append_file(self, path: str, content: str) -> ToolResult:
        if contains_secret(content):
            return ToolResult("append_file", False,
                              error="Refused: content appears to contain secrets/tokens.")
        try:
            abs_path = self._abs(path)
            os.makedirs(os.path.dirname(abs_path) or abs_path, exist_ok=True)
            before_size = os.path.getsize(abs_path) if os.path.exists(abs_path) else 0
            with open(abs_path, "a") as f:
                f.write(content)
            after_size = os.path.getsize(abs_path)
            if after_size <= before_size and len(content) > 0:
                return ToolResult("append_file", False,
                                  error=f"Append did not increase file size: {path}")
            return ToolResult("append_file", True, output=f"Appended to {path} ({after_size} bytes)")
        except Exception as e:
            return ToolResult("append_file", False, error=str(e))

    def patch_file(self, path: str, old_text: str, new_text: str) -> ToolResult:
        if contains_secret(new_text):
            return ToolResult("patch_file", False,
                              error="Refused: replacement text appears to contain secrets.")
        try:
            abs_path = self._abs(path)
            with open(abs_path, "r", errors="replace") as f:
                content = f.read()
            if old_text not in content:
                return ToolResult("patch_file", False,
                                  error=f"Text not found in {path}: {old_text[:60]!r}")
            new_content = content.replace(old_text, new_text, 1)
            with open(abs_path, "w") as f:
                f.write(new_content)
            # Verify patch applied
            with open(abs_path, "r", errors="replace") as f:
                verify = f.read()
            if old_text in verify and new_text not in verify:
                return ToolResult("patch_file", False,
                                  error=f"Patch verification failed: old text still present in {path}")
            return ToolResult("patch_file", True, output=f"Patched: {path}")
        except Exception as e:
            return ToolResult("patch_file", False, error=str(e))

    def delete_file(self, path: str) -> ToolResult:
        try:
            abs_path = self._abs(path)
            if os.path.isdir(abs_path):
                shutil.rmtree(abs_path)
                if os.path.exists(abs_path):
                    return ToolResult("delete_file", False,
                                      error=f"Failed to delete directory: {path}")
                return ToolResult("delete_file", True, output=f"Deleted directory: {path}")
            else:
                os.remove(abs_path)
                if os.path.exists(abs_path):
                    return ToolResult("delete_file", False,
                                      error=f"Failed to delete file: {path}")
                return ToolResult("delete_file", True, output=f"Deleted: {path}")
        except Exception as e:
            return ToolResult("delete_file", False, error=str(e))

    def copy_file(self, src: str, dst: str) -> ToolResult:
        try:
            abs_src = self._abs(src)
            abs_dst = self._abs(dst)
            os.makedirs(os.path.dirname(abs_dst) or abs_dst, exist_ok=True)
            if os.path.isdir(abs_src):
                shutil.copytree(abs_src, abs_dst, dirs_exist_ok=True)
            else:
                shutil.copy2(abs_src, abs_dst)
            if not os.path.exists(abs_dst):
                return ToolResult("copy_file", False, error=f"Copy failed: {dst} not found after copy")
            return ToolResult("copy_file", True, output=f"Copied: {src} → {dst}")
        except Exception as e:
            return ToolResult("copy_file", False, error=str(e))

    def move_file(self, src: str, dst: str) -> ToolResult:
        try:
            abs_src = self._abs(src)
            abs_dst = self._abs(dst)
            os.makedirs(os.path.dirname(abs_dst) or abs_dst, exist_ok=True)
            shutil.move(abs_src, abs_dst)
            if not os.path.exists(abs_dst):
                return ToolResult("move_file", False, error=f"Move failed: {dst} not found after move")
            return ToolResult("move_file", True, output=f"Moved: {src} → {dst}")
        except Exception as e:
            return ToolResult("move_file", False, error=str(e))

    def search_files(self, pattern: str, path: str = ".") -> ToolResult:
        try:
            abs_path = self._abs(path)
            matches = glob.glob(os.path.join(abs_path, "**", pattern), recursive=True)
            rel_matches = [self._rel(m) for m in sorted(matches)]
            if not rel_matches:
                return ToolResult("search_files", True, output=f"No files matching '{pattern}'")
            return ToolResult("search_files", True, output="\n".join(rel_matches))
        except Exception as e:
            return ToolResult("search_files", False, error=str(e))

    def grep_files(self, text: str, path: str = ".", ext: str = "") -> ToolResult:
        try:
            ext_flag = f"--include='*{ext}'" if ext else ""
            cmd = f"grep -rn {ext_flag} {text!r} ."
            result = self.run_bash(cmd, cwd=self._abs(path))
            return ToolResult("grep_files", result.success,
                              output=result.output or "(no matches)",
                              error=result.error)
        except Exception as e:
            return ToolResult("grep_files", False, error=str(e))

    def file_info(self, path: str) -> ToolResult:
        try:
            abs_path = self._abs(path)
            if not os.path.exists(abs_path):
                return ToolResult("file_info", False, error=f"Not found: {path}")
            import datetime
            stat = os.stat(abs_path)
            mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            if os.path.isdir(abs_path):
                count = sum(len(files) for _, _, files in os.walk(abs_path))
                info = f"Directory: {path}\nFiles: {count}\nModified: {mtime}"
            else:
                info = f"File: {path}\nSize: {stat.st_size} bytes\nModified: {mtime}"
            return ToolResult("file_info", True, output=info)
        except Exception as e:
            return ToolResult("file_info", False, error=str(e))

    # ── Directory management ──────────────────────────────────────────────────
    def list_files(self, path: str = ".") -> ToolResult:
        try:
            abs_path = self._abs(path)
            lines = []
            for root, dirs, files in os.walk(abs_path):
                dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")
                            and d not in ("node_modules", "__pycache__", "venv", ".git")]
                level = root.replace(abs_path, "").count(os.sep)
                indent = "  " * level
                folder_name = os.path.basename(root)
                lines.append(f"{indent}📁 {folder_name}/")
                subindent = "  " * (level + 1)
                for f in sorted(files):
                    fpath = os.path.join(root, f)
                    size = os.path.getsize(fpath)
                    size_str = f"{size}B" if size < 1024 else f"{size//1024}KB"
                    lines.append(f"{subindent}📄 {f} ({size_str})")
            return ToolResult("list_files", True, output="\n".join(lines))
        except Exception as e:
            return ToolResult("list_files", False, error=str(e))

    def make_dir(self, path: str) -> ToolResult:
        try:
            abs_path = self._abs(path)
            os.makedirs(abs_path, exist_ok=True)
            if not os.path.isdir(abs_path):
                return ToolResult("make_dir", False, error=f"Directory not created: {path}")
            return ToolResult("make_dir", True, output=f"Created directory: {path}")
        except Exception as e:
            return ToolResult("make_dir", False, error=str(e))

    def change_dir(self, path: str) -> ToolResult:
        try:
            abs_path = self._abs(path) if not os.path.isabs(path) else path
            if not os.path.isdir(abs_path):
                os.makedirs(abs_path, exist_ok=True)
            self.project_dir = abs_path
            return ToolResult("change_dir", True, output=f"Working dir: {abs_path}")
        except Exception as e:
            return ToolResult("change_dir", False, error=str(e))

    def disk_usage(self, path: str = ".") -> ToolResult:
        abs_path = self._abs(path)
        r = self.run_bash(f"du -sh {abs_path!r} 2>&1")
        return ToolResult("disk_usage", r.success, output=r.output, error=r.error)

    # ── Git ───────────────────────────────────────────────────────────────────
    def _git_get_remote(self) -> str:
        """Return the current origin remote URL, or empty string if none."""
        r = self.run_bash("git remote get-url origin 2>/dev/null", cwd=self.project_dir)
        return r.output.strip() if r.success else ""

    def _git_has_remote(self) -> bool:
        return bool(self._git_get_remote())

    def _git_current_branch(self) -> str:
        """Return the current branch name."""
        r = self.run_bash("git rev-parse --abbrev-ref HEAD 2>/dev/null", cwd=self.project_dir)
        return r.output.strip() if r.success and r.output.strip() else "main"

    def _git_try_self_heal(self, error: str, context: str = "") -> "tuple[bool, str]":
        """
        Attempt to self-heal a known git error.
        Returns (healed: bool, message: str).
        """
        err = (error or "").lower()

        # Nothing to commit — not a real failure
        if "nothing to commit" in err or "nothing added to commit" in err:
            return True, "Nothing new to commit — working tree clean."

        # Upstream not set
        if "set-upstream" in err or "no upstream" in err or "--set-upstream" in err:
            branch = self._git_current_branch()
            r = self.run_bash(f"git push --set-upstream origin {branch}", cwd=self.project_dir)
            if r.success:
                return True, f"Set upstream and pushed branch '{branch}'."
            return False, r.error

        # Non-fast-forward / diverged
        if "fetch first" in err or ("rejected" in err and "non-fast-forward" in err):
            branch = self._git_current_branch()
            r = self.run_bash(f"git pull --rebase origin {branch}", cwd=self.project_dir)
            if r.success:
                return True, "Rebased on remote changes — you can push again."
            return False, "Rebase failed — manual merge may be needed:\n" + r.output

        # Detached HEAD
        if "detached head" in err or "detached" in err:
            branch = "main"
            r = self.run_bash(
                f"git checkout -b {branch} 2>/dev/null || git checkout {branch}",
                cwd=self.project_dir
            )
            if r.success:
                return True, f"Reattached HEAD to branch '{branch}'."
            return False, r.error

        # Line-ending warning — not a real error
        if "lf will be replaced" in err or "crlf" in err:
            return True, "Line ending warning (non-fatal) — continuing."

        # User identity not configured
        if "user.email" in err or "user.name" in err or "please tell me who you are" in err:
            creds = load_git_creds()
            email = creds.get("email") or "sable@local"
            name  = creds.get("username") or "Sable"
            self.run_bash(f'git config user.email "{email}"', cwd=self.project_dir)
            self.run_bash(f'git config user.name "{name}"', cwd=self.project_dir)
            return True, f"Auto-configured git identity: {name} <{email}>. Retry your command."

        # Empty repo — src refspec doesn't match
        if "src refspec" in err and "does not match" in err:
            r = self.run_bash("git commit --allow-empty -m 'Initial commit'", cwd=self.project_dir)
            if r.success:
                return True, "Created initial commit so remote branch can be pushed."
            return False, r.error

        # Auth failure
        if ("authentication failed" in err or "could not read username" in err
                or "403" in err or "401" in err):
            return False, (
                "Authentication failed. Run /git creds to update your GitHub "
                "username and PAT token, then try again."
            )

        return False, ""  # Unknown error — no self-heal available

    def git_init(self, remote: "Optional[str]" = None) -> ToolResult:
        r = self.run_bash("git init", cwd=self.project_dir)
        if not r.success:
            return r
        creds = load_git_creds()
        email = creds.get("email") or "sable@local"
        name  = creds.get("username") or "Sable"
        self.run_bash(f'git config user.email "{email}"', cwd=self.project_dir)
        self.run_bash(f'git config user.name "{name}"', cwd=self.project_dir)
        if remote:
            auth_remote = build_authenticated_remote(remote, creds)
            self.run_bash(f"git remote add origin {auth_remote}", cwd=self.project_dir)
        return ToolResult("git_init", True, output="Git repo initialised")

    def git_set_remote(self, url: str) -> ToolResult:
        """Set or update origin remote URL (with credentials embedded)."""
        creds = load_git_creds()
        auth_url = build_authenticated_remote(url, creds)
        r = self.run_bash(f"git remote set-url origin {auth_url}", cwd=self.project_dir)
        if not r.success:
            r = self.run_bash(f"git remote add origin {auth_url}", cwd=self.project_dir)
        if r.success:
            return ToolResult("git_set_remote", True, output=f"Remote origin set to: {url}")
        return ToolResult("git_set_remote", False, error=r.error)

    def git_add(self, files: str = ".") -> ToolResult:
        return self.run_bash(f"git add {files}", cwd=self.project_dir)

    def git_commit(self, message: str) -> ToolResult:
        """Commit with secret scanning on message + diff. Self-heals known errors."""
        if contains_secret(message):
            return ToolResult("git_commit", False,
                              error="Commit aborted: message appears to contain secrets/tokens.")

        diff_r = self.run_bash("git diff --cached", cwd=self.project_dir)
        raw_diff = diff_r.output + diff_r.error
        if contains_secret(raw_diff):
            return ToolResult("git_commit", False,
                              error="Commit aborted: staged diff contains secrets/tokens. "
                                    "Remove secrets from files before committing.")

        safe_msg = message.replace('"', '\\"')
        r = self.run_bash(f'git commit -m "{safe_msg}"', cwd=self.project_dir)

        if not r.success:
            healed, heal_msg = self._git_try_self_heal(r.error or r.output, "commit")
            if healed:
                return ToolResult("git_commit", True, output=heal_msg)
            return ToolResult("git_commit", False, error=(r.error or r.output))

        log_r = self.run_bash("git log --oneline -1", cwd=self.project_dir)
        if not log_r.success or not log_r.output.strip():
            return ToolResult("git_commit", False,
                              error="Commit command ran but no commit found in log.")
        return ToolResult("git_commit", True, output=f"Committed: {log_r.output.strip()}")

    def git_push(self, branch: str = "") -> ToolResult:
        """
        Push to remote. Auto-detects branch, embeds creds, self-heals common errors.
        Returns a special sentinel if remote is missing (CLI layer handles the prompt).
        """
        branch = branch or self._git_current_branch()
        creds = load_git_creds()
        remote_url = self._git_get_remote()

        if not remote_url:
            # Signal to caller that remote is missing
            return ToolResult("git_push", False,
                              error="__NO_REMOTE__")

        if creds.get("token") and "@" not in remote_url:
            auth_url = build_authenticated_remote(remote_url, creds)
            self.run_bash(f"git remote set-url origin {auth_url}", cwd=self.project_dir)

        r = self.run_bash(f"git push -u origin {branch}", cwd=self.project_dir)

        # Restore clean URL after push
        if "@" not in remote_url:
            self.run_bash(f"git remote set-url origin {remote_url}", cwd=self.project_dir)

        if not r.success:
            healed, heal_msg = self._git_try_self_heal(r.error or r.output, "push")
            if healed:
                return ToolResult("git_push", True, output=heal_msg)
            return ToolResult("git_push", False, error=(r.error or r.output))

        # Verify
        status_r = self.run_bash(
            f"git rev-list --count origin/{branch}..HEAD 2>/dev/null || echo 0",
            cwd=self.project_dir
        )
        ahead = status_r.output.strip()
        if ahead not in ("0", ""):
            healed, heal_msg = self._git_try_self_heal("set-upstream", "push")
            if healed:
                return ToolResult("git_push", True, output=heal_msg)
            return ToolResult("git_push", False,
                              error=f"Branch still {ahead} commit(s) ahead after push.")
        return ToolResult("git_push", True, output=r.output or "Pushed successfully.")

    def git_pull(self, branch: str = "") -> ToolResult:
        """Pull from remote with self-healing."""
        branch = branch or self._git_current_branch()
        if not self._git_has_remote():
            return ToolResult("git_pull", False, error="__NO_REMOTE__")
        r = self.run_bash(f"git pull origin {branch}", cwd=self.project_dir)
        if not r.success:
            healed, heal_msg = self._git_try_self_heal(r.error or r.output, "pull")
            if healed:
                return ToolResult("git_pull", True, output=heal_msg)
            return ToolResult("git_pull", False, error=r.error or r.output)
        return ToolResult("git_pull", True, output=r.output or "Pulled successfully.")

    def git_status(self) -> ToolResult:
        return self.run_bash("git status --short", cwd=self.project_dir)

    def git_log(self, n: int = 10) -> ToolResult:
        return self.run_bash(
            f"git log --oneline --decorate --graph -n {n}", cwd=self.project_dir
        )

    def git_diff(self, file: str = "") -> ToolResult:
        cmd = f"git diff {file}".strip()
        r = self.run_bash(cmd, cwd=self.project_dir)
        return ToolResult("git_diff", r.success,
                          output=redact_secrets(r.output),
                          error=r.error)

    def git_clone(self, url: str, dest: str = "") -> ToolResult:
        creds = load_git_creds()
        auth_url = build_authenticated_remote(url, creds)
        cmd = f"git clone {auth_url} {dest}".strip() if dest else f"git clone {auth_url}"
        r = self.run_bash(cmd, cwd=self.project_dir)
        if r.success:
            expected = os.path.join(
                self.project_dir,
                dest or url.rstrip("/").split("/")[-1].replace(".git", "")
            )
            if not os.path.isdir(expected):
                return ToolResult("git_clone", False,
                                  error=f"Clone reported success but destination not found: {expected}")
        return r

    def git_branch(self, name: str = "") -> ToolResult:
        """Create and switch to a branch, or list branches."""
        if name:
            r = self.run_bash(
                f"git checkout -b {name} 2>/dev/null || git checkout {name}",
                cwd=self.project_dir
            )
            if r.success:
                return ToolResult("git_branch", True, output=f"Switched to branch '{name}'.")
            healed, heal_msg = self._git_try_self_heal(r.error or r.output)
            if healed:
                return ToolResult("git_branch", True, output=heal_msg)
            return ToolResult("git_branch", False, error=r.error)
        r = self.run_bash("git branch -a", cwd=self.project_dir)
        return ToolResult("git_branch", r.success, output=r.output or "(no branches)", error=r.error)

    def git_stash(self, action: str = "push") -> ToolResult:
        """Stash or pop changes."""
        if action in ("pop", "apply"):
            r = self.run_bash("git stash pop", cwd=self.project_dir)
        else:
            r = self.run_bash("git stash push -m 'sable stash'", cwd=self.project_dir)
        if not r.success:
            healed, heal_msg = self._git_try_self_heal(r.error or r.output)
            if healed:
                return ToolResult("git_stash", True, output=heal_msg)
            return ToolResult("git_stash", False, error=r.error)
        return ToolResult("git_stash", True, output=r.output or "Stash operation done.")

    def git_ahead_count(self, branch: str = "") -> int:
        """Return how many commits HEAD is ahead of origin/branch. 0 if unknown."""
        branch = branch or self._git_current_branch()
        r = self.run_bash(
            f"git rev-list --count origin/{branch}..HEAD 2>/dev/null || echo 0",
            cwd=self.project_dir
        )
        try:
            return int(r.output.strip())
        except (ValueError, AttributeError):
            return 0

        r = self.run_bash("git init", cwd=self.project_dir)
        if not r.success:
            return r
        creds = load_git_creds()
        email = creds.get("email") or "sable@local"
        name  = creds.get("username") or "Sable"
        self.run_bash(f'git config user.email "{email}"', cwd=self.project_dir)
        self.run_bash(f'git config user.name "{name}"', cwd=self.project_dir)
        if remote:
            auth_remote = build_authenticated_remote(remote, creds)
            self.run_bash(f"git remote add origin {auth_remote}", cwd=self.project_dir)
        return ToolResult("git_init", True, output="Git repo initialised")

    def git_add(self, files: str = ".") -> ToolResult:
        return self.run_bash(f"git add {files}", cwd=self.project_dir)

    def git_commit(self, message: str) -> ToolResult:
        """Commit with secret scanning on message + diff."""
        # Scan commit message for secrets
        if contains_secret(message):
            return ToolResult("git_commit", False,
                              error="Commit aborted: message appears to contain secrets/tokens.")

        # Scan staged diff for secrets before committing
        diff_r = self.run_bash("git diff --cached", cwd=self.project_dir)
        raw_diff = diff_r.output + diff_r.error
        if contains_secret(raw_diff):
            return ToolResult("git_commit", False,
                              error="Commit aborted: staged diff contains secrets/tokens. "
                                    "Remove secrets from files before committing.")

        safe_msg = message.replace('"', '\\"')
        r = self.run_bash(f'git commit -m "{safe_msg}"', cwd=self.project_dir)
        if not r.success:
            return r

        # Verify: commit actually created
        log_r = self.run_bash("git log --oneline -1", cwd=self.project_dir)
        if not log_r.success or not log_r.output.strip():
            return ToolResult("git_commit", False,
                              error="Commit command ran but no commit found in log.")
        return ToolResult("git_commit", True,
                          output=f"Committed: {log_r.output.strip()}")

    def git_push(self, branch: str = "main") -> ToolResult:
        """Push with stored credentials. Auto-push if ahead of remote."""
        creds = load_git_creds()
        remote_r = self.run_bash("git remote get-url origin 2>/dev/null", cwd=self.project_dir)
        remote_url = remote_r.output.strip()

        if remote_url and creds.get("token") and "@" not in remote_url:
            auth_url = build_authenticated_remote(remote_url, creds)
            self.run_bash(f"git remote set-url origin {auth_url}", cwd=self.project_dir)

        r = self.run_bash(f"git push -u origin {branch}", cwd=self.project_dir)

        # Restore clean URL after push
        if remote_url and "@" not in remote_url:
            self.run_bash(f"git remote set-url origin {remote_url}", cwd=self.project_dir)

        if not r.success:
            return r

        # Verify push by checking ahead/behind status
        status_r = self.run_bash(
            f"git rev-list --count origin/{branch}..HEAD 2>/dev/null || echo 0",
            cwd=self.project_dir
        )
        ahead = status_r.output.strip()
        if ahead not in ("0", ""):
            return ToolResult("git_push", False,
                              error=f"Push appeared to succeed but branch still {ahead} commit(s) ahead of remote.")
        return ToolResult("git_push", True, output=r.output or "Pushed successfully.")

    def git_status(self) -> ToolResult:
        return self.run_bash("git status --short", cwd=self.project_dir)

    def git_log(self, n: int = 10) -> ToolResult:
        return self.run_bash(f"git log --oneline -n {n}", cwd=self.project_dir)

    def git_diff(self, file: str = "") -> ToolResult:
        cmd = f"git diff {file}".strip()
        r = self.run_bash(cmd, cwd=self.project_dir)
        # Redact secrets from diffs before displaying
        return ToolResult("git_diff", r.success,
                          output=redact_secrets(r.output),
                          error=r.error)

    def git_clone(self, url: str, dest: str = "") -> ToolResult:
        creds = load_git_creds()
        auth_url = build_authenticated_remote(url, creds)
        cmd = f"git clone {auth_url} {dest}".strip() if dest else f"git clone {auth_url}"
        r = self.run_bash(cmd, cwd=self.project_dir)
        # Check destination exists
        if r.success:
            expected = os.path.join(self.project_dir, dest or url.rstrip("/").split("/")[-1].replace(".git", ""))
            if not os.path.isdir(expected):
                return ToolResult("git_clone", False,
                                  error=f"Clone reported success but destination not found: {expected}")
        return r

    def git_ahead_count(self, branch: str = "main") -> int:
        """Return how many commits HEAD is ahead of origin/branch. 0 if unknown."""
        r = self.run_bash(
            f"git rev-list --count origin/{branch}..HEAD 2>/dev/null || echo 0",
            cwd=self.project_dir
        )
        try:
            return int(r.output.strip())
        except (ValueError, AttributeError):
            return 0

    # ── Dispatch ──────────────────────────────────────────────────────────────
    def dispatch(self, tool_name: str, args: dict) -> ToolResult:
        mapping = {
            "run_bash":         lambda a: self.run_bash(a["command"], a.get("cwd")),
            "read_file":        lambda a: self.read_file(a["path"]),
            "read_file_lines":  lambda a: self.read_file_lines(a["path"], a.get("start", 1), a.get("end")),
            "write_file":       lambda a: self.write_file(a["path"], a["content"]),
            "append_file":      lambda a: self.append_file(a["path"], a["content"]),
            "patch_file":       lambda a: self.patch_file(a["path"], a["old"], a["new"]),
            "delete_file":      lambda a: self.delete_file(a["path"]),
            "copy_file":        lambda a: self.copy_file(a["src"], a["dst"]),
            "move_file":        lambda a: self.move_file(a["src"], a["dst"]),
            "search_files":     lambda a: self.search_files(a["pattern"], a.get("path", ".")),
            "grep_files":       lambda a: self.grep_files(a["text"], a.get("path", "."), a.get("ext", "")),
            "file_info":        lambda a: self.file_info(a["path"]),
            "list_files":       lambda a: self.list_files(a.get("path", ".")),
            "make_dir":         lambda a: self.make_dir(a["path"]),
            "change_dir":       lambda a: self.change_dir(a["path"]),
            "disk_usage":       lambda a: self.disk_usage(a.get("path", ".")),
            "git_init":         lambda a: self.git_init(a.get("remote")),
            "git_set_remote":   lambda a: self.git_set_remote(a["url"]),
            "git_add":          lambda a: self.git_add(a.get("files", ".")),
            "git_commit":       lambda a: self.git_commit(a["message"]),
            "git_push":         lambda a: self.git_push(a.get("branch", "")),
            "git_pull":         lambda a: self.git_pull(a.get("branch", "")),
            "git_status":       lambda a: self.git_status(),
            "git_log":          lambda a: self.git_log(a.get("n", 10)),
            "git_diff":         lambda a: self.git_diff(a.get("file", "")),
            "git_clone":        lambda a: self.git_clone(a["url"], a.get("dest", "")),
            "git_branch":       lambda a: self.git_branch(a.get("name", "")),
            "git_stash":        lambda a: self.git_stash(a.get("action", "push")),
        }
        fn = mapping.get(tool_name)
        if fn is None:
            return ToolResult(tool_name, False, error=f"Unknown tool: {tool_name}")
        try:
            result = fn(args)
            # Guarantee output/error are always strings
            result.output = str(result.output) if result.output is not None else ""
            result.error  = str(result.error)  if result.error  is not None else ""
            return result
        except KeyError as e:
            return ToolResult(tool_name, False, error=f"Missing required arg: {e}")
        except Exception as e:
            return ToolResult(tool_name, False, error=f"Tool execution error: {e}")
