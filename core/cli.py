# core/cli.py
"""
CLI — interactive terminal interface for Sable.
Designed for Termux on Android.
"""

import os
import sys
try:
    import readline
except ImportError:
    pass
try:
    from getpass import getpass as _getpass
except ImportError:
    _getpass = input

from .config import (
    load_config, save_config, AVAILABLE_MODELS,
    get_active_key, rotate_to_next_key,
    load_git_creds, save_git_creds, build_authenticated_remote,
    reset_daily_tokens,
)
from .groq_client import GroqClient
from .tools import ToolExecutor
from .main_agent import MainAgent
from .debug_agent import DebugAgent
from .orchestrator import Orchestrator

# ── ANSI colours ──────────────────────────────────────────────────────────────
R   = "\033[0m"
B   = "\033[1m"
DIM = "\033[2m"
CYN = "\033[96m"
GRN = "\033[92m"
YLW = "\033[93m"
RED = "\033[91m"
MGT = "\033[95m"
BLU = "\033[94m"
WHT = "\033[97m"

ACCENT = "\033[38;5;154m"   # #e8ff6b-ish (bright yellow-green)

BANNER = f"""{ACCENT}{B}
    ╔══════════════════════════════════════════════╗
    ║                  S A B L E                   ║
    ╚══════════════════════════════════════════════╝{R}
{DIM}  Agentic AI coding assistant  ·  Termux Edition  ·  by atrx07{R}
"""

HELP_TEXT = f"""
{B}{ACCENT}Commands:{R}
  {CYN}/help{R}                  Show this help
  {CYN}/config{R}                View / change model settings
  {CYN}/keys{R}                  Manage Groq API keys (up to 3)
  {CYN}/keys use <1|2|3>{R}      Switch active key
  {CYN}/git creds{R}             Set GitHub username & token
  {CYN}/git creds show{R}        Show stored git credentials
  {CYN}/git init [url]{R}        Init git repo (optional remote)
  {CYN}/git remote <url>{R}      Set/update remote URL
  {CYN}/git push [branch]{R}     Push to remote (uses stored creds)
  {CYN}/git pull [branch]{R}     Pull from remote
  {CYN}/git branch [name]{R}     List branches or create+switch
  {CYN}/git stash [pop]{R}       Stash or pop working changes
  {CYN}/git status{R}            Show git status
  {CYN}/git log [n]{R}           Show recent commits (graph)
  {CYN}/git diff [file]{R}       Show diffs
  {CYN}/git clone <url>{R}       Clone a repo
  {CYN}/project <n>{R}           Switch / create project
  {CYN}/project delete <n>{R}    Delete a project and all its files
  {CYN}/projects{R}              List all projects
  {CYN}/debug on|off{R}          Toggle debug loop override
  {CYN}/run <cmd>{R}             Set custom run command for debug
  {CYN}/ls [path]{R}             List project files
  {CYN}/cat <file>{R}            Show file contents
  {CYN}/mkdir <dir>{R}           Create directory
  {CYN}/rm <path>{R}             Delete file or directory
  {CYN}/cp <src> <dst>{R}        Copy file or directory
  {CYN}/mv <src> <dst>{R}        Move / rename file or directory
  {CYN}/find <pattern>{R}        Search files by name/glob
  {CYN}/grep <text> [ext]{R}     Search text inside files
  {CYN}/info <path>{R}           File/directory metadata
  {CYN}/df{R}                    Disk usage of current project
  {CYN}/cd <path>{R}             Change working directory
  {CYN}/pwd{R}                   Show current working directory
  {CYN}/clear{R}                 Clear conversation memory
  {CYN}/history{R}               Show recent conversation turns
  {CYN}/exit{R}                  Quit

{B}Just type naturally to talk to the agent.{R}
"""


def _hr(char="─", width=60, color=DIM):
    return f"{color}{char * width}{R}"


def _mask(key: str) -> str:
    if not key:
        return f"{RED}(not set){R}"
    return f"{GRN}{key[:8]}…{key[-4:]}{R}"


