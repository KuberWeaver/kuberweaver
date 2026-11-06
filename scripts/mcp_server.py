#!/usr/bin/env python3
"""Launch MCP stdio from any working directory using the app interpreter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from kuberweaver.mcp_server import main

if __name__ == '__main__':
    main()
