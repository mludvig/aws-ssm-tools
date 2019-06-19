#!/bin/bash -xe

python3 setup.py sdist bdist_wheel
python3 setup-agent.py sdist bdist_wheel --universal
