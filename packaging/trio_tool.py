"""Entry point for the Windows build (PyInstaller)."""
import sys

from trioplus.gui.server import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
