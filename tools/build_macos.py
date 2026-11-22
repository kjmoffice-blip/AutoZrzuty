"""Odbudowa Universal .app i DMG. Uruchom na Macu: python3 tools/build_macos.py.
Potrzebny internet do pobrania oficjalnych komponentów i Xcode Command Line Tools.
Nie instaluje Pythona do /Library ani nie zmienia globalnych zabezpieczeń.
"""
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import sys

BASE=Path(__file__).resolve().parents[1]
BUILD=BASE/'build-support'
VERSION='1.0.1'
PYTHON_VERSION='3.13.16'
PYTHON_SHA='30666509020b4da0dd8bc2e773255f34d76b7bb80b66960a928d5f6daa0192d7'
PYTHON_URL=f'https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-macos11.pkg'
RUNNER=BASE/'tools/python_universal.py'

def run(args):
    subprocess.run([str(a) for a in args],cwd=BASE,check=True)

def py(*args):
    run([sys.executable,RUNNER,*args])

if (BASE/f'dist/AutoZrzuty-{VERSION}-Universal.dmg').exists():
    raise SystemExit('To wydanie DMG już istnieje. Zmień numer wersji, aby zachować poprzedni instalator.')
BUILD.mkdir(exist_ok=True)
runtime=BUILD/'runtime/Python.framework'
if not runtime.exists():
    pkg=BUILD/f'python-{PYTHON_VERSION}-macos11.pkg'
    if not pkg.exists():
        run(['curl','--fail','--location','--retry','1','--connect-timeout','20',
             '--max-time','180',PYTHON_URL,'-o',pkg])
    if sha256(pkg.read_bytes()).hexdigest()!=PYTHON_SHA:
        raise SystemExit('Suma pobranego Pythona nie zgadza się z oficjalnym wydaniem.')
    extracted=BUILD/'python-extracted'
    if not extracted.exists():
        run(['pkgutil','--expand-full',pkg,extracted])
    runtime.parent.mkdir(exist_ok=True)
    run(['ditto',extracted/'Python_Framework.pkg/Payload',runtime])
run([sys.executable,BASE/'tools/relocate_python.py'])
py('-m','ensurepip')
py('-m','pip','install','--disable-pip-version-check','--only-binary=:all:',
   '-r',BASE/'src/requirements.txt','pyinstaller==6.22.3','delocate==0.13.0')
for arch,platform_tag in [('arm','macosx_11_0_arm64'),('intel','macosx_11_0_x86_64')]:
    dest=BUILD/f'wheels-{arch}'
    dest.mkdir(exist_ok=True)
    py('-m','pip','download','--only-binary=:all:','--no-deps','--platform',platform_tag,
       '--python-version','313','--abi','cp313','Pillow==12.3.0','-d',dest)
merged=BUILD/'wheels-universal';merged.mkdir(exist_ok=True)
if not list(merged.glob('*.whl')):
    run([sys.executable,RUNNER,'-m','delocate.cmd.delocate_merge','-w',merged,
         next((BUILD/'wheels-arm').glob('pillow*.whl')),
         next((BUILD/'wheels-intel').glob('pillow*.whl'))])
py('-m','pip','install','--no-index','--force-reinstall','--no-deps',next(merged.glob('*.whl')))
# Użyj dołączonych źródeł LGPL, również po lokalnych zmianach użytkownika.
site_packages=runtime/'Versions/3.13/lib/python3.13/site-packages'
for package in ('pynput','pystray'):
    local=BASE/'source-libraries'/package
    if local.exists():
        shutil.copytree(local,site_packages/package,dirs_exist_ok=True)
py('-m','PyInstaller','--clean','--noconfirm','--distpath',BASE/f'dist/macos-{VERSION}',
   '--workpath',BUILD/'pyinstaller-build',BASE/'tools/AutoZrzuty.spec')
app=BASE/f'dist/macos-{VERSION}/AutoZrzuty.app'
# Końcowe informacje licencyjne są zachowane także wewnątrz .app.
run(['ditto',BASE/'licenses',app/'Contents/Resources/licenses'])
shutil.copy2(BASE/'THIRD_PARTY_LICENSES.txt',app/'Contents/Resources/THIRD_PARTY_LICENSES.txt')
run(['codesign','--force','--sign','-',app])
run(['codesign','--verify','--deep','--strict',app])
stage=BUILD/f'dmg-stage-{VERSION}'
if stage.exists():
    raise SystemExit(f'Staging już istnieje: {stage}. Użyj nowego numeru wydania, aby zachować stare pakiety.')
stage.mkdir()
run(['ditto',app,stage/'AutoZrzuty.app'])
(stage/'Applications').symlink_to('/Applications')
shutil.copy2(BASE/'INSTALACJA_DMG.txt',stage/'INSTALACJA.txt')
run(['hdiutil','create','-volname',f'AutoZrzuty {VERSION}','-srcfolder',stage,
     '-ov','-format','UDZO',BASE/f'dist/AutoZrzuty-{VERSION}-Universal.dmg'])
print('Gotowe .app i DMG. Przed dostarczeniem wykonaj audit_app.py oraz test zamontowanego DMG.')
