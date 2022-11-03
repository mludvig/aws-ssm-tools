# aws-ssm-tools packaging

import os
import sys
import pathlib

from setuptools import setup, find_packages
from setuptools.command.install import install

import ssm_tools

HERE = pathlib.Path(__file__).parent
README = (HERE / "README.md").read_text()

SCRIPTS = [
    "ec2-session",
    "ecs-session",
    "ec2-ssh",
    "ssm-tunnel",
    # Renamed, deprecated and soon to be removed...
    "ssm-session",
    "ssm-ssh",
]
VERSION = ssm_tools.__version__

requirements = HERE / "requirements.txt"
with requirements.open() as f:
    reqs = [req.strip() for req in f.readlines() if req.strip() and not req.startswith("#")]


class VerifyVersionCommand(install):
    """Custom command to verify that the git tag matches our version"""

    description = "verify that the git tag matches our version"

    def run(self) -> None:
        tag = os.getenv("CIRCLE_TAG")
        if not tag:
            sys.exit("Env var $CIRCLE_TAG is not defined - are we running a CircleCI build?")

        if tag.startswith("v"):  # If tag is v1.2.3 make it 1.2.3
            tag = tag[1:]

        if tag != VERSION:
            info = f"Git tag: {tag} does not match the version of this app: {VERSION}"
            sys.exit(info)


def console_scripts() -> list:
    scripts = []
    for script in SCRIPTS:
        # All script entries must be in this format:
        # "ec2-session = ssm_tools.ec2_session_cli:main"
        if script in ["ec2-session", "ec2-ssh"]:
            scripts.append(f"{script} = ssm_tools.{script.replace('-','_').replace('ec2','ssm')}_cli:main")
        else:
            scripts.append(f"{script} = ssm_tools.{script.replace('-','_')}_cli:main")
    return scripts


setup(
    name="aws-ssm-tools",
    version=VERSION,
    packages=find_packages(),
    entry_points={
        "console_scripts": console_scripts(),
    },
    python_requires=">=3.6",
    install_requires=reqs,
    package_data={
        "": ["*.txt", "*.md", "ssm-tunnel-updown.dns-example", "LICENSE"],
    },
    author="Michael Ludvig",
    author_email="mludvig@logix.net.nz",
    description="Tools for AWS Systems Manager: " + " ".join(SCRIPTS),
    long_description=README,
    long_description_content_type="text/markdown",
    license="Apache License 2.0",
    keywords="aws ssm " + " ".join(SCRIPTS),
    url="https://github.com/mludvig/aws-ssm-tools",
    project_urls={
        "Bug Tracker": "https://github.com/mludvig/aws-ssm-tools/issues",
        "Documentation": "https://github.com/mludvig/aws-ssm-tools/blob/master/README.md",
        "Source Code": "https://github.com/mludvig/aws-ssm-tools",
    },
    classifiers=[
        "Environment :: Console",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: Apache Software License",
        "Development Status :: 5 - Production/Stable",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: System :: Systems Administration",
        "Topic :: System :: Networking",
    ],
    cmdclass={
        "verify": VerifyVersionCommand,
    },
)
