from __future__ import annotations

"""
TestGuard CLI package.

This package contains argument parsing and command handlers for the `testguard` console script.
"""

from testguard.cli.arg_parser import RunOptions, build_arg_parser
from testguard.cli.main import entrypoint, main

__all__ = [
    "RunOptions",
    "build_arg_parser",
    "main",
    "entrypoint",
]