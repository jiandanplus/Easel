"""Build the separate management site and freeze its public asset inventory."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_receipt(frontend):
    frontend = Path(frontend).resolve()
    dist = frontend / "dist"
    if not (dist / "index.html").is_file():
        raise ValueError("console_build_missing")
    sources = {}
    for name in ("package.json", "package-lock.json", "index.html", "vite.config.ts", "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json"):
        path = frontend / name
        if path.is_file():
            if path.is_symlink():
                raise ValueError("console_source_symlink")
            sources[name] = sha(path)
    for path in (frontend / "src").rglob("*"):
        if path.is_symlink():
            raise ValueError("console_source_symlink")
        if path.is_file() and path.suffix in {".ts", ".tsx", ".css", ".svg"}:
            sources[path.relative_to(frontend).as_posix()] = sha(path)
    files = {}
    for path in dist.rglob("*"):
        if path.is_symlink():
            raise ValueError("console_output_symlink")
        if not path.is_file() or path.name == "console-assets.json":
            continue
        relative = path.relative_to(dist).as_posix()
        if relative != "index.html" and not (relative.startswith("assets/") and path.suffix in {".js", ".css", ".svg", ".png", ".webp", ".woff2"}):
            raise ValueError("unexpected_console_output")
        files[relative] = sha(path)
    result = {"schema_version": 1, "source": dict(sorted(sources.items())), "files": dict(sorted(files.items())), "credentials_bundled": False, "desktop_assets_included": False}
    (dist / "console-assets.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-existing", action="store_true")
    args = parser.parse_args()
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    if not args.verify_existing:
        subprocess.run(["npm", "run", "build"], cwd=frontend, check=True)
    receipt = write_receipt(frontend)
    print(json.dumps({"success": True, "source_files": len(receipt["source"]), "output_files": len(receipt["files"]), "receipt_sha256": sha(frontend / "dist/console-assets.json")}))


if __name__ == "__main__":
    main()
