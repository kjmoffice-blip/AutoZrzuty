"""Relokuje kopię oficjalnego Python.framework w katalogu budowy.
Nie modyfikuje /Library ani innych aplikacji. Pozwala działać podprocesom
PyInstaller, które celowo usuwają zmienne DYLD ze środowiska.
"""
import os
from pathlib import Path
import re
import subprocess

base=Path(__file__).resolve().parents[1]
framework=base/'build-support'/'runtime'/'Python.framework'
old='/Library/Frameworks/Python.framework/'
count=0
for file in framework.rglob('*'):
    if not file.is_file() or file.is_symlink():
        continue
    with file.open('rb') as stream:
        magic=stream.read(4)
    if magic not in (b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca'):
        continue
    text=subprocess.check_output(['otool','-L',str(file)],text=True)
    deps=set(re.findall(r'^\s+(/Library/Frameworks/Python\.framework/.*?) \(compatibility',text,re.M))
    changes=[]
    for dep in sorted(deps):
        target=framework/dep.removeprefix(old)
        if not target.exists():
            raise RuntimeError(f'Brak zależności {target}')
        relative=os.path.relpath(target,file.parent)
        changes.extend(['-change',dep,'@loader_path/'+relative])
    if changes:
        subprocess.run(['install_name_tool',*changes,str(file)],check=True,capture_output=True)
        subprocess.run(['codesign','--force','--sign','-',str(file)],check=True,capture_output=True)
        count+=1
print('Relokowane binaria lokalnego Pythona:',count)
