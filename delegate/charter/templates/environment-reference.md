# Environment Setup — Reference Templates

These show what the auto-generated scripts look like and how to modify them
when the project has unusual needs. Only consult these when `setup.sh` is
missing or broken and you need to create/fix it manually.

## Finding the repo root from a linked worktree

Worktrees are linked git directories. The generated scripts use a portable
`_SELF` reference:

```bash
if [ -n "${BASH_VERSION:-}" ]; then _SELF="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION:-}" ]; then eval '_SELF="${(%):-%x}"'
else _SELF="$0"; fi

WORKTREE_ROOT="$(cd "$(dirname "$_SELF")/.." && pwd)"
GIT_COMMON="$(git -C "$WORKTREE_ROOT" rev-parse --git-common-dir 2>&1)" || true
REPO_ROOT="$(cd "$GIT_COMMON/.." 2>/dev/null && pwd)"
```

---

## Nix

When `shell.nix` or `flake.nix` is present, all commands run inside the nix shell.

### setup.sh

```bash
#!/usr/bin/env bash
set -e
# [portable _SELF block above]
WORKTREE_ROOT="$(cd "$(dirname "$_SELF")/.." && pwd)"
GIT_COMMON="$(git -C "$WORKTREE_ROOT" rev-parse --git-common-dir 2>&1)" || true
REPO_ROOT="$(cd "$GIT_COMMON/.." 2>/dev/null && pwd)"

if [ -f "$REPO_ROOT/flake.nix" ] && command -v nix >/dev/null 2>&1; then
  nix develop "$REPO_ROOT" --command bash -c \
    "cd $WORKTREE_ROOT && <install command --quiet>"
elif [ -f "$REPO_ROOT/shell.nix" ] && command -v nix-shell >/dev/null 2>&1; then
  nix-shell "$REPO_ROOT/shell.nix" --run \
    "bash -c 'cd $WORKTREE_ROOT && <install command --quiet>'"
else
  echo "ERROR: shell.nix/flake.nix found but nix not on PATH" >&2; exit 1
fi
```

### premerge.sh (self-contained — do NOT source setup.sh)

```bash
#!/usr/bin/env bash
set -e
# [portable _SELF block]
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
WORKTREE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GIT_COMMON="$(git -C "$WORKTREE_ROOT" rev-parse --git-common-dir 2>&1)" || true
REPO_ROOT="$(cd "$GIT_COMMON/.." 2>/dev/null && pwd)"

nix-shell "$REPO_ROOT/shell.nix" --run \
  "bash -c 'cd $WORKTREE_ROOT && <install command> && <test command>'"
```

---

## Python

### Install command selection

| Condition | Command |
|---|---|
| `poetry.lock` | `poetry install --with dev` |
| `uv.lock` + `[dependency-groups]` | `uv sync --group dev` |
| `uv.lock` + `[project.optional-dependencies]` | `uv sync --extra dev` |
| `uv.lock`, no extras | `uv sync` |
| No lockfile, uv available | `uv pip install -e ".[dev]"` |
| No lockfile, no uv | `pip install ".[dev]"` |

Rules: check `poetry.lock` first → `uv.lock` next. Never `--no-cache`. Never `uv pip install` when `uv.lock` exists.

### setup.sh (3-layer additive strategy)

