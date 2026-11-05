"""Bounded fully decoded images in an owner-only, inode-pinned blob store."""
from ..media_maintenance.runtime import mutation,physical_guard,current_permit,GuardedDatabase,physical_call,operation,physical_directory_guard
from contextlib import contextmanager
import asyncio
import hashlib
import io
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
import warnings

from PIL import Image, UnidentifiedImageError

from ..errors import PlatformError

MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def inspect_image(raw, *, max_bytes=20 * 1024 * 1024, max_pixels=16 * 1024 * 1024, mime=None):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= max_bytes:
        raise PlatformError(422, "invalid_media_artifact")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                fmt = image.format
                width, height = image.size
                if (fmt not in MIMES or width <= 0 or height <= 0 or width * height > max_pixels
                        or width > 16384 or height > 16384 or getattr(image, "n_frames", 1) != 1
                        or mime not in (None, MIMES.get(fmt))):
                    raise ValueError
                image.verify()
            # verify() does not decode pixel data. Reopen and force the actual
            # decoder to reject truncation, invalid scan data and CRC failures.
            with Image.open(io.BytesIO(raw)) as decoded:
                decoded.load()
                if decoded.size != (width, height) or decoded.format != fmt:
                    raise ValueError
        return {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "mime": MIMES[fmt],
                "properties": {"width": width, "height": height, "format": fmt}}
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise PlatformError(422, "invalid_media_artifact") from None


async def _reap_decoder(process):
    if process.returncode is None:
        try: process.kill()
        except ProcessLookupError: pass
    async def drain():
        if process.stdin is not None: process.stdin.close()
        if process.stdout is not None:
            while await process.stdout.read(4096): pass
        await process.wait()
    cleanup = asyncio.create_task(drain())
    cancelled = False
    while not cleanup.done():
        try: await asyncio.shield(cleanup)
        except asyncio.CancelledError: cancelled = True
    cleanup.result()
    if cancelled: raise asyncio.CancelledError


