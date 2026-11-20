"""Prepare the pinned Qlib MSVC/SDK tools without relying on a previous run."""
import argparse
import hashlib
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / '.cache'
TOOLS = CACHE / 'tools'
REQUIRED = (
    'msvc/VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64/cl.exe',
    'msvc/VC/Tools/MSVC/14.44.35207/include/vector',
    'msvc/VC/Tools/MSVC/14.44.35207/lib/x64/msvcprt.lib',
    'microsoft.windows.sdk.cpp/c/Include/10.0.26100.0/um/Windows.h',
    'microsoft.windows.sdk.cpp.x64/c/um/x64/kernel32.lib',
    'microsoft.windows.sdk.cpp.x64/c/ucrt/x64/ucrt.lib',
)


def missing():
    return [name for name in REQUIRED if not (TOOLS / name).is_file()]


def prepare():
    if not missing():
        return TOOLS
    CACHE.mkdir(parents=True, exist_ok=True)
    assets = json.loads((ROOT / 'assets.lock.json').read_text(encoding='utf-8'))
    for asset in assets:
        archive = CACHE / asset['filename']
        if not archive.is_file():
            temporary = archive.with_suffix(archive.suffix + '.partial')
            with urllib.request.urlopen(asset['url'], timeout=90) as response, temporary.open('wb') as target:
                shutil.copyfileobj(response, target)
            with temporary.open('rb') as source:
                digest = hashlib.file_digest(source, asset['algorithm']).hexdigest()
            if digest != asset['digest']:
                raise RuntimeError(f"Downloaded tool hash differs: {asset['id']}")
            temporary.replace(archive)
        with archive.open('rb') as source:
            digest = hashlib.file_digest(source, asset['algorithm']).hexdigest()
        if digest != asset['digest']:
            raise RuntimeError(f"Cached tool hash differs: {archive}")
        destination = TOOLS / asset['destination']
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                name = entry.filename.replace('\\', '/')
                prefix = asset.get('stripPrefix', '')
                if prefix and not name.startswith(prefix):
                    continue
                name = name[len(prefix):]
                if not name or entry.is_dir():
                    continue
                target = (destination / name).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise RuntimeError(f'Archive member escaped tools: {name}')
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
    if missing():
        raise RuntimeError(f'Missing prepared tools: {missing()}')
    return TOOLS


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check existing tools without downloading')
    args = parser.parse_args()
    if args.check:
        if missing():
            parser.exit(1, 'Tools absent; run tools/native-build/prepare.py to prepare them.\n')
        print(TOOLS)
    else:
        print(prepare())
