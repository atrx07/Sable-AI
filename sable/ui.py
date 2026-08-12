"""Terminal colors, banner, and display helpers."""

from __future__ import annotations

R = "\033[0m"

B = "\033[1m"

DIM = "\033[2m"

CYN = "\033[96m"

GRN = "\033[92m"

YLW = "\033[93m"

RED = "\033[91m"

MGT = "\033[95m"

BLU = "\033[94m"

ACCENT = "\033[38;5;154m"

BANNER = f"""{ACCENT}{B}
    ╔══════════════════════════════════════════════╗
    ║                  S A B L E                   ║
    ╚══════════════════════════════════════════════╝{R}
{DIM}  Bounded coding agent · Termux-first · v2.0 · by atrx07{R}
"""

HELP_TEXT = f"""
{B}{ACCENT}Agent:{R}
  {CYN}/mode plan|build|yolo{R}  Permission mode (default: build)
  {CYN}/verify on|off{R}         Deterministic verification after code edits
  {CYN}/run <command>{R}         Override verification command for this session
  {CYN}/clear{R}                 Clear conversation memory
  {CYN}/history{R}               Show recent conversation turns

{B}{ACCENT}Groq:{R}
  {CYN}/keys{R}                  Manage up to 3 Groq API keys
  {CYN}/keys use <1|2|3>{R}      Choose preferred key slot
  {CYN}/models{R}                Fetch live models from Groq
  {CYN}/config{R}                Show current Sable settings

{B}{ACCENT}Projects / files:{R}
  {CYN}/project <name>{R}        Switch/create project
  {CYN}/projects{R}              List projects
  {CYN}/ls [path]{R}             List workspace files
  {CYN}/cat <file>{R}            Read file
  {CYN}/mkdir <dir>{R}           Create directory
  {CYN}/rm <path>{R}             Delete with confirmation
  {CYN}/cp <src> <dst>{R}        Copy file/directory
  {CYN}/mv <src> <dst>{R}        Move/rename
  {CYN}/find <pattern>{R}        Glob search
  {CYN}/grep <text> [ext]{R}     Text search
  {CYN}/info <path>{R}           Metadata
  {CYN}/df{R}                    Workspace disk usage
  {CYN}/cd <path>{R}             Change cwd inside workspace jail
  {CYN}/pwd{R}                   Show cwd

{B}{ACCENT}Git:{R}
  {CYN}/git init [url]{R}        Initialize git (main by default)
  {CYN}/git remote <url>{R}      Set origin
  {CYN}/git status{R}            Status
  {CYN}/git diff [file]{R}       Diff
  {CYN}/git log [n]{R}           Log
  {CYN}/git branch [name]{R}     List/switch/create branch
  {CYN}/git add [paths]{R}       Stage paths
  {CYN}/git commit <message>{R}  Commit
  {CYN}/git push [branch]{R}     Explicit push using your normal Git auth
  {CYN}/git pull [branch]{R}     Explicit rebase pull
  {CYN}/git clone <url> [dest]{R} Clone
  {CYN}/git stash [pop]{R}       Stash/pop

{B}Natural-language requests go through the bounded agent loop.{R}
"""

def _hr(char: str = "─", width: int = 64, color: str = DIM) -> str:
    return f"{color}{char * width}{R}"

def _mask(key: str) -> str:
    if not key:
        return f"{RED}(not set){R}"
    return f"{GRN}{key[:6]}…{key[-4:]}{R}"

