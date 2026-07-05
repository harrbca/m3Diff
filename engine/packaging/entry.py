"""PyInstaller entry point for the m3diff-engine sidecar (ADR-021).

One frozen exe serves both roles:
    m3diff-engine.exe serve            <- spawned by the desktop shell
    m3diff-engine.exe compare/classify <- usable as a standalone CLI

``freeze_support()`` MUST run first: multiprocessing pool workers re-execute
this same binary with ``--multiprocessing-fork``, and without the call each
worker would boot another full CLI instead of entering the worker loop.
"""
import multiprocessing
import sys

from m3diff.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
