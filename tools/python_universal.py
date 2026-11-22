"""Uruchamia rozpakowany oficjalny Python bez instalacji do /Library.
To narzędzie budowy; gotowa aplikacja nie używa tych ścieżek ani zmiennych.
"""
import os
from pathlib import Path
import subprocess
import sys
base=Path(__file__).resolve().parents[1]
runtime=base/'build-support'/'runtime'
prefix=runtime/'Python.framework'/'Versions'/'3.13'
frameworks=prefix/'Frameworks'
env=os.environ.copy()
env['DYLD_FRAMEWORK_PATH']=str(runtime)+':'+str(frameworks)
env['PYTHONHOME']=str(prefix)
env['DYLD_LIBRARY_PATH']=str(prefix/'lib')
env['TCL_LIBRARY']=str(frameworks/'Tcl.framework'/'Versions'/'8.6'/'Resources'/'Scripts')
env['TK_LIBRARY']=str(frameworks/'Tk.framework'/'Versions'/'8.6'/'Resources'/'Scripts')
# Cache narzędzi budowy jest lokalny, nie w katalogach systemowych.
env['PYINSTALLER_CONFIG_DIR']=str(base/'build-support'/'pyinstaller-cache')
raise SystemExit(subprocess.call([str(prefix/'bin'/'python3.13'),*sys.argv[1:]],env=env))
