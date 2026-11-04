# GitHub Actions Workflows

This directory contains the automated workflows for the OptionsCommander repository. Actions are pinned to commit SHAs (#604).

## Workflows

### `main.yml` - CI Tests

**Triggers:** Push and pull requests to `main` and `development`

Installs the package (`pip install -e .` plus pytest) and runs `pytest tests/ -m "not db"`.
No secrets are passed, so no test can reach the broker or place an order. The DB tests
need the private gex package and PostgreSQL, so they run locally only
(`pytest tests/ -m db`).

### `lint.yml` - Code Quality Checks (non-blocking)

**Triggers:** Pull requests to `main` and `development`

Every step has `continue-on-error`, so findings show on the PR without failing it:

- **Lint:** ruff at the rev pinned in `.pre-commit-config.yaml`, on the tracked Python files (ruff skips the gitignored `tests/` when it walks directories); pylint errors only; ruff format check through pre-commit; mypy on `src/`.
- **Security scan:** bandit on `src/`, with the JSON report uploaded as an artifact.
- **Code complexity analysis:** radon cyclomatic complexity and maintainability index on `src/`.

### `auto-fix-on-push.yml` - Auto-Fix Code Quality on Push

**Triggers:** Push to `feature/**`, `development` and `main`

Runs the ruff fixes and formatter through pre-commit (the same version as local commits) on the tracked Python files, and pushes a `style: Auto-fix code quality issues [skip ci]` commit only if something changed. With the hooks run locally there is nothing to fix (#603).

### `check-commit-signatures.yml` - Commit Signature Checker

**Triggers:**

- Pull requests (opened, synchronized, reopened)
- Push to `development` and `main` branches

**Purpose:** Prevents commits with Claude Code signatures from being merged.

**What it checks:**

- `Generated with [Claude Code]` - Auto-generated footer
- `Co-Authored-By: Claude <noreply@anthropic.com>` - Co-author signature, with or without a model name (`Claude Opus 5.5`); matched case-insensitively. Only the PR's own commits are checked (or the pushed commit), so older history isn't rescanned.

**Why:** Claude Code signatures should not appear in the repository's commit history. This workflow automatically detects and rejects any commits containing these signatures.

**If signatures are detected:**

1. The workflow will fail with an error message
2. Use the cleanup script to remove signatures:

   ```bash
   git filter-branch -f --msg-filter 'scripts/utilities/remove_commit_signatures.py' HEAD~10..HEAD
   git push -f origin <branch-name>
   ```

3. The PR will automatically update with the cleaned commits

## Related Scripts

- **`scripts/utilities/remove_commit_signatures.py`** - Python script to remove Claude Code signatures from commit messages
  - Used with `git filter-branch` to clean commit history
  - See script header for usage examples
