"""Wszystkie Mach-O muszą zawierać Intel i ARM, a zależności nie mogą
wskazywać środowiska programistycznego. Zapis audytu i podpisu do JSON.
"""
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys
from macholib.MachO import MachO

app=Path(sys.argv[1]).resolve()
output=Path(sys.argv[2])
report={'app':str(app),'binaries':[],'errors':[]}
for file in app.rglob('*'):
    if not file.is_file() or file.is_symlink():
        continue
    with file.open('rb') as stream:
        magic=stream.read(4)
    if magic not in (b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca'):
        continue
    macho=MachO(str(file))
    arches=[];minimum={}
    for header in macho.headers:
        arch={16777223:'x86_64',16777228:'arm64'}.get(header.header.cputype,str(header.header.cputype))
        arches.append(arch)
        for lc,cmd,data in header.commands:
            version=getattr(cmd,'minos',None) or getattr(cmd,'version',None)
            if lc.cmd in (0x24,0x32) and version is not None:
                minimum[arch]=f'{version>>16}.{(version>>8)&255}.{version&255}'
    if set(arches)!={'arm64','x86_64'}:
        report['errors'].append(f'Brak architektury: {file.relative_to(app)} {arches}')
    deps=subprocess.check_output(['otool','-L',str(file)],text=True,stderr=subprocess.DEVNULL)
    libraries=re.findall(r'^\s+(.*?) \(compatibility',deps,re.M)
    if any('/Users/' in dep or '/Library/Frameworks/Python.framework' in dep for dep in libraries):
        report['errors'].append(f'Zewnętrzna zależność: {file.relative_to(app)}')
    report['binaries'].append({'path':str(file.relative_to(app)),'architectures':arches,'minimum':minimum})
plist=plistlib.loads((app/'Contents/Info.plist').read_bytes())
report['bundle']={k:plist.get(k) for k in ['CFBundleIdentifier','CFBundleVersion','LSMinimumSystemVersion']}
check=subprocess.run(['codesign','--verify','--deep','--strict',str(app)],capture_output=True,text=True)
report['signature_valid']=check.returncode==0
report['signature_details']=subprocess.run(['codesign','-dv','--verbose=4',str(app)],capture_output=True,text=True).stderr
if not report['signature_valid']:
    report['errors'].append(check.stderr)
report['binary_count']=len(report['binaries'])
report['passed']=not report['errors']
output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print('Mach-O:',report['binary_count'],'Universal:',report['passed'],'podpis:',report['signature_valid'])
if report['errors']:
    print(*report['errors'],sep='\n')
raise SystemExit(0 if report['passed'] else 1)
