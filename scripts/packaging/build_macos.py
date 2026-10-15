"""Build one local unsigned core trial from explicitly prepared, pinned inputs."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from easel.portable import digest, bounded


def selected(relative):
    path = Path(relative)
    if relative.startswith(('scripts/validation/', 'scripts/packaging/')): return False
    excluded = {'.git','node_modules','__pycache__','.venv','profiles','outputs','.cache','models','cookies'}
    if any(part in excluded for part in path.parts): return False
    name = path.name.lower()
    if name == '.env' or name.startswith('.env.') or any(x in name for x in ('token_cache','cookies','auth.json','wechat-publisher.yaml')): return False
    return path.parts[0] in {'easel','web','skills','agents','scripts'} and not (relative.startswith('web/frontend/') and not relative.startswith('web/frontend/dist/'))


def verify_metadata(prepared, authoritative, names):
    for name in names:
        if (prepared/name).is_symlink() or (prepared/name).read_bytes() != (authoritative/name).read_bytes():
            raise ValueError('Prepared metadata differs from tracked authority: '+name)


def verify_payload(root, records, *, bytecode=False):
    def excluded(path):
        return bytecode and ('__pycache__' in path.parts or path.suffix == '.pyc')
    records = {relative: expected for relative, expected in records.items() if not excluded(Path(relative))}
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*')
              if (p.is_file() or p.is_symlink()) and not excluded(p.relative_to(root))}
    if actual != set(records):
        raise ValueError('Unpinned or missing payload files: '+str(sorted(actual ^ set(records))))
    for relative, expected in records.items():
        source = bounded(root, relative)
        if isinstance(expected, dict) and 'symlink' in expected:
            if not source.is_symlink() or str(source.readlink()) != expected['symlink']:
                raise ValueError('Payload link mismatch: '+relative)
        elif source.is_symlink() or digest(source) != (expected['sha256'] if isinstance(expected, dict) else expected):
            raise ValueError('Payload digest mismatch: '+relative)


def copy_tracked_licenses(destination, tracked):
    """Copy only authoritative tracked notices, rejecting links at every level."""
    prefix = 'packaging/macos/licenses/'
    sources = []
    for relative in tracked:
        if not relative.startswith(prefix):
            continue
        path = Path(relative)
        if '..' in path.parts:
            raise ValueError('Invalid tracked license path: '+relative)
        source = ROOT/path
        for parent in [source, *source.parents]:
            if parent == ROOT:
                break
            mode = parent.lstat().st_mode
            if stat.S_ISLNK(mode) or (parent == source and not stat.S_ISREG(mode)):
                raise ValueError('Nonregular tracked license input: '+relative)
        sources.append((source, destination/path.relative_to('packaging/macos/licenses')))
    for source, target in sources:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', type=Path, default=ROOT/'outputs/_run/portable-build')
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/_run/portable-delivery')
    parser.add_argument('--codex-root', type=Path, required=True, help='Pinned native aarch64-apple-darwin vendor directory')
    args = parser.parse_args()
    inputs, output = args.inputs.resolve(), args.output.resolve()
    app = output/'Easel Core Trial.app'
    if app.exists(): raise SystemExit('Refusing to overwrite existing stage; choose fresh output')
    container = output/'Easel Core Trial'
    app = container/'Easel Core Trial.app'
    if app.exists(): raise SystemExit('Refusing to overwrite existing stage')
    resource = app/'Contents/Resources'
    metadata = ['core-requirements.txt','wheels-provenance.json','python-assets.json','python-runtime-files.json','codex-vendor-provenance.json','build-preparation-notes.json']
    verify_metadata(inputs, ROOT/'packaging/macos', metadata)
    python = inputs/'python-download/cpython-3.12.13-macos-aarch64-none'
    verify_payload(python, json.loads((ROOT/'packaging/macos/python-runtime-files.json').read_text())['files'], bytecode=True)
    verify_payload(args.codex_root.resolve(), json.loads((ROOT/'packaging/macos/codex-vendor-provenance.json').read_text())['files'])
    resource.mkdir(parents=True)
    tracked = subprocess.check_output(['git','ls-files','-z'], cwd=ROOT).decode().split('\0')
    copied = []
    for relative in tracked:
        if not relative or not selected(relative): continue
        source = ROOT/relative
        if not source.is_file() or source.is_symlink(): continue
        target = resource/('seed/'+relative if relative.split('/')[0] in {'skills','agents','scripts'} else relative)
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source,target); copied.append(relative)
    # model_registry remains importable before writable seeds have been created.
    registry = resource/'skills/shared/scripts/model_registry.py'; registry.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/'skills/shared/scripts/model_registry.py',registry)
    dist = ROOT/'web/frontend/dist'
    if not (dist/'index.html').is_file(): raise SystemExit('Prebuilt frontend missing')
    shutil.copytree(dist,resource/'web/frontend/dist',dirs_exist_ok=True)
    python = inputs/'python-download/cpython-3.12.13-macos-aarch64-none'
    shutil.copytree(python,resource/'python',symlinks=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    site = resource/'python/lib/python3.12/site-packages'
    subprocess.run(['uv','pip','install','--offline','--no-index','--python',str(resource/'python/bin/python3'),'--require-hashes','--no-deps','--no-compile-bytecode','--target',str(site),'--find-links',str(inputs/'wheels'),'-r',str(ROOT/'packaging/macos/core-requirements.txt')],check=True, env={**os.environ,'UV_NO_CONFIG':'1'})
    shutil.rmtree(site/'bin',ignore_errors=True)
    vendor = args.codex_root.resolve()
    shutil.copytree(vendor,resource/'native',symlinks=True)
    copy_tracked_licenses(resource/'licenses', tracked)
    shutil.copy2(ROOT/'LICENSE',resource/'licenses/Easel-LICENSE')
    for name in ['core-requirements.txt','wheels-provenance.json','python-assets.json','python-runtime-files.json','codex-vendor-provenance.json','build-preparation-notes.json']:
        shutil.copy2(ROOT/'packaging/macos'/name,resource/name)
    native = {p.relative_to(resource).as_posix():digest(p) for p in (resource/'native').rglob('*') if p.is_file()}
    manifest = {'schema_version':1,'platform':'darwin','architecture':'arm64','codex':{'version':'0.146.0','executable':'native/bin/codex','sha256':native['native/bin/codex']},'files':native}
    (resource/'runtime-manifest.json').write_text(json.dumps(manifest,indent=2))
    dirty_path = ROOT/'.git/sdd/preexisting-ui.json'
    dirty = json.loads(dirty_path.read_text()) if dirty_path.exists() else None
    # Record exactly the tracked/untracked appearance inputs without staging them.
    (resource/'build-provenance.json').write_text(json.dumps({'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'source_allowlist':copied,'appearance_baseline':dirty,'dirty_source':subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True),'appearance_hashes':{str(p.relative_to(ROOT)):digest(p) for p in [ROOT/x for x in subprocess.check_output(['git','ls-files','-m'],cwd=ROOT,text=True).splitlines()] if p.is_file()},'python_archive_verified':False,'minimum_macos':'15.0','unsigned':True},ensure_ascii=False,indent=2))
    macos = app/'Contents/MacOS'; macos.mkdir()
    launcher = macos/'Easel'
    launcher.write_text('#!/bin/sh\nHERE="$(CDPATH= cd -- "$(dirname -- "$0")/../Resources" && pwd)"\nLOG="$HOME/Library/Logs/Easel"\nmkdir -p "$LOG"\nexport PYTHONDONTWRITEBYTECODE=1\nexport PYTHONPATH="$HERE"\ncd "$HERE" || exit 1\nexec "$HERE/python/bin/python3" -B -m easel.portable "$@" >>"$LOG/launcher.log" 2>&1\n')
    launcher.chmod(0o755)
    (container/'Stop Easel.command').write_text('#!/bin/sh\nHERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\nexec "$HERE/Easel Core Trial.app/Contents/MacOS/Easel" --stop "$@"\n')
    (container/'Stop Easel.command').chmod(0o755)
    with (app/'Contents/Info.plist').open('wb') as stream:
        plistlib.dump({'CFBundleExecutable':'Easel','CFBundleIdentifier':'local.easel.core-trial','CFBundleName':'Easel Core Trial','CFBundleVersion':'0.1.0','CFBundlePackageType':'APPL','LSMinimumSystemVersion':'15.0','LSArchitecturePriority':['arm64']},stream)
    shutil.copy2(ROOT/'packaging/macos/README.md', container/'README.md')
    inventory = {}
    for path in resource.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(resource): raise ValueError('External symlink: '+str(path))
        if path.is_file(): inventory[path.relative_to(resource).as_posix()] = digest(path)
    (resource/'build-inventory.json').write_text(json.dumps(inventory,sort_keys=True))
    archive = output/'Easel-Core-Trial-macOS15-arm64.zip'
    subprocess.run(['/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',str(container),str(archive)],check=True)
    report = {'app':str(app),'archive':str(archive),'sha256':digest(archive),'archive_bytes':archive.stat().st_size,'inventory_files':len(inventory)}
    (output/'artifact.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2))

if __name__ == '__main__': main()
