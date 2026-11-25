"""Entry point used by PyInstaller to build ClipKit.exe."""
import sys

from clipkit.cli import main

if __name__ == "__main__":
    sys.exit(main())
