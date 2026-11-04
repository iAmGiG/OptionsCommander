#!/usr/bin/env python3
"""
Remove Claude Code signatures from git commit messages.

This script is designed to be used with git filter-branch:

    git filter-branch -f --msg-filter 'scripts/utilities/remove_commit_signatures.py' HEAD~10..HEAD

It reads commit messages from stdin and removes:
- Lines containing "Generated with [Claude Code]"
- Lines containing "Co-Authored-By: Claude"
- Trailing empty lines

The cleaned commit message is written to stdout.
"""

import sys


def main():
    """Read commit message from stdin, remove signatures, write to stdout."""
    message = sys.stdin.read()
    lines = message.split("\n")
    filtered_lines = []

    for line in lines:
        # Skip lines with Claude Code signature (various formats)
        if "Generated with [Claude Code]" in line:
            continue
        if "Generated with Claude Code" in line:
            continue
        if "Co-Authored-By: Claude" in line:
            continue
        filtered_lines.append(line)

    # Remove trailing empty lines
    while filtered_lines and filtered_lines[-1] == "":
        filtered_lines.pop()

    print("\n".join(filtered_lines))


if __name__ == "__main__":
    main()