class ArtifactStore:
    def __init__(self, root, *, max_bytes=20 * 1024 * 1024, max_pixels=16 * 1024 * 1024):
        self.root = Path(root)
        self.max_bytes, self.max_pixels = max_bytes, max_pixels

    @physical_guard
    def check_capacity(self, required=0):
        if type(required) is not int or not 0 <= required <= 128 * 1024 * 1024:
            raise PlatformError(503, "media_storage_unavailable")
        with self._directory(create=True):
            if shutil.disk_usage(self.root).free < 128 * 1024 * 1024 + required:
                raise PlatformError(503, "media_storage_capacity")

    @physical_directory_guard
    @contextmanager
    def _directory(self, create=False):
        if create:
            from ..media_maintenance.runtime import require_physical
            require_physical()._permit._control._source.verify_path(self.root)
        fd = None
        try:
            if not self.root.is_absolute() or self.root.absolute() != self.root.resolve():
                raise ValueError
            if create:
                self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(fd)
            from ..media_maintenance.runtime import optional_permit
            active=optional_permit()
            if active is not None:active._control._source.verify_fd(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError
            yield fd
        except (OSError, ValueError):
            raise PlatformError(503, "media_storage_unavailable") from None
        finally:
            if fd is not None: os.close(fd)

    @physical_guard
    def put_image(self, raw, *, mime=None, max_bytes=None, fence=None):
        info = inspect_image(raw, max_bytes=min(max_bytes or self.max_bytes, self.max_bytes),
                             max_pixels=self.max_pixels, mime=mime)
        from ..media_maintenance.permits import WriteFence
        from ..media_maintenance.contracts import require
        require(type(fence) is WriteFence)
        return self._put_verified(raw,info,storage_key=fence.storage_key,fence=fence)

    @physical_guard
    async def put_image_async(self, raw, *, mime=None, max_bytes=None, before_write=None):
        """Decode in a credential-free bounded child, reaped before returning."""
        maximum = min(max_bytes or self.max_bytes, self.max_bytes)
        if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
            raise PlatformError(422, "invalid_media_artifact")
        code = (
            "import resource,sys,json; "
            "resource.setrlimit(resource.RLIMIT_CPU,(8,8)); "
            "resource.setrlimit(resource.RLIMIT_AS,(536870912,536870912)) if sys.platform=='linux' else None; "
            "from platform_service.media.artifacts import inspect_image; "
            "raw=sys.stdin.buffer.read(int(sys.argv[1])+1); "
            "print(json.dumps(inspect_image(raw,max_bytes=int(sys.argv[1]),max_pixels=int(sys.argv[2]),mime=json.loads(sys.argv[3]))))"
        )
        import json
        process = None
        try:
            pending = asyncio.create_task(asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", code, str(maximum), str(self.max_pixels), json.dumps(mime),
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONNOUSERSITE": "1"},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL))
            try:
                process = await asyncio.shield(pending)
            except asyncio.CancelledError:
                # The OS child can exist before create_subprocess_exec returns
                # its handle. Keep the caller's actual physical ref until that
                # same child is known and reaped, including repeated cancels.
                while not pending.done():
                    try: await asyncio.shield(pending)
                    except asyncio.CancelledError: pass
                try: process = pending.result()
                except (OSError, asyncio.CancelledError): pass
                raise
            output, _ = await asyncio.wait_for(process.communicate(raw), 10)
            if process.returncode != 0 or len(output) > 4096: raise ValueError
            info = json.loads(output)
            if info.get("sha256") != hashlib.sha256(raw).hexdigest() or info.get("size") != len(raw): raise ValueError
            key = secrets.token_hex(32)
            if before_write is not None: await before_write({"storage_key": key, **info})
            from ..media_maintenance.runtime import authorize_write,current_permit
            fence=await authorize_write(current_permit(),{'storage_key':key,**info})
            return self._put_verified(raw, info, storage_key=key,fence=fence)
        except (OSError, ValueError, TimeoutError):
            raise PlatformError(422, "invalid_media_artifact") from None
        finally:
            if process is not None:
                await _reap_decoder(process)

    @physical_guard
    def _put_verified(self, raw, info, *, storage_key=None, fence=None):
        from ..media_maintenance.permits import WriteFence
        from ..media_maintenance.contracts import require
        require(type(fence) is WriteFence and type(raw) is bytes)
        key=storage_key or fence.storage_key;fence.verify({'storage_key':key,**info})
        require(len(raw)==info['size'] and hashlib.sha256(raw).hexdigest()==info['sha256'])
        self.check_capacity(len(raw))
        temporary = key + ".part"
        with self._directory(create=True) as directory:
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                os.link(temporary, key, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                os.fsync(directory)
            except OSError:
                raise PlatformError(503, "media_storage_unavailable") from None
            finally:
                try: os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError: pass
        return {"storage_key": key, **info}

    @physical_guard
    def discard(self, metadata, *, fence=None):
        from ..media_maintenance.permits import WriteFence
        from ..media_maintenance.contracts import require
        require(type(fence) is WriteFence);fence.verify(metadata,action='discard')
        # Only for a fresh blob whose metadata registration was affirmatively
        # rejected. A database/commit uncertainty never calls this cleanup.
        self.read(metadata)
        with self._directory() as directory:
            os.unlink(metadata["storage_key"], dir_fd=directory)
            os.fsync(directory)

    @physical_guard
    def delete_exact(self, metadata, *, fence=None):
        from ..media_maintenance.permits import GcFence
        from ..media_maintenance.contracts import require
        require(type(fence) is GcFence);fence.verify(metadata)
        key,expected,size=metadata.get("storage_key"),metadata.get("sha256"),metadata.get("size")
        if (not isinstance(key,str) or not re.fullmatch(r"[0-9a-f]{32}|[0-9a-f]{64}",key)
                or not isinstance(expected,str) or not re.fullmatch(r"[0-9a-f]{64}",expected)
                or type(size) is not int or not 0<size<=self.max_bytes):
            raise PlatformError(404,"media_artifact_unavailable")
        # A crash may leave the exclusive .part before link, or both hardlinks
        # before temporary-name cleanup. Validate every present name before any
        # unlink. An incomplete/unverifiable partial stays pending and charged.
        with self._directory() as directory:
            verified=[]
            try:
                for name in (key,key+".part"):
                    try:descriptor=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
                    except FileNotFoundError:continue
                    with os.fdopen(descriptor,"rb") as stream:
                        info=os.fstat(stream.fileno())
                        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
                                or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size!=size):raise ValueError
                        raw=stream.read(size+1)
                    if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=expected:raise ValueError
                    verified.append((name,(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)))
                for name,signature in verified:
                    current=os.stat(name,dir_fd=directory,follow_symlinks=False)
                    if (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)!=signature:raise ValueError
                for name,_ in verified:
                    fence.verify(metadata);os.unlink(name,dir_fd=directory)
                if verified:os.fsync(directory)
                return bool(verified)
            except (OSError,ValueError):
                raise PlatformError(503,"media_gc_blob_unverifiable") from None

    def read(self, metadata):
        key, expected, size = metadata.get("storage_key"), metadata.get("sha256"), metadata.get("size")
        if (not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}|[0-9a-f]{64}", key)
                or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)
                or type(size) is not int or not 0 < size <= self.max_bytes):
            raise PlatformError(404, "media_artifact_unavailable")
        with self._directory() as directory:
            try:
                fd = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != size):
                        raise ValueError
                    raw = stream.read(size + 1)
                if len(raw) != size or hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError
                return raw
            except (OSError, ValueError):
                raise PlatformError(404, "media_artifact_unavailable") from None


def public_artifact(metadata):
    return {key: (value.isoformat() if hasattr(value, "isoformat") else value)
            for key, value in metadata.items()
            if key in {"id", "artifact_id", "asset_id", "sha256", "size", "mime", "properties", "expires_at", "job_id"}}
