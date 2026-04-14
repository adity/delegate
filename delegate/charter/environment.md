# Environment Setup

Every repo worktree has auto-generated `.delegate/setup.sh` and `.delegate/premerge.sh` scripts. The daemon creates these when it provisions the worktree, detecting the project's tooling (Python, Node, Rust, Go, Ruby, Nix) and generating correct install/test commands.

Your job is to **source** the setup script, verify it works, and fix it if needed — not to write it from scratch.

## Step 1: Source .delegate/setup.sh — MANDATORY

This is your FIRST action on every task, before writing any code or running any command. No exceptions.

```
. .delegate/setup.sh
```

If it exits non-zero, investigate and fix it before proceeding. Common issues:
- Missing system tool (nix, uv, cargo, etc.) — check error message, report to manager
- Stale lockfile — run the appropriate sync command, update setup.sh if needed
- Wrong install command — see `charter/templates/environment-reference.md` and fix

## Step 2: If scripts are missing (rare)

Regenerate them: `python -m delegate.env "$WORKTREE_ROOT"` (auto-detects stack, writes both scripts with a git commit). Use `--force` to overwrite, `--no-commit` to skip commit, `--print` to preview.

If unavailable, create scripts manually using templates in `charter/templates/environment-reference.md`.

## What the auto-generated scripts detect

| Signal | Stack |
|---|---|
| `poetry.lock` | Python (Poetry) |
| `uv.lock` | Python (uv sync) |
| `pyproject.toml` / `requirements.txt` | Python (uv/pip fallback) |
| `pnpm-lock.yaml` / `yarn.lock` / `package-lock.json` | Node |
| `Cargo.toml` | Rust |
| `go.mod` | Go |
| `Gemfile` | Ruby |
| `shell.nix` / `flake.nix` | Nix (wraps inner stack) |

For multi-language repos, all detected stacks are composed into a single pair of scripts.

## Isolation requirement — CRITICAL

Every worktree MUST have its own isolated environment (`.venv`, `node_modules`, etc.) created INSIDE the worktree directory. NEVER reuse or symlink to the main repo's environment.

The correct pattern: `VENV_DIR="$WORKTREE_ROOT/.venv"`. If creation fails, exit with a clear error — do NOT fall back to sharing.

**Network is restricted** to curated package registries. Setup scripts use a 3-layer additive install strategy:
1. **Copy from main repo** — instant, zero network (bulk bootstrap)
2. **Install from system cache** — offline/cache-only mode
3. **Full network install** — standard install, catches remaining gaps

All three run unconditionally and are idempotent. Do not create `.pth` files, symlinks, or any mechanism to borrow packages from outside the worktree.

## Keeping scripts up to date

When you change dependencies: update `setup.sh`, update `premerge.sh` if test commands change, commit both with your code. To regenerate: `python -m delegate.env --force "$WORKTREE_ROOT"`.

## Safety rules

**Never:** `sudo`, install system packages, start system services, change global tool versions, share environments across worktrees.

**Always:** use project-local environments, use lockfiles when available, check tools exist and fail clearly if not.

If the project requires system dependencies (Postgres, Redis, etc.), add checks at the top of `setup.sh` that verify they exist and print install instructions. Do not install them yourself.

Always source `.delegate/setup.sh` before running `python`, `pytest`, `npm`, etc. The merge worker runs `.delegate/premerge.sh` before merging — keep it passing.
