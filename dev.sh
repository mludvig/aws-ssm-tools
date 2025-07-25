#!/bin/bash

# Unified development script for aws-ssm-tools using uv
# Replaces: build.sh, check.sh, upload.sh, and the old dev.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

usage() {
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Setup Commands:"
    echo "  install      Install project and dev dependencies"
    echo "  sync         Sync dependencies (update lockfile)"
    echo ""
    echo "Development Commands:"
    echo "  check [tool] Run linting and type checking (black, ruff, mypy, or all)"
    echo "  format       Format code with black and ruff"
    echo "  test         Run tests (if any)"
    echo ""
    echo "Build & Release Commands:"
    echo "  build        Build the package"
    echo "  upload       Build and upload to PyPI"
    echo "  clean        Clean build artifacts"
    echo ""
    echo "Version Commands:"
    echo "  bump <part>  Bump version (major, minor, patch, alpha, beta, rc)"
    echo "  version [ver] Show project version or set to specific version"
    echo ""
    echo "Utility Commands:"
    echo "  shell        Enter the project's virtual environment"
    echo "  run <cmd>    Run a command in the project environment"
    echo ""
    echo "Examples:"
    echo "  $0 install"
    echo "  $0 check"
    echo "  $0 check ruff"
    echo "  $0 format"
    echo "  $0 build"
    echo "  $0 upload"
    echo "  $0 bump patch"
    echo "  $0 bump minor"
    echo "  $0 version"
    echo "  $0 version 2.1.0"
    echo "  $0 run python -c 'import ssm_tools; print(ssm_tools.__version__)'"
}

get_version() {
    uv run python -c 'import ssm_tools; print(ssm_tools.__version__)'
}

set_version_directly() {
    local target_version="$1"
    local init_file="ssm_tools/__init__.py"
    local backup_file="$init_file.backup"

    # Simply restore from backup
    if [ -f "$backup_file" ]; then
        mv "$backup_file" "$init_file"
        echo "Restored version from backup"
        return 0
    else
        echo "Error: No backup file found"
        return 1
    fi
}

bump_version() {
    local part="${1:-patch}"

    if [ -z "$part" ]; then
        echo "Error: Version part required (major, minor, patch, alpha, beta, rc)"
        exit 1
    fi

    # Get current version and create backup
    local current_version
    current_version=$(get_version)
    echo "Current version: $current_version"

    # Create backup before making changes
    cp "ssm_tools/__init__.py" "ssm_tools/__init__.py.backup"

    # Use hatch to bump the version
    echo "Bumping $part version..."
    uv run hatch version "$part"

    # Get the new version that hatch actually created
    local new_version
    new_version=$(get_version)
    echo "New version: $new_version"

    # Ask if user is happy with the new version
    echo ""
    read -p "Accept version $new_version? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Reverting to previous version..."
        # Revert by restoring the backup
        if set_version_directly "$current_version"; then
            echo "Version reverted to $current_version"
        else
            echo "Failed to revert version"
        fi
        return 0
    fi

    # Remove backup since we're keeping the changes
    rm -f "ssm_tools/__init__.py.backup"

    # Check if we're in a git repository
    if ! git rev-parse --git-dir > /dev/null 2>&1; then
        echo "Not in a git repository. Version updated but no commit/tag created."
        return 0
    fi

    # Check if there are any changes to commit
    if git diff-index --quiet HEAD --; then
        echo "No changes to commit (version might already be at target)."
        return 0
    fi

    # Ask for confirmation for git operations
    echo ""
    read -p "Commit and tag version v$new_version? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Version updated but not committed."
        return 0
    fi

    # Commit the version change
    git add ssm_tools/__init__.py
    git commit -m "Version v$new_version"

    # Create and push the tag
    git tag "v$new_version"

    echo "Created commit and tag for version v$new_version"
    echo "Don't forget to push: git push && git push --tags"
}

set_version() {
    local target_version="$1"

    if [ -z "$target_version" ]; then
        echo "Error: Target version required"
        exit 1
    fi

    # Get current version and create backup
    local current_version
    current_version=$(get_version)
    echo "Current version: $current_version"

    # Create backup before making changes
    cp "ssm_tools/__init__.py" "ssm_tools/__init__.py.backup"

    # Use hatch to set the specific version
    echo "Setting version to $target_version..."
    uv run hatch version "$target_version"

    # Get the new version that hatch actually created
    local new_version
    new_version=$(get_version)
    echo "New version: $new_version"

    # Ask if user is happy with the new version
    echo ""
    read -p "Accept version $new_version? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Reverting to previous version..."
        # Revert by restoring the backup
        if set_version_directly "$current_version"; then
            echo "Version reverted to $current_version"
        else
            echo "Failed to revert version"
        fi
        return 0
    fi

    # Remove backup since we're keeping the changes
    rm -f "ssm_tools/__init__.py.backup"

    # Check if we're in a git repository
    if ! git rev-parse --git-dir > /dev/null 2>&1; then
        echo "Not in a git repository. Version updated but no commit/tag created."
        return 0
    fi

    # Check if there are any changes to commit
    if git diff-index --quiet HEAD --; then
        echo "No changes to commit (version might already be at target)."
        return 0
    fi

    # Ask for confirmation for git operations
    echo ""
    read -p "Commit and tag version v$new_version? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Version updated but not committed."
        return 0
    fi

    # Commit the version change
    git add ssm_tools/__init__.py
    git commit -m "Version v$new_version"

    # Create and push the tag
    git tag "v$new_version"

    echo "Created commit and tag for version v$new_version"
    echo "Don't forget to push: git push && git push --tags"
}

