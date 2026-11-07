"""Host/boot/process identities collected from the current OS, never HTTP input."""
import ctypes
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4
from .contracts import MaintenanceError,WorkerIdentity,require


def _hash(label,value):
    require(type(value) is bytes and 0<len(value)<=4096)
    return label+"-"+hashlib.sha256(value).hexdigest()


def _darwin(command):
    result=subprocess.run(command,env={"PATH":"/usr/bin:/bin","LC_ALL":"C"},stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=3,check=True)
    require(0<len(result.stdout)<=4096);return result.stdout.strip()


def host_identity():
    """Return hashed host/boot IDs and this OS process's actual start identity."""
    try:
        pid=os.getpid()
        if sys.platform=="linux":
            machine=Path("/etc/machine-id").read_bytes().strip()
            boot=Path("/proc/sys/kernel/random/boot_id").read_bytes().strip()
            require(re.fullmatch(rb"[0-9a-f]{32}",machine) is not None)
            require(re.fullmatch(rb"[0-9a-f-]{36}",boot) is not None)
            raw=Path("/proc/"+str(pid)+"/stat").read_text();prefix,fields=raw.rsplit(") ",1)
            require(prefix.startswith(str(pid)+" ("));start=fields.split()[19]
            require(start.isdecimal())
            return _hash("Linux",machine),_hash("Linux",boot),"Linux-"+str(pid)+"-"+start
        require(sys.platform=="darwin")
        class Timespec(ctypes.Structure):
            _fields_=[('seconds',ctypes.c_long),('nanoseconds',ctypes.c_long)]
        machine_buffer=ctypes.create_string_buffer(16);timeout=Timespec(1,0)
        libc=ctypes.CDLL(None,use_errno=True)
        require(libc.gethostuuid(machine_buffer,ctypes.byref(timeout))==0)
        machine=machine_buffer.raw
        boot=_darwin(["/usr/sbin/sysctl","-n","kern.boottime"])
        start=_darwin(["/bin/ps","-p",str(pid),"-o","lstart="])
        return _hash("Darwin",machine),_hash("Darwin",boot),"Darwin-"+str(pid)+"-"+hashlib.sha256(start).hexdigest()
    except (OSError,ValueError,IndexError,AttributeError,subprocess.SubprocessError):raise MaintenanceError() from None


def worker_identity():
    return WorkerIdentity(str(uuid4()),str(uuid4()),*host_identity())
