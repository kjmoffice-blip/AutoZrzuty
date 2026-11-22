"""Buduje archiwum źródeł bez zależności, konfiguracji i prywatnych zrzutów."""
from hashlib import sha256
import json
from pathlib import Path
import zipfile

BASE = Path(__file__).resolve().parents[1]
VERSION = '1.0.1'
DIST = BASE/'dist'
DIST.mkdir(exist_ok=True)
archive = DIST/f'AutoZrzuty-{VERSION}-zrodla.zip'
files = [p for p in BASE.rglob('*') if p.is_file()
         and p.relative_to(BASE).parts[0] in ('src', 'tools', 'assets', 'licenses', 'source-libraries')
         and '__pycache__' not in p.parts and p.suffix != '.pyc']
files += [BASE/n for n in ('README.md', 'INSTALACJA.txt', 'INSTALACJA_DMG.txt',
                          'THIRD_PARTY_LICENSES.txt', 'RIGHTS.md', 'LICENSE', '.gitignore')]
files = sorted(set(files))
manifest = {path.relative_to(BASE).as_posix():sha256(path.read_bytes()).hexdigest()
            for path in files}
# 'x' zachowuje wcześniejsze, sprawdzone wydania.
with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as bundle:
    for path in files:
        bundle.write(path,f'AutoZrzuty-{VERSION}/{path.relative_to(BASE).as_posix()}')
    bundle.writestr(f'AutoZrzuty-{VERSION}/MANIFEST_SHA256.json',
                    json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
with zipfile.ZipFile(archive) as bundle:
    if bundle.testzip() is not None:
        raise RuntimeError('Błąd integralności ZIP.')
    for relative, digest in manifest.items():
        content=bundle.read(f'AutoZrzuty-{VERSION}/{relative}')
        if sha256(content).hexdigest()!=digest:
            raise RuntimeError(f'Niezgodność archiwum: {relative}')
checksums = {archive.name:sha256(archive.read_bytes()).hexdigest()}
dmg = DIST/f'AutoZrzuty-{VERSION}-Universal.dmg'
if dmg.exists():
    checksums[dmg.name] = sha256(dmg.read_bytes()).hexdigest()
(DIST/f'SHA256-{VERSION}.txt').write_text(''.join(f'{digest}  {name}\n' for name,digest in checksums.items()))
print(f'ZIP sprawdzony: {archive}; {len(files)} plików; {archive.stat().st_size} bajtów')
