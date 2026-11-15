"""PyInstaller entry for SystemAutoBot.exe."""
import sys

from systemautobot.app import run

if __name__ == "__main__":
    sys.exit(run())