class CLI:
    def __init__(self):
        self.cfg = load_config()
        self._debug_override = None   # None=auto, True=force on, False=force off
        self._run_command = None
        self._current_project = "default"
        self._executor = None
        self._orchestrator = None
        self._setup_project(self._current_project)

    # ── Setup ─────────────────────────────────────────────────────────────────
    def _setup_project(self, name: str):
        project_dir = os.path.join(self.cfg["project_dir"], name)
        os.makedirs(project_dir, exist_ok=True)
        self._executor = ToolExecutor(project_dir)
        self._current_project = name
        self._rebuild_agents()

    def _rebuild_agents(self):
        key, idx = get_active_key(self.cfg)
        if not key:
            self._orchestrator = None
            return
        main_client  = GroqClient(self.cfg, self.cfg["main_model"],  self.cfg["temperature"])
        debug_client = GroqClient(self.cfg, self.cfg["debug_model"], self.cfg["temperature"])
        main_agent   = MainAgent(main_client, self._executor)
        debug_agent  = DebugAgent(debug_client, self._executor)
        self._orchestrator = Orchestrator(
            main_agent, debug_agent, self._executor,
            max_debug_loops=self.cfg["max_debug_loops"],
            auto_commit=self.cfg["git_auto_commit"],
            on_status=lambda msg: print(f"  {DIM}{msg}{R}"),
        )

    def _ensure_key(self) -> bool:
        key, _ = get_active_key(self.cfg)
        if not key:
            print(f"\n{RED}No Groq API key set!{R}")
            self._cmd_keys("")
            key, _ = get_active_key(self.cfg)
            return bool(key)
        return True

    # ── Status bar ────────────────────────────────────────────────────────────
    def _status_bar(self) -> str:
        from datetime import date
        # Reload config to get freshest token counts + check for daily reset
        self.cfg = load_config()
        key, idx = get_active_key(self.cfg)
        tokens = self.cfg.get("token_usage", {}).get(str(idx), 0)
        token_str = f"{tokens:,}" if tokens else "0"
        key_indicator = f"{ACCENT}Key {idx}/3{R}"

        # Show which keys are set
        slots = []
        for i in (1, 2, 3):
            k = self.cfg.get(f"groq_key_{i}", "")
            if i == idx and k:
                slots.append(f"{ACCENT}{B}●{R}")
            elif k:
                slots.append(f"{DIM}●{R}")
            else:
                slots.append(f"{DIM}○{R}")
        key_slots = " ".join(slots)

        dir_short = self._executor.project_dir if self._executor else "?"
        home = os.path.expanduser("~")
        if dir_short.startswith(home):
            dir_short = "~" + dir_short[len(home):]

        # Daily reset label — matches Groq's reset cycle
        reset_date = self.cfg.get("token_reset_date", "")
        today = date.today().isoformat()
        reset_label = f" {GRN}↺today{R}" if reset_date == today else ""

        return (
            f"  {DIM}┤{R} {key_indicator} {key_slots} "
            f"{DIM}│{R} {DIM}Tokens:{R} {token_str}{reset_label} "
            f"{DIM}│{R} {BLU}{dir_short}{R} "
            f"{DIM}├{R}"
        )

    # ── Display ───────────────────────────────────────────────────────────────
    def _print_result(self, result: dict):
        print()
        print(_hr("═", 64, ACCENT))
        print(f"{B}{ACCENT}  Sable Reply{R}")
        print(_hr("─", 64))
        for line in result["chat_reply"].split("\n"):
            print(f"  {line}")
        print()

        if result.get("changes_summary"):
            print(f"{B}{GRN}  Changes:{R}")
            for ch in result["changes_summary"]:
                print(f"  {GRN}▸{R} {ch}")
            print()

        if result.get("tool_results"):
            print(f"{B}{BLU}  Tools:{R}")
            for tr in result["tool_results"]:
                icon = f"{GRN}✓{R}" if tr.success else f"{RED}✗{R}"
                print(f"  {icon} {DIM}{tr.tool}{R}", end="")
                if not tr.success and tr.error:
                    print(f"  → {RED}{tr.error[:80]}{R}", end="")
                elif tr.success and tr.output:
                    short = tr.output.split("\n")[0][:60]
                    print(f"  → {DIM}{short}{R}", end="")
                print()
            print()

        if result.get("debug_loops"):
            print(f"{B}{YLW}  Debug Loops:{R}")
            for loop in result["debug_loops"]:
                status_str = {
                    "pass":    f"{GRN}PASS{R}",
                    "fail":    f"{RED}FAIL{R}",
                    "partial": f"{YLW}PARTIAL{R}",
                }.get(loop["status"], loop["status"])
                print(f"  Loop {loop['loop']}: {status_str}  — {DIM}{loop['summary']}{R}")
                for issue in loop.get("issues", [])[:3]:
                    print(f"    {RED}•{R} {issue}")
            print()
        elif result.get("debug_skipped_reason"):
            reason = result["debug_skipped_reason"]
            if reason != "debug disabled by user":
                print(f"  {DIM}⚡ Debug skipped ({reason}){R}\n")

        final = result.get("final_status", "")
        if final == "pass":
            print(f"  {GRN}{B}✅  All checks passed!{R}")
        elif final == "max_loops_reached":
            print(f"  {YLW}{B}⚠️   Max debug loops reached.{R}")
        elif final == "built":
            print(f"  {BLU}{B}🔨  Done.{R}")

        if result.get("git_commit"):
            print(f"  {MGT}📦  {result['git_commit']}{R}")
        git_push = result.get("git_push", "")
        if git_push == "__NEEDS_REMOTE__":
            print(f"  {YLW}⚠️  No remote set. Run /git remote <url> to configure push.{R}")
        elif git_push:
            print(f"  {MGT}📤  {git_push}{R}")

        print(_hr("═", 64, ACCENT))
        print(self._status_bar())
        print()

    # ── Command: /keys ────────────────────────────────────────────────────────
    def _cmd_keys(self, arg: str):
        parts = arg.strip().split()
        sub = parts[0].lower() if parts else ""

        if sub == "use" and len(parts) > 1:
            try:
                idx = int(parts[1])
                assert 1 <= idx <= 3
                if not self.cfg.get(f"groq_key_{idx}"):
                    print(f"  {RED}Key {idx} is not set. Add it first with /keys{R}")
                    return
                self.cfg["active_key_index"] = idx
                save_config(self.cfg)
                self._rebuild_agents()
                print(f"  {GRN}Switched to key {idx}{R}")
            except (ValueError, AssertionError):
                print(f"  {RED}Usage: /keys use <1|2|3>{R}")
            return

        # Interactive key manager
        print(f"\n{B}{ACCENT}  Groq API Keys{R}")
        print(_hr())
        for i in (1, 2, 3):
            active_mark = f" {ACCENT}← active{R}" if i == self.cfg.get("active_key_index") else ""
            k = self.cfg.get(f"groq_key_{i}", "")
            tokens = self.cfg.get("token_usage", {}).get(str(i), 0)
            tok_str = f"  {DIM}({tokens:,} tokens used){R}" if tokens else ""
            print(f"  {B}Key {i}{R}: {_mask(k)}{active_mark}{tok_str}")

        print()
        for i in (1, 2, 3):
            label = "primary" if i == 1 else f"fallback {i-1}"
            val = _getpass(f"  Enter key {i} ({label}) [blank to keep, hidden]: ").strip()
            if val:
                self.cfg[f"groq_key_{i}"] = val
                print(f"  {GRN}Key {i} saved.{R}")

        # Set active to first available
        if not self.cfg.get(f"groq_key_{self.cfg['active_key_index']}"):
            for i in (1, 2, 3):
                if self.cfg.get(f"groq_key_{i}"):
                    self.cfg["active_key_index"] = i
                    break

        save_config(self.cfg)
        self._rebuild_agents()
        print(f"  {GRN}Keys saved. Active: key {self.cfg['active_key_index']}{R}\n")

    # ── Command: /git creds ───────────────────────────────────────────────────
    def _cmd_git_creds(self, sub: str):
        creds = load_git_creds()
        if sub == "show":
            print(f"\n{B}Git Credentials:{R}")
            print(f"  Username : {creds.get('username') or f'{RED}(not set){R}'}")
            print(f"  Email    : {creds.get('email') or f'{RED}(not set){R}'}")
            tok = creds.get("token", "")
            print(f"  Token    : {_mask(tok)}")
            print()
            return

        print(f"\n{B}{ACCENT}  GitHub Credentials{R}")
        print(f"  {DIM}Used for seamless git push/clone with private repos.{R}")
        print(f"  {DIM}Token is stored locally at ~/.sable/git_creds.json (chmod 600).{R}\n")

        u = input(f"  GitHub username [{creds.get('username') or 'blank to keep'}]: ").strip()
        e = input(f"  GitHub email    [{creds.get('email') or 'blank to keep'}]: ").strip()
        t = _getpass("  GitHub PAT token [blank to keep, hidden]: ").strip()

        if u: creds["username"] = u
        if e: creds["email"] = e
        if t: creds["token"] = t

        save_git_creds(creds)
        print(f"  {GRN}Credentials saved.{R}\n")

    # ── Command: /config ──────────────────────────────────────────────────────
    def _cmd_config(self):
        print(f"\n{B}Settings:{R}")
        safe_cfg = {k: ("***" if "key" in k and v else v) for k, v in self.cfg.items()
                    if k not in ("token_usage",)}
        for k, v in safe_cfg.items():
            print(f"  {CYN}{k}{R}: {v}")
        print(f"\n{B}Available models:{R}")
        for m in AVAILABLE_MODELS:
            mark = f" {GRN}←{R}" if m == self.cfg["main_model"] else ""
            print(f"  {m}{mark}")
        k = input(f"\n  Change main model? (enter name or blank to skip): ").strip()
        if k in AVAILABLE_MODELS:
            self.cfg["main_model"] = k
            save_config(self.cfg)
            self._rebuild_agents()
            print(f"  {GRN}Main model set to {k}{R}")

    # ── Command: /project ─────────────────────────────────────────────────────
    def _cmd_project(self, arg: str):
        parts = arg.strip().split(None, 1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub == "delete":
            self._cmd_project_delete(rest)
            return

        name = (arg.strip().replace(" ", "_") or "default")
        self._setup_project(name)
        print(f"  {GRN}Switched to project: {B}{name}{R}")
        print(f"  {DIM}Dir: {self._executor.project_dir}{R}")

    def _cmd_project_delete(self, name: str):
        import shutil
        name = name.strip().replace(" ", "_")
        if not name:
            print(f"  {RED}Usage: /project delete <name>{R}")
            return
        base = self.cfg["project_dir"]
        target = os.path.join(base, name)
        if not os.path.isdir(target):
            print(f"  {RED}Project not found: {name}{R}")
            return
        if name == self._current_project:
            print(f"  {RED}Cannot delete the active project. Switch first with /project <other>.{R}")
            return
        confirm = input(f"  {YLW}Delete project '{name}' and ALL its files? [y/N]: {R}").strip().lower()
        if confirm == "y":
            shutil.rmtree(target)
            print(f"  {GRN}Deleted project: {name}{R}")
        else:
            print(f"  {DIM}Cancelled.{R}")

    def _cmd_projects(self):
        base = self.cfg["project_dir"]
        os.makedirs(base, exist_ok=True)
        projects = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        print(f"\n{B}Projects:{R}")
        for p in sorted(projects):
            mark = f" {GRN}← current{R}" if p == self._current_project else ""
            print(f"  📁 {p}{mark}")

    # ── Command: /git ─────────────────────────────────────────────────────────
    def _prompt_remote(self) -> bool:
        """Ask user to set a remote. Returns True if one was set."""
        print(f"\n  {YLW}⚠️  No remote origin configured for this project.{R}")
        url = input(f"  Enter GitHub repo URL (blank to cancel): ").strip()
        if not url:
            print(f"  {DIM}Skipped. Use /git remote <url> later.{R}")
            return False
        r = self._executor.git_set_remote(url)
        if r.success:
            print(f"  {GRN}{r.output}{R}")
        else:
            print(f"  {RED}{r.error}{R}")
        return r.success

    def _cmd_git(self, args: str):
        parts = args.strip().split(None, 1)
        if not parts:
            return
        sub = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if sub == "creds":
            self._cmd_git_creds(rest.strip().lower())

        elif sub == "init":
            remote = rest.strip() or None
            r = self._executor.git_init(remote)
            print(f"  {GRN if r.success else RED}{r}{R}")

        elif sub == "remote":
            url = rest.strip()
            if not url:
                print(f"  {RED}Usage: /git remote <url>{R}")
                return
            r = self._executor.git_set_remote(url)
            print(f"  {GRN if r.success else RED}{r.output or r.error}{R}")

        elif sub == "push":
            branch = rest.strip() or ""
            # Prompt for creds if missing
            creds = load_git_creds()
            if not creds.get("token"):
                print(f"  {YLW}No git credentials stored. Let's set them up.{R}")
                self._cmd_git_creds("")
            # Prompt for remote if missing
            if not self._executor._git_has_remote():
                if not self._prompt_remote():
                    return
            r = self._executor.git_push(branch)
            if r.error == "__NO_REMOTE__":
                self._prompt_remote()
                return
            print(f"  {GRN if r.success else RED}{r.output or r.error}{R}")

        elif sub == "pull":
            if not self._executor._git_has_remote():
                if not self._prompt_remote():
                    return
            r = self._executor.git_pull(rest.strip() or "")
            if r.error == "__NO_REMOTE__":
                self._prompt_remote()
                return
            print(f"  {GRN if r.success else RED}{r.output or r.error}{R}")

        elif sub == "branch":
            r = self._executor.git_branch(rest.strip())
            print(f"  {GRN if r.success else RED}{r.output or r.error}{R}")

        elif sub == "stash":
            action = rest.strip().lower() or "push"
            r = self._executor.git_stash(action)
            print(f"  {GRN if r.success else RED}{r.output or r.error}{R}")

        elif sub == "status":
            r = self._executor.git_status()
            print(f"  {r.output or '(clean)'}")

        elif sub == "log":
            n = int(rest.strip()) if rest.strip().isdigit() else 10
            r = self._executor.git_log(n)
            print(f"  {r.output}")

        elif sub == "diff":
            r = self._executor.git_diff(rest.strip())
            print(r.output or "(no diff)")

        elif sub == "clone":
            if not rest.strip():
                print(f"  {RED}Usage: /git clone <url> [dest]{R}")
                return
            url_parts = rest.strip().split(None, 1)
            url = url_parts[0]
            dest = url_parts[1] if len(url_parts) > 1 else ""
            r = self._executor.git_clone(url, dest)
            print(f"  {GRN if r.success else RED}{r.output or r.error}{R}")

        elif sub == "add":
            r = self._executor.git_add(rest.strip() or ".")
            print(f"  {GRN if r.success else RED}{r}{R}")

        elif sub == "commit":
            if not rest.strip():
                rest = input("  Commit message: ").strip()
            r = self._executor.git_commit(rest)
            print(f"  {GRN if r.success else RED}{r}{R}")

        else:
            r = self._executor.run_bash(f"git {args}", cwd=self._executor.project_dir)
            print(f"  {r.output or r.error}")

    # ── File/dir commands ─────────────────────────────────────────────────────
    def _cmd_ls(self, path: str = "."):
        r = self._executor.list_files(path or ".")
        print(f"\n{B}Files in {self._executor.project_dir}/{path}:{R}")
        print(r.output or "(empty)")

    def _cmd_cat(self, path: str):
        if not path:
            print(f"  {RED}Usage: /cat <file>{R}")
            return
        r = self._executor.read_file(path)
        if r.success:
            print()
            print(_hr("─", 64, ACCENT))
            print(r.output)
            print(_hr("─", 64, ACCENT))
        else:
            print(f"  {RED}{r.error}{R}")

    def _cmd_mkdir(self, path: str):
        if not path:
            print(f"  {RED}Usage: /mkdir <dir>{R}")
            return
        r = self._executor.make_dir(path)
        print(f"  {GRN if r.success else RED}{r.output or r.error}{R}")

    def _cmd_rm(self, path: str):
        if not path:
            print(f"  {RED}Usage: /rm <path>{R}")
            return
        confirm = input(f"  {YLW}Delete '{path}'? [y/N]: {R}").strip().lower()
        if confirm == "y":
            r = self._executor.delete_file(path)
            print(f"  {GRN if r.success else RED}{r.output or r.error}{R}")
        else:
            print(f"  {DIM}Cancelled.{R}")

    def _cmd_cp(self, args: str):
        parts = args.split(None, 1)
        if len(parts) < 2:
            print(f"  {RED}Usage: /cp <src> <dst>{R}")
            return
        r = self._executor.copy_file(parts[0], parts[1])
        print(f"  {GRN if r.success else RED}{r.output or r.error}{R}")

    def _cmd_mv(self, args: str):
        parts = args.split(None, 1)
        if len(parts) < 2:
            print(f"  {RED}Usage: /mv <src> <dst>{R}")
            return
        r = self._executor.move_file(parts[0], parts[1])
        print(f"  {GRN if r.success else RED}{r.output or r.error}{R}")

    def _cmd_find(self, pattern: str):
        if not pattern:
            print(f"  {RED}Usage: /find <pattern>{R}")
            return
        r = self._executor.search_files(pattern)
        print(f"\n{B}Search results for '{pattern}':{R}")
        print(r.output)

    def _cmd_grep(self, args: str):
        parts = args.split()
        if not parts:
            print(f"  {RED}Usage: /grep <text> [.ext]{R}")
            return
        text = parts[0]
        ext  = parts[1] if len(parts) > 1 else ""
        r = self._executor.grep_files(text, ext=ext)
        print(f"\n{B}Grep '{text}':{R}")
        print(r.output or "(no matches)")

    def _cmd_info(self, path: str):
        if not path:
            print(f"  {RED}Usage: /info <path>{R}")
            return
        r = self._executor.file_info(path)
        print(f"\n{r.output}")

    def _cmd_df(self):
        r = self._executor.disk_usage()
        print(f"\n{B}Disk usage:{R}")
        print(f"  {r.output}")

    def _cmd_cd(self, path: str):
        if not path:
            print(f"  {RED}Usage: /cd <path>{R}")
            return
        r = self._executor.change_dir(path)
        if r.success:
            print(f"  {GRN}{r.output}{R}")
        else:
            print(f"  {RED}{r.error}{R}")

    # ── Main loop ─────────────────────────────────────────────────────────────
    def run(self):
        print(BANNER)
        key, idx = get_active_key(self.cfg)
        print(f"  Project : {B}{ACCENT}{self._current_project}{R}")
        print(f"  Model   : {B}{self.cfg['main_model']}{R}")
        print(f"  Key     : {B}key {idx}{R} {_mask(key)}")
        print(f"  Type {CYN}/help{R} for commands.\n")
        print(self._status_bar())
        print()

        while True:
            try:
                prompt = f"\n{MGT}{B}[{self._current_project}]{R} {ACCENT}▶{R} "
                # Print prompt then read — avoids newline overlap issue
                sys.stdout.write(prompt)
                sys.stdout.flush()
                user_input = sys.stdin.readline()
                if user_input == "":
                    raise EOFError
                user_input = user_input.rstrip("\n").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{DIM}Bye!{R}")
                sys.exit(0)

            if not user_input:
                continue

            # ── Built-in commands ──────────────────────────────────────────
            if user_input.startswith("/"):
                parts = user_input[1:].split(None, 1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""

                if cmd in ("exit", "quit", "q"):
                    print(f"{DIM}Bye!{R}")
                    sys.exit(0)

                elif cmd == "help":
                    print(HELP_TEXT)

                elif cmd == "config":
                    self._cmd_config()

                elif cmd == "keys":
                    self._cmd_keys(arg)

                elif cmd == "project":
                    self._cmd_project(arg)

                elif cmd == "projects":
                    self._cmd_projects()

                elif cmd == "ls":
                    self._cmd_ls(arg)

                elif cmd == "cat":
                    self._cmd_cat(arg)

                elif cmd == "mkdir":
                    self._cmd_mkdir(arg)

                elif cmd == "rm":
                    self._cmd_rm(arg)

                elif cmd == "cp":
                    self._cmd_cp(arg)

                elif cmd == "mv":
                    self._cmd_mv(arg)

                elif cmd == "find":
                    self._cmd_find(arg)

                elif cmd == "grep":
                    self._cmd_grep(arg)

                elif cmd == "info":
                    self._cmd_info(arg)

                elif cmd == "df":
                    self._cmd_df()

                elif cmd == "cd":
                    self._cmd_cd(arg)

                elif cmd == "pwd":
                    print(f"  {BLU}{self._executor.project_dir}{R}")

                elif cmd == "clear":
                    if self._orchestrator:
                        self._orchestrator.main.reset_history()
                    print(f"  {GRN}Conversation history cleared.{R}")

                elif cmd == "history":
                    if self._orchestrator:
                        hist = self._orchestrator.main.history
                        print(f"\n{B}Conversation turns: {len(hist)}{R}")
                        for h in hist[-6:]:
                            rc = GRN if h["role"] == "user" else CYN
                            print(f"  {rc}{h['role']}{R}: {h['content'][:80]}…")
                    else:
                        print("  No history.")

                elif cmd == "debug":
                    if arg.lower() in ("on", "1", "true"):
                        self._debug_override = True
                        print(f"  {GRN}Debug loop: forced ON{R}")
                    elif arg.lower() in ("off", "0", "false"):
                        self._debug_override = False
                        print(f"  {YLW}Debug loop: forced OFF{R}")
                    else:
                        mode = "auto" if self._debug_override is None else ("ON" if self._debug_override else "OFF")
                        print(f"  Debug mode: {mode}")

                elif cmd == "nodebug":
                    self._debug_override = False
                    print(f"  {YLW}Debug loop: OFF for next request.{R}")

                elif cmd == "run":
                    self._run_command = arg or None
                    print(f"  {GRN}Run command: {self._run_command or '(auto-detect)'}{R}")

                elif cmd == "git":
                    self._cmd_git(arg)

                else:
                    print(f"  {RED}Unknown command /{cmd}. Type /help{R}")

                continue

            # ── Agent message ──────────────────────────────────────────────
            if not self._ensure_key():
                print(f"  {RED}Cannot proceed without API key.{R}")
                continue

            if self._orchestrator is None:
                self._rebuild_agents()

            try:
                # debug_enabled: if override set use that, else let orchestrator decide (True = "may run")
                debug_flag = True if self._debug_override is None else self._debug_override

                result = self._orchestrator.handle(
                    user_input,
                    debug_enabled=debug_flag,
                    run_command=self._run_command,
                )
                self._print_result(result)
                # Reset one-shot overrides
                if self._debug_override is False:
                    self._debug_override = None

            except Exception as e:
                print(f"\n{RED}{B}Error:{R} {e}{R}\n")
                import traceback
                traceback.print_exc()
