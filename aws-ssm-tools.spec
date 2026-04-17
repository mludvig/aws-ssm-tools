# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for building Windows executables.
#
# Produces a single dist/aws-ssm-tools/ folder:
#   ec2-session.exe, ec2-ssh.exe, ecs-session.exe, ssm-port-forward.exe
#   _internal/   <- shared Python runtime, DLLs, botocore data
#
# Before running: generate the _entry_*.py entry-point stubs (see ci.yml).
# Usage: pyinstaller aws-ssm-tools.spec --distpath dist

import botocore
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

# Only bundle botocore service data for the services we actually use
_botocore_data = Path(botocore.__file__).parent / "data"
_services = ["ssm", "ec2", "ecs", "sts", "sso", "sso-oidc"]
datas = [
    (str(_botocore_data / svc), f"botocore/data/{svc}") for svc in _services
]

common = dict(
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("botocore"),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,  # store compiled bytecode in PYZ archive (faster load)
    optimize=2,       # strip docstrings and asserts
)

a_ec2_session = Analysis(["_entry_ec2_session.py"],     **common)
a_ec2_ssh     = Analysis(["_entry_ec2_ssh.py"],         **common)
a_ecs_session = Analysis(["_entry_ecs_session.py"],     **common)
a_ssm_pf      = Analysis(["_entry_ssm_port_forward.py"], **common)

pyz_ec2_session = PYZ(a_ec2_session.pure, optimize=2)
pyz_ec2_ssh     = PYZ(a_ec2_ssh.pure,     optimize=2)
pyz_ecs_session = PYZ(a_ecs_session.pure, optimize=2)
pyz_ssm_pf      = PYZ(a_ssm_pf.pure,     optimize=2)

exe_ec2_session = EXE(pyz_ec2_session, a_ec2_session.scripts, [],
                      exclude_binaries=True, name="ec2-session",      console=True, optimize=2)
exe_ec2_ssh     = EXE(pyz_ec2_ssh,     a_ec2_ssh.scripts,     [],
                      exclude_binaries=True, name="ec2-ssh",          console=True, optimize=2)
exe_ecs_session = EXE(pyz_ecs_session, a_ecs_session.scripts, [],
                      exclude_binaries=True, name="ecs-session",      console=True, optimize=2)
exe_ssm_pf      = EXE(pyz_ssm_pf,     a_ssm_pf.scripts,      [],
                      exclude_binaries=True, name="ssm-port-forward", console=True, optimize=2)

# All four exes share one output directory; DLLs and botocore data are deduplicated
coll = COLLECT(
    exe_ec2_session, a_ec2_session.binaries, a_ec2_session.datas,
    exe_ec2_ssh,     a_ec2_ssh.binaries,     a_ec2_ssh.datas,
    exe_ecs_session, a_ecs_session.binaries, a_ecs_session.datas,
    exe_ssm_pf,      a_ssm_pf.binaries,      a_ssm_pf.datas,
    name="aws-ssm-tools",
    strip=False,
    upx=False,
)
