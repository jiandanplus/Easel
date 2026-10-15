"""Build one local unsigned core trial from explicitly prepared, pinned inputs."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', type=Path, default=ROOT/'outputs/_run/portable-build')
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/_run/portable-delivery')
    args = parser.parse_args()
    inputs, output = args.inputs.resolve(), args.output.resolve()
    app = output/'Easel Core Trial.app'
    if app.exists(): raise SystemExit('Refusing to overwrite existing stage; choose fresh output')
    resource = app/'Contents/Resources'
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
    subprocess.run(['uv','pip','install','--offline','--require-hashes','--no-deps','--no-compile-bytecode','--target',str(site),'--find-links',str(inputs/'wheels'),'-r',str(inputs/'core-requirements.txt')],check=True, env={**os.environ,'UV_NO_CONFIG':'1'})
    shutil.rmtree(site/'bin',ignore_errors=True)
    vendor = Path('/Users/jiandan/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin')
    shutil.copytree(vendor,resource/'native',symlinks=True)
    shutil.copytree(inputs/'licenses',resource/'licenses')
    shutil.copy2(ROOT/'LICENSE',resource/'licenses/Easel-LICENSE')
    for name in ['core-requirements.txt','wheels-provenance.json','python-provenance.json','python-assets.json','python-runtime-files.json','codex-vendor-provenance.json']:
        shutil.copy2(inputs/name,resource/name)
    native = {p.relative_to(resource).as_posix():digest(p) for p in (resource/'native').rglob('*') if p.is_file()}
    manifest = {'schema_version':1,'platform':'darwin','architecture':'arm64','codex':{'version':'0.146.0','executable':'native/bin/codex','sha256':native['native/bin/codex']},'files':native}
    (resource/'runtime-manifest.json').write_text(json.dumps(manifest,indent=2))
    dirty = json.loads((ROOT/'.git/sdd/preexisting-ui.json').read_text())
    # Record exactly the tracked/untracked appearance inputs without staging them.
    (resource/'build-provenance.json').write_text(json.dumps({'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'source_allowlist':copied,'appearance_baseline':dirty,'dirty_source':subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True),'appearance_hashes':{str(p.relative_to(ROOT)):digest(p) for p in [ROOT/x for x in subprocess.check_output(['git','ls-files','-m'],cwd=ROOT,text=True).splitlines()] if p.is_file()},'python_archive_verified':False,'minimum_macos':'15.0','unsigned':True},ensure_ascii=False,indent=2))
    macos = app/'Contents/MacOS'; macos.mkdir()
    launcher = macos/'Easel'
    launcher.write_text('#!/bin/sh\nHERE="$(CDPATH= cd -- "$(dirname -- "$0")/../Resources" && pwd)"\nLOG="$HOME/Library/Logs/Easel"\nmkdir -p "$LOG"\nexport PYTHONDONTWRITEBYTECODE=1\nexport PYTHONPATH="$HERE"\nexec "$HERE/python/bin/python3" -B -m easel.portable "$@" >>"$LOG/launcher.log" 2>&1\n')
    launcher.chmod(0o755)
    (output/'Stop Easel.command').write_text('#!/bin/sh\nHERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\nexec "$HERE/Easel Core Trial.app/Contents/MacOS/Easel" --stop "$@"\n')
    (output/'Stop Easel.command').chmod(0o755)
    with (app/'Contents/Info.plist').open('wb') as stream:
        plistlib.dump({'CFBundleExecutable':'Easel','CFBundleIdentifier':'local.easel.core-trial','CFBundleName':'Easel Core Trial','CFBundleVersion':'0.1.0','CFBundlePackageType':'APPL','LSMinimumSystemVersion':'15.0','LSArchitecturePriority':['arm64']},stream)
    inventory = {}
    for path in resource.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(resource): raise ValueError('External symlink: '+str(path))
        if path.is_file(): inventory[path.relative_to(resource).as_posix()] = digest(path)
    (resource/'build-inventory.json').write_text(json.dumps(inventory,sort_keys=True))
    archive = output/'Easel-Core-Trial-macOS15-arm64.zip'
    subprocess.run(['/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',str(app),str(archive)],check=True)
    report = {'app':str(app),'archive':str(archive),'sha256':digest(archive),'archive_bytes':archive.stat().st_size,'inventory_files':len(inventory)}
    (output/'artifact.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2))

if __name__ == '__main__': main()
