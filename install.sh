#!/data/data/com.termux/files/usr/bin/bash
# Sable v2 — Termux installer
set -euo pipefail

printf '\n  ╔══════════════════════════════════╗\n'
printf '  ║        Sable v2 Installer        ║\n'
printf '  ║  Bounded coding agent · Termux   ║\n'
printf '  ╚══════════════════════════════════╝\n\n'

echo '▸ Updating Termux packages...'
pkg update -y -q

echo '▸ Installing Python and Git...'
pkg install -y python git

echo '▸ Installing Sable in editable mode...'
python -m pip install --upgrade pip setuptools >/dev/null
python -m pip install -e .

mkdir -p "$HOME/.sable" "$HOME/sable-projects"
chmod 700 "$HOME/.sable" 2>/dev/null || true

echo ''
echo '  ✅ Sable v2 installed.'
echo ''
echo '  Run: sable'
echo ''
echo '  First run: add a Groq API key with /keys.'
echo '  Git auth is intentionally NOT stored by Sable; configure SSH or your normal Git credential helper.'
echo ''
