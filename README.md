# Sable

> Agentic AI coding assistant · Termux Edition · by atrx07

A lightweight, fully offline-capable AI coding agent that runs entirely in Termux on Android. Powered by Groq with multi-key rotation and advanced file management.

---

## Install

```bash
bash install.sh
```

Then run:
```bash
python sable.py
# or after restarting shell:
sable
```

---

## Features

- **Multi-key Groq support** — Add up to 3 API keys. When one hits rate limits, Sable auto-rotates to the next.
- **Smart debug agent** — The main agent decides *if* and *how many* debug loops are needed. No unnecessary debug calls for simple file reads or config changes.
- **Advanced file management** — Read, write, patch, search, grep, copy, move, directory ops — all from the chat interface or via slash commands.
- **Git with stored credentials** — Set your GitHub username + PAT token once; all pushes and clones are authenticated seamlessly.
- **Token tracking** — See how many tokens each key has consumed in the status bar.
- **Live status bar** — Shows active key slot, token usage, and current working directory.

---

## Slash Commands

| Command | Description |
|---|---|
| `/help` | Full command list |
| `/keys` | Manage 1–3 Groq API keys |
| `/keys use <1\|2\|3>` | Switch active key |
| `/git creds` | Store GitHub username + token |
| `/git push` | Authenticated push |
| `/git clone <url>` | Authenticated clone |
| `/cat <file>` | Show file contents |
| `/ls [path]` | List files with sizes |
| `/find <pattern>` | Search files by glob |
| `/grep <text> [.ext]` | Search inside files |
| `/mkdir / /rm / /cp / /mv` | Directory management |
| `/cd <path>` | Change working dir |
| `/info <path>` | File metadata |
| `/df` | Disk usage |
| `/debug on\|off` | Force debug on/off |
| `/config` | Model settings |
| `/project <name>` | Switch project |

---

## Config

Stored at `~/.sable/config.json`.  
Git credentials stored at `~/.sable/git_creds.json` (chmod 600).

---

## Credits

Built by [atrx07](https://github.com/atrx07) · Powered by [Groq](https://console.groq.com)
