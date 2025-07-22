# Development Guide

This project uses [uv](https://docs.astral.sh/uv/) for dependency management and development tasks, with a unified `dev.sh` script for all development operations.

## Prerequisites

Install uv:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Quick Start

1. **Set up the development environment:**
   ```bash
   ./dev.sh install
   ```

2. **Run code quality checks:**
   ```bash
   ./dev.sh check
   ```

3. **Format code:**
   ```bash
   ./dev.sh format
   ```

4. **Build the package:**
   ```bash
   ./dev.sh build
   ```

## Development Commands

The `dev.sh` script provides all development functionality in one place:

### Setup Commands
- `./dev.sh install` - Install project and dev dependencies
- `./dev.sh sync` - Sync dependencies (update lockfile)

### Development Commands
- `./dev.sh check [tool]` - Run linting and type checking
  - `./dev.sh check` - Run all checks (black, ruff, mypy)
  - `./dev.sh check ruff` - Run only ruff
  - `./dev.sh check black` - Run only black
  - `./dev.sh check mypy` - Run only mypy
- `./dev.sh format` - Format code with black and ruff
- `./dev.sh test` - Run tests (when available)

### Build & Release Commands
- `./dev.sh build` - Build the package
- `./dev.sh upload` - Build and upload to PyPI
- `./dev.sh clean` - Clean build artifacts

### Utility Commands
- `./dev.sh shell` - Enter the project's virtual environment
- `./dev.sh run <cmd>` - Run a command in the project environment
- `./dev.sh version` - Show project version

## Examples

```bash
# Development workflow
./dev.sh install
./dev.sh format
./dev.sh check
./dev.sh build

# Run specific tools
./dev.sh check ruff
./dev.sh run python -c "import ssm_tools; print(ssm_tools.__version__)"

# Release workflow
./dev.sh check
./dev.sh upload
```

## Using uv directly

You can still use uv commands directly if needed:

- `uv sync --dev` - Install dependencies
- `uv run ruff check .` - Run ruff linter
- `uv run black .` - Format code with black
- `uv run mypy .` - Run type checking
- `uv build` - Build the package

## Project Structure

The project is now configured with:

- `pyproject.toml` - Project configuration, dependencies, and tool settings
- `uv.lock` - Lock file with exact dependency versions
- `.python-version` - Python version specification for uv
- `dev.sh` - Unified development script (replaces build.sh, check.sh, upload.sh)

## Migration Notes

This project has been modernized to use a unified development workflow:

- ✅ `dev.sh` - Single script for all development tasks
- ❌ `build.sh` - Functionality moved to `./dev.sh build`
- ❌ `check.sh` - Functionality moved to `./dev.sh check`
- ❌ `upload.sh` - Functionality moved to `./dev.sh upload`

The old `setup.py` and separate agent package have been unified into a single modern package with all tools included.

## Virtual Environment

uv automatically manages a virtual environment in `.venv/`. You can activate it manually:

```bash
source .venv/bin/activate
```

Or use `./dev.sh shell` to enter an interactive shell with the environment activated.