run_checks() {
    local tool="${1:-all}"
    local python_scripts

    echo "Finding Python scripts..."
    python_scripts="$(grep -l '#!/usr/bin/env python3' ssm-* ecs-* ec2-* 2>/dev/null || true)"

    case "$tool" in
        black|all)
            echo "Running black..."
            if [ -n "$python_scripts" ]; then
                uv run black --line-length 120 --check --diff $python_scripts ssm_tools/*.py
            else
                uv run black --line-length 120 --check --diff ssm_tools/*.py
            fi
            [ "$tool" = "black" ] && return 0
            ;;
    esac

    case "$tool" in
        mypy|all)
            echo "Running mypy..."
            if [ -n "$python_scripts" ]; then
                uv run mypy --scripts-are-modules --ignore-missing-imports $python_scripts
            fi
            uv run mypy --ignore-missing-imports ssm_tools/
            [ "$tool" = "mypy" ] && return 0
            ;;
    esac

    case "$tool" in
        ruff|all)
            echo "Running ruff..."
            uv run ruff check .
            [ "$tool" = "ruff" ] && return 0
            ;;
    esac

    if [ "$tool" != "all" ] && [ "$tool" != "black" ] && [ "$tool" != "mypy" ] && [ "$tool" != "ruff" ]; then
        echo "Unknown tool: $tool"
        echo "Available tools: black, mypy, ruff, all"
        exit 1
    fi
}

case "${1:-}" in
    install)
        echo "Installing project and development dependencies..."
        uv sync --dev
        ;;
    sync)
        echo "Syncing dependencies..."
        uv sync --dev
        ;;
    check)
        echo "Running code quality checks..."
        run_checks "${2:-all}"
        ;;
    format)
        echo "Formatting code..."
        python_scripts="$(grep -l '#!/usr/bin/env python3' ssm-* ecs-* ec2-* 2>/dev/null || true)"
        if [ -n "$python_scripts" ]; then
            uv run black --line-length 120 $python_scripts ssm_tools/*.py
        else
            uv run black --line-length 120 ssm_tools/*.py
        fi
        uv run ruff check --fix .
        ;;
    test)
        echo "Running tests..."
        # Add test command here when tests are available
        echo "No tests configured yet"
        ;;
    build)
        echo "Building package..."
        echo "Cleaning previous builds..."
        rm -rf dist/ build/ *.egg-info/ tmp-agent-build/
        echo "Building with uv..."
        uv build
        echo "Build complete. Packages in dist/:"
        ls -la dist/
        ;;
    upload)
        echo "Building and uploading package..."
        # Build first
        rm -rf dist/ build/ *.egg-info/ tmp-agent-build/
        uv build

        # Get version and upload
        version=$(get_version)
        echo "Checking packages for version $version..."
        uv run twine check dist/*${version}*

        echo "Uploading to PyPI..."
        uv run twine upload dist/*${version}*
        ;;
    clean)
        echo "Cleaning build artifacts..."
        rm -rf dist/ build/ *.egg-info/ .pytest_cache/ .mypy_cache/ .ruff_cache/ tmp-agent-build/
        find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
        ;;
    shell)
        echo "Entering virtual environment shell..."
        uv shell
        ;;
    run)
        shift
        if [ $# -eq 0 ]; then
            echo "Error: No command specified for 'run'"
            usage
            exit 1
        fi
        uv run "$@"
        ;;
    bump)
        if [ -z "${2:-}" ]; then
            echo "Error: Version part required"
            echo "Usage: $0 bump <part>"
            echo "Parts: major, minor, patch, alpha, beta, rc"
            exit 1
        fi
        bump_version "$2"
        ;;
    version)
        if [ -n "${2:-}" ]; then
            # Set specific version
            set_version "$2"
        else
            # Show current version
            get_version
        fi
        ;;
    "")
        usage
        ;;
    *)
        echo "Error: Unknown command '$1'"
        echo ""
        usage
        exit 1
        ;;
esac
