#!/bin/bash -xe

VERSION=$(python -c 'import ssm_tools; print(ssm_tools.__version__)')
twine check dist/*${VERSION}*
twine upload dist/*${VERSION}*