```bash
#!/usr/bin/env bash
set -e
_cp_tree() { cp -Rc "$@" 2>/dev/null || cp -r --reflink=auto "$@" 2>/dev/null || cp -r "$@"; }
# [portable _SELF block]
WORKTREE_ROOT="$(cd "$(dirname "$_SELF")/.." && pwd)"
VENV_DIR="$WORKTREE_ROOT/.venv"
_GIT_COMMON="$(git -C "$WORKTREE_ROOT" rev-parse --git-common-dir 2>&1)" || true
MAIN_VENV="$(cd "$_GIT_COMMON/.." 2>/dev/null && pwd)/.venv"

if [ ! -d "$VENV_DIR" ] || ! "$VENV_DIR/bin/python" --version >/dev/null 2>&1; then
  rm -rf "$VENV_DIR"; python3 -m venv "$VENV_DIR"
fi
cd "$WORKTREE_ROOT"

# Layer 1: copy from main repo venv (fast, offline)
if [ -d "$MAIN_VENV" ]; then
  MAIN_SITE="$(ls -d "$MAIN_VENV"/lib/python*/site-packages 2>/dev/null | head -1)"
  WORKTREE_SITE="$(ls -d "$VENV_DIR"/lib/python*/site-packages 2>/dev/null | head -1)"
  if [ "$(basename "$(dirname "$MAIN_SITE")")" = "$(basename "$(dirname "$WORKTREE_SITE")")" ]; then
    _cp_tree "$MAIN_SITE/." "$WORKTREE_SITE/"
  fi
fi
# Layer 2: install from cache (offline)
uv pip install --python "$VENV_DIR/bin/python" ".[dev]" --offline --quiet 2>/dev/null || true
# Layer 3: install with network
uv pip install --python "$VENV_DIR/bin/python" ".[dev]" --quiet 2>/dev/null || true
. "$VENV_DIR/bin/activate"
export PYTHONPATH="$WORKTREE_ROOT${PYTHONPATH:+:$PYTHONPATH}"
```

---

## Node

| Condition | Command |
|---|---|
| `pnpm-lock.yaml` | `pnpm install --frozen-lockfile` |
| `yarn.lock` | `yarn install --frozen-lockfile` |
| `package-lock.json` | `npm ci` |
| No lockfile | `npm install` |

### setup.sh

```bash
#!/usr/bin/env bash
set -e
_cp_tree() { cp -Rc "$@" 2>/dev/null || cp -r --reflink=auto "$@" 2>/dev/null || cp -r "$@"; }
# [portable _SELF block]
WORKTREE_ROOT="$(cd "$(dirname "$_SELF")/.." && pwd)"
MAIN_REPO="$(cd "$(git -C "$WORKTREE_ROOT" rev-parse --git-common-dir 2>&1)/.." 2>/dev/null && pwd)"
cd "$WORKTREE_ROOT"
# Layer 1: copy node_modules from main repo
[ ! -d node_modules ] && [ -d "$MAIN_REPO/node_modules" ] && _cp_tree "$MAIN_REPO/node_modules" node_modules
# Layer 2: offline install
npm install --prefer-offline --silent 2>/dev/null || true
# Layer 3: network install
npm ci --silent 2>/dev/null || true
export PATH="$WORKTREE_ROOT/node_modules/.bin:$PATH"
```

---

## Rust

```bash
#!/usr/bin/env bash
set -e
# [portable _SELF block]
WORKTREE_ROOT="$(cd "$(dirname "$_SELF")/.." && pwd)"
cd "$WORKTREE_ROOT"
cargo build --quiet
```
Premerge: `cargo test`.

## Go

```bash
#!/usr/bin/env bash
set -e
# [portable _SELF block]
WORKTREE_ROOT="$(cd "$(dirname "$_SELF")/.." && pwd)"
cd "$WORKTREE_ROOT"
go mod tidy
```
Premerge: `go test ./...`.

## Ruby

```bash
#!/usr/bin/env bash
set -e
# [portable _SELF block]
WORKTREE_ROOT="$(cd "$(dirname "$_SELF")/.." && pwd)"
cd "$WORKTREE_ROOT"
export BUNDLE_PATH="$WORKTREE_ROOT/vendor/bundle"
bundle install --path vendor/bundle --quiet
```
Premerge: `bundle exec rspec`.

---

## premerge.sh (non-Nix stacks)

```bash
#!/usr/bin/env bash
set -e
# [portable _SELF block]
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
WORKTREE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$SCRIPT_DIR/setup.sh"
cd "$WORKTREE_ROOT"
# Adapt: pytest / npm test / cargo test / go test ./... / bundle exec rspec
pytest tests/ -x -q
```
