#!/bin/bash -e

# Linter and formatter scripts. Used by .circleci/config.yml

# This can also be called with {black, ruff, mypy} as a parameter
# to run only the specific checks.

set -x

PYTHON_SCRIPTS="$(grep -l '#!/usr/bin/env python3' ssm-* ecs-* ec2-*) setup.py"

test -z "$1" -o "$1" == "black" && black --line-length 120 --check --diff ${PYTHON_SCRIPTS} ssm_tools/*.py

test -z "$1" -o "$1" == "mypy" && mypy --scripts-are-modules --ignore-missing-imports ${PYTHON_SCRIPTS}

test -z "$1" -o "$1" == "ruff" && ruff check .
