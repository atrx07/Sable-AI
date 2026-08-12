#!/data/data/com.termux/files/usr/bin/bash
# Sable — Termux installer
set -e

echo ""
echo "  ╔══════════════════════════════════╗"
echo "  ║         Sable Installer          ║"
echo "  ║   Agentic AI · Termux Edition    ║"
echo "  ╚══════════════════════════════════╝"
echo ""

# 1. System packages
echo "▸ Updating pkg..."
pkg update -y -q

echo "▸ Installing Python & git..."
pkg install -y python git

# 2. Python deps
echo "▸ Installing Python packages..."
pip install --quiet requests

# 3. Make agent executable
chmod +x sable.py

# 4. Verify core package
if [ ! -f "core/__init__.py" ]; then
  echo "  ✗  ERROR: core/ package not found. Make sure you extracted the full archive."
  exit 1
fi
echo "▸ core/ package found. ✓"

# 5. Config directory
mkdir -p "$HOME/.sable"
echo "▸ Config dir: ~/.sable ✓"

# 6. Add alias 'sable'
SHELL_RC="$HOME/.bashrc"
if [ -f "$HOME/.zshrc" ]; then SHELL_RC="$HOME/.zshrc"; fi

SABLE_PATH="$(pwd)/sable.py"
ALIAS_LINE="alias sable='python $SABLE_PATH'"

if ! grep -q "alias sable=" "$SHELL_RC" 2>/dev/null; then
  echo "" >> "$SHELL_RC"
  echo "# Sable" >> "$SHELL_RC"
  echo "$ALIAS_LINE" >> "$SHELL_RC"
  echo "▸ Added alias 'sable' to $SHELL_RC"
else
  # Update existing alias in case path changed
  sed -i "s|alias sable=.*|$ALIAS_LINE|" "$SHELL_RC"
  echo "▸ Updated alias 'sable' in $SHELL_RC"
fi

echo ""
echo "  ✅  Installation complete!"
echo ""
echo "  Run now  :  python sable.py"
echo "  Or alias :  source $SHELL_RC && sable"
echo ""
echo "  On first run you'll be prompted for your Groq API key."
echo "  You can add up to 3 keys (with fallback rotation)."
echo "  Get a free key at: https://console.groq.com"
echo ""
