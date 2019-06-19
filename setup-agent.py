import pathlib

from setuptools import setup, find_packages

import ssm_tools

HERE = pathlib.Path(__file__).parent
README = (HERE / "README-agent.md").read_text()

setup(
    name="aws-ssm-tunnel-agent",
    version=ssm_tools.__version__,
    scripts=[
        'ssm-tunnel-agent'
    ],

    python_requires='>=2.7',

    install_requires=[ ],

    author="Michael Ludvig",
    author_email="mludvig@logix.net.nz",
    description="ssm-tunnel-agent for ssm-tunnel script from aws-ssm-tools package",
    long_description=README,
    long_description_content_type="text/markdown",
    license="Apache License 2.0",
    keywords="aws ssm ssm-tunnel ssm-tunnel-agent",
    url="https://github.com/mludvig/aws-ssm-tools",
    project_urls={
        "Bug Tracker": "https://github.com/mludvig/aws-ssm-tools/issues",
        "Documentation": "https://github.com/mludvig/aws-ssm-tools/blob/master/README.md",
        "Source Code": "https://github.com/mludvig/aws-ssm-tools",
    }
)
