import os

__version__ = "3.1.0"

if os.name == "nt":
    # Don't run ssh, aws or the plugin from the cwd, nor let our children. See docs/adr/0026-windows-program-lookup.md
    os.environ.setdefault("NoDefaultCurrentDirectoryInExePath", "1")
