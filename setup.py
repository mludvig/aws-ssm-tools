import pathlib

from setuptools import setup, find_packages

import ssm_tools

HERE = pathlib.Path(__file__).parent
README = (HERE / "README.md").read_text()

SCRIPTS=[
    'ssm-session',
    'ssm-copy',
    'ssm-tunnel',
]

setup(
    name="aws-ssm-tools",
    version=ssm_tools.__version__,
    packages=find_packages(),
    scripts=SCRIPTS+[
        'ssm-tunnel-agent'
    ],

    python_requires='>=3.6',

    install_requires=[
        'botocore',
        'boto3',
        'pexpect',
    ],

    package_data={
        '': ['*.txt', '*.md'],
    },

    author="Michael Ludvig",
    author_email="mludvig@logix.net.nz",
    description="Tools for AWS Systems Manager: "+" ".join(SCRIPTS),
    long_description=README,
    long_description_content_type="text/markdown",
    license="Apache License 2.0",
    keywords="aws ssm "+" ".join(SCRIPTS),
    url="https://github.com/mludvig/aws-ssm-tools",
    project_urls={
        "Bug Tracker": "https://github.com/mludvig/aws-ssm-tools/issues",
        "Documentation": "https://github.com/mludvig/aws-ssm-tools/blob/master/README.md",
        "Source Code": "https://github.com/mludvig/aws-ssm-tools",
    }
)
