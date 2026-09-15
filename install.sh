#!/data/data/com.termux/files/usr/bin/bash
# Sable v2 — Termux installer
set -euo pipefail

if ! command -v pkg >/dev/null 2>&1 || [[ "${PREFIX:-}" != *com.termux* ]]; then
    echo 'This installer requires Termux. See docs/platforms.md for pip/venv installation.' >&2
    exit 1
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

printf '\n  ╔══════════════════════════════════╗\n'
printf '  ║        Sable v2 Installer        ║\n'
printf '  ║  Bounded coding agent · Termux   ║\n'
printf '  ╚══════════════════════════════════╝\n\n'

echo '▸ Updating Termux packages...'
pkg update -y -q

echo '▸ Installing Python and Git...'
pkg install -y python python-pip git

echo '▸ Installing Sable in editable mode...'
# Termux owns pip via pkg; upgrading pip itself is explicitly unsupported.
python -m pip install -e "$SCRIPT_DIR"

mkdir -p "$HOME/.sable" "$HOME/sable-projects"
chmod 700 "$HOME/.sable"

echo ''
echo '  ✅ Sable v2 installed.'
echo ''
echo '  Run: sable'
echo ''
echo '  First run: add a Groq API key with /keys.'
echo '  Git auth is intentionally NOT stored by Sable; configure SSH or your normal Git credential helper.'
echo ''
