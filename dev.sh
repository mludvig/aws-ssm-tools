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
    echo "Utility Commands:"
    echo "  shell        Enter the project's virtual environment"
    echo "  run <cmd>    Run a command in the project environment"
    echo "  version      Show project version"
    echo ""
    echo "Examples:"
    echo "  $0 install"
    echo "  $0 check"
    echo "  $0 check ruff"
    echo "  $0 format"
    echo "  $0 build"
    echo "  $0 upload"
    echo "  $0 run python -c 'import ssm_tools; print(ssm_tools.__version__)'"
}

get_version() {
    uv run python -c 'import ssm_tools; print(ssm_tools.__version__)'
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
    version)
        get_version
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
