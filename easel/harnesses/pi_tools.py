"""Fixed host-side tools; no native Pi tools or inherited credentials."""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import stat

from .base import HarnessError

MAX_FILE = 256 * 1024
MAX_OUTPUT = 128 * 1024
PRIVATE_PARTS = {".git", ".env", ".aws", ".ssh", ".gnupg", ".codex", ".pi", ".agents", ".easel.lock", ".easel-portable",
                 "_harness", "_platform", "_central", ".platform", ".venv", "node_modules",
                 ".openclaw", ".easel", ".easel-platform", ".easel-browser-profiles", "cookies",
                 "cookies.json", "auth.json", "credentials.json", "storage_state.json",
                 "_sessions", "_login", "_publish", "_debug", "_profile_build"}
PRIVATE_PREFIXES = (".env", ".token_cache", "wechat-publisher.yaml")


def _trusted_resources():
    value = os.environ.get("EASEL_RESOURCE_ROOT")
    if not value:
        return None
    path = Path(value)
    if (not path.is_absolute() or path.is_symlink() or not path.is_dir()
            or path.resolve(strict=True) != path):
        raise ValueError
    # Launcher-owned absolute root. Reject linked descendants rather than
    # letting a bundled Python PATH resolve to external user software.
    path = path.resolve(strict=True)
    candidate = path / "python/bin"
    for child in (path / "python", candidate):
        if child.is_symlink(): raise ValueError
    if (not candidate.is_dir() or not (candidate / "python3").is_file()
            or not (candidate / "python3").resolve(strict=True).is_relative_to(path / "python")):
        raise ValueError
    if not os.access(candidate / "python3", os.X_OK):
        raise ValueError
    return path


