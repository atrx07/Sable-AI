#!/usr/bin/env python3
"""
Sable — Agentic AI coding assistant for Termux
Main entry point
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.cli import CLI

if __name__ == "__main__":
    cli = CLI()
    cli.run()
