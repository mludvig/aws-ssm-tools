#!/bin/bash -e

# Linter and formatter scripts. Used by .circleci/config.yml

# This can also be called with {black, pylint, mypy} as a parameter
# to run only the specific checks.

set -x

PYTHON_SCRIPTS="$(grep -l '#!/usr/bin/env python3' ssm-* ecs-* ec2-*) setup.py"

test -z "$1" -o "$1" == "black" && black --line-length 250 --check --diff ${PYTHON_SCRIPTS} ssm_tools/*.py

test -z "$1" -o "$1" == "pylint" && pylint --exit-zero --disable=invalid-name,missing-docstring,line-too-long ${PYTHON_SCRIPTS} ssm_tools/*.py ssm-tunnel-agent
test -z "$1" -o "$1" == "pylint" && pylint --errors-only ${PYTHON_SCRIPTS} ssm_tools/*.py ssm-tunnel-agent

test -z "$1" -o "$1" == "mypy" && mypy --scripts-are-modules --ignore-missing-imports ${PYTHON_SCRIPTS}