def clean_environment(resources=None):
    # No PATH inheritance, loaders, proxy variables, Python imports or auth.
    return {"PATH": (str(resources / "python/bin") + ":" if resources is not None else "") + "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "PYTHONNOUSERSITE": "1"}


def protected(parts):
    return any(p.lower() in PRIVATE_PARTS or p.lower().startswith(PRIVATE_PREFIXES) for p in parts)


def _parts(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or "\0" in value or "\\" in value:
        raise ValueError
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or protected(path.parts):
        raise ValueError
    return path.parts


def validate_artifact_descriptor(value):
    """Validate host reference shape; this alone does not verify file bytes."""
    if (type(value) is not dict or set(value) != {'version', 'path', 'name', 'size', 'sha256'}
            or type(value.get('version')) is not int or value['version'] != 1
            or type(value.get('size')) is not int or not 0 <= value['size'] <= MAX_FILE
            or type(value.get('sha256')) is not str or not re.fullmatch(r'[0-9a-f]{64}', value['sha256'])):
        return None
    path = value.get('path')
    if (type(path) is not str or not path.startswith('outputs/') or len(path) > 2048
            or any(ord(c) < 32 or ord(c) == 127 or c == '\\' for c in path)
            or '[已隐藏]' in path or '[redacted]' in path.lower()):
        return None
    parts = path.split('/')
    if (any(not part or part.startswith(('.', '_')) for part in parts)
            or protected(parts) or len(parts) < 2 or type(value.get('name')) is not str
            or value['name'] != parts[-1]):
        return None
    return dict(value)


def _written_artifact(request, parts, data, stream):
    """Publish only the exact successful host write's scanned immutable bytes."""
    from easel.platform.privacy import safe_snapshot, sensitive_values
    from fastapi import HTTPException
    candidate = {'version': 1, 'path': Path(*parts).as_posix(), 'name': parts[-1],
                 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    if validate_artifact_descriptor(candidate) is None:
        return None
    try:
        secrets = [*sensitive_values(request.cwd), *(v for k, v in request.env.items()
                   if any(marker in k.upper() for marker in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')))]
        if any(secret and (secret in candidate['path'] or secret.encode() in data) for secret in secrets):
            return None
        root = request.cwd.resolve(); path = root.joinpath(*parts)
        written = os.fstat(stream.fileno())
        before = path.stat(follow_symlinks=False)
        if (not os.path.samestat(written, before) or written.st_nlink != 1
                or not stat.S_ISREG(written.st_mode) or written.st_size != len(data)):
            return None
        with safe_snapshot(path, root / 'outputs') as snapshot:
            if snapshot.read(MAX_FILE + 1) != data:
                return None
        after = path.stat(follow_symlinks=False); current = os.fstat(stream.fileno())
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                                 info.st_ctime_ns, info.st_nlink)
        if identity(written) != identity(after) or identity(written) != identity(current):
            return None
        return candidate
    except (HTTPException, HarnessError, OSError, ValueError, UnicodeError):
        # The write already succeeded. A publication check cannot undo it or
        # turn it into a failed tool result that might encourage another write.
        return None


@contextmanager
def _directory(root, parts, create=False):
    descriptor = os.open(root.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            if create:
                try: os.mkdir(part, 0o755, dir_fd=descriptor)
                except FileExistsError: pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _files(request, name, arguments):
    parts = _parts(arguments.get("path", "." if name == "list_files" else None))
    if name == "list_files":
        with _directory(request.cwd, parts) as directory:
            values = []
            for entry in sorted(os.listdir(directory)):
                if protected((*parts, entry)): continue
                info = os.stat(entry, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)): continue
                values.append(entry + ("/" if stat.S_ISDIR(info.st_mode) else ""))
                if len(values) > 2000: raise ValueError
            result = "\n".join(values)
            if len(result.encode()) > MAX_OUTPUT: raise ValueError
            return result
    if not parts:
        raise ValueError
    if name == "write_file":
        content = arguments.get("content")
        if not isinstance(content, str) or len(content.encode()) > MAX_FILE: raise ValueError
    with _directory(request.cwd, parts[:-1], create=name == "write_file") as directory:
        # Open first, verify pinned inode, then mutate. Never truncate a hardlink.
        flags = os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_WRONLY | os.O_CREAT if name == "write_file" else os.O_RDONLY)
        descriptor = os.open(parts[-1], flags, 0o644, dir_fd=directory)
        with os.fdopen(descriptor, "wb" if name == "write_file" else "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise ValueError
            if name == "write_file":
                stream.truncate(0)
                data = content.encode()
                stream.write(data)
                stream.flush()
                artifact = _written_artifact(request, parts, data, stream)
                return {"text": "文件已保存", **({"artifact": artifact} if artifact else {})}
            if info.st_size > MAX_FILE: raise ValueError
            content = stream.read(MAX_FILE + 1)
            if len(content) > MAX_FILE or b"\0" in content: raise ValueError
            from easel.platform.privacy import sensitive_values
            secrets = [*sensitive_values(request.cwd), *(v for k, v in request.env.items()
                       if any(m in k.upper() for m in ("KEY", "TOKEN", "SECRET", "PASSWORD")))]
            if any(value and value.encode() in content for value in secrets): raise ValueError
            return content.decode("utf-8")


def shell_supported():
    return platform.system() == "Darwin" and Path("/usr/bin/sandbox-exec").is_file()


def _sandbox_profile(root, resources=None):
    root = str(root.resolve())
    # Seatbelt reads Scheme strings, not JSON: JSON \u escapes do not name
    # Unicode filesystem paths. Keep native UTF-8 while escaping quotes/slashes.
    quoted = json.dumps(root, ensure_ascii=False)
    # System software may be read, workspace writes only. Network and all other
    # files/process introspection remain denied by default. Deny private paths
    # after broad workspace rules, including nested environment files.
    rules = ["(version 1)", "(deny default)", "(allow process-exec)", "(allow process-fork)",
             "(allow signal (target self))", "(allow sysctl-read)",
             '(allow file-read* (subpath "/System") (subpath "/usr") (subpath "/bin") (subpath "/sbin") (subpath "/Library/Apple") (literal "/dev/null") (literal "/dev/urandom") (literal "/private/etc/localtime") (literal "/private/var/select/sh") (literal "/"))',
             f"(allow file-read* (subpath {quoted}))", f"(allow file-write* (subpath {quoted}))"]
    # Regex path matching applies under every directory, not just root/.env.
    def insensitive(text):
        return "".join("[" + c.lower() + c.upper() + "]" if c.isalpha() else "\\." if c == "." else c for c in text)
    patterns = [*(insensitive(prefix) + "[^/]*" for prefix in PRIVATE_PREFIXES),
                *(insensitive(name) for name in sorted(PRIVATE_PARTS))]
    # Seatbelt's individual regex string size is limited. Separate short rules
    # preserve the shared component policy without truncating a large alternation.
    for pattern in patterns:
        rules.append('(deny file-read* file-write* (regex #"/' + pattern + '(/|$)"))')
    for part in PRIVATE_PARTS:
        rules.append(f"(deny file-read* file-write* (subpath {json.dumps(root + '/' + part, ensure_ascii=False)}))")
    if resources is not None:
        rules.append(f"(allow file-read* (subpath {json.dumps(str(resources), ensure_ascii=False)}))")
        rules.append(f"(deny file-write* (subpath {json.dumps(str(resources), ensure_ascii=False)}))")
    return "\n".join(rules)


async def stop_process(process):
    if process.returncode is None:
        if process.stdin:
            try: process.stdin.close()
            except (OSError, RuntimeError): pass
        try: await asyncio.wait_for(process.wait(), .5)
        except TimeoutError:
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: await asyncio.wait_for(process.wait(), 1)
            except TimeoutError:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                await process.wait()
    # A shell may have exited while background children remain in its group.
    try: os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError: pass


async def _shell(request, arguments, ask):
    if not shell_supported(): raise ValueError
    command = arguments.get("command")
    seconds = arguments.get("timeout_seconds", 30)
    if (not isinstance(command, str) or not command or len(command) > 8192 or "\0" in command
            or type(seconds) is not int or not 1 <= seconds <= 120): raise ValueError
    answers = await ask([{"questionId": "permission", "header": "执行授权", "question": "Pi 请求在工作区执行（仅本次）：\n" + command,
                          "options": [{"label": "允许本次"}, {"label": "拒绝"}]}])
    if answers.get("permission") != ["允许本次"]: raise ValueError
    resources = _trusted_resources()
    process = await asyncio.create_subprocess_exec("/usr/bin/sandbox-exec", "-p", _sandbox_profile(request.cwd, resources),
                "/bin/sh", "-c", command, cwd=str(request.cwd.resolve()), env=clean_environment(resources),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    async def output():
        chunks, size = [], 0
        while block := await process.stdout.read(8192):
            size += len(block)
            if size > MAX_OUTPUT: raise ValueError
            chunks.append(block)
        await process.wait()
        return {"ok": process.returncode == 0, "text": b"".join(chunks).decode("utf-8", errors="replace"), "exit_code": process.returncode}
    try: return await asyncio.wait_for(output(), seconds)
    finally: await stop_process(process)


async def execute_tool(request, name, arguments, ask):
    try:
        if request.sandbox not in ("read-only", "workspace-write") or not isinstance(arguments, dict): raise ValueError
        if name not in ("read_file", "list_files", "write_file", "run_command"): raise ValueError
        if request.sandbox == "read-only" and name in ("write_file", "run_command"): raise ValueError
        if name == "run_command": return await _shell(request, arguments, ask)
        # Files <=256 KiB and 2000 immediate entries keep these synchronous
        # inode-pinned operations bounded; cancellation cannot leave a thread
        # writing after a user turn has already been cancelled.
        result = _files(request, name, arguments)
        return {"ok": True, **result} if isinstance(result, dict) else {"ok": True, "text": result}
    except (OSError, ValueError, UnicodeError, TimeoutError):
        return {"ok": False, "text": "工具操作被拒绝或失败：请检查工作区路径、权限、文件大小或执行沙箱"}
