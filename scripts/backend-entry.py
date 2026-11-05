"""Frozen executable entry point, shared by the native app and terminal."""
from typhoon.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
