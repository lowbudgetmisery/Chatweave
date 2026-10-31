"""Entry point for the packaged .exe (PyInstaller). Same as: python -m chatmerge"""
from chatmerge.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
