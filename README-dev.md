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

### Version Commands
- `./dev.sh bump <part>` - Bump version (major, minor, patch, alpha, beta, rc)
- `./dev.sh version` - Show project version
- `./dev.sh version <version>` - Set specific version

### Utility Commands
- `./dev.sh shell` - Enter the project's virtual environment
- `./dev.sh run <cmd>` - Run a command in the project environment

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

# Version management
./dev.sh version                    # Show current version
./dev.sh bump patch                 # Bump patch version (e.g., 1.0.0 -> 1.0.1)
./dev.sh bump minor                 # Bump minor version (e.g., 1.0.0 -> 1.1.0)
./dev.sh bump major                 # Bump major version (e.g., 1.0.0 -> 2.0.0)
./dev.sh bump alpha                 # Bump to alpha (e.g., 1.0.0 -> 1.0.1a1)
./dev.sh version 2.1.0              # Set specific version

# Release workflow
./dev.sh check
./dev.sh bump patch                 # Or bump minor/major as needed
./dev.sh upload
```

## Version Management

The project uses [hatch](https://hatch.pypa.io/) for version management with convenient commands:

### Version Bumping
```bash
./dev.sh bump patch    # 1.0.0 -> 1.0.1
./dev.sh bump minor    # 1.0.0 -> 1.1.0
./dev.sh bump major    # 1.0.0 -> 2.0.0
./dev.sh bump alpha    # 1.0.0 -> 1.0.1a1
./dev.sh bump beta     # 1.0.0 -> 1.0.1b1
./dev.sh bump rc       # 1.0.0 -> 1.0.1rc1
```

### Setting Specific Versions
```bash
./dev.sh version 2.1.0         # Set to specific version
./dev.sh version 2.0.0-alpha1  # Set to pre-release (becomes 2.0.0a1)
```

### Version Workflow
1. The command shows the current version
2. Hatch updates the version in `ssm_tools/__init__.py`
3. Shows what version hatch actually created (may normalize format)
4. Asks if you accept the new version
5. If not, reverts to the original version
6. If yes, asks whether to commit and tag
7. Creates git commit with message "Version v{version}" and tags it

**Note**: Hatch may normalize version strings (e.g., `2.0.0-alpha1` becomes `2.0.0a1`). The script handles this by showing you the actual version created and using that for commit messages and tags.

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
