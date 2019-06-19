#!/bin/bash -xe

python3 setup.py clean --all
python3 setup.py sdist bdist_wheel

# Always run 'clean' before building agent to prevent
# inclusion of unneeded files!
python3 setup.py clean --all
python3 setup-agent.py sdist bdist_wheel --universal
