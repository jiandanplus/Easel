#!/usr/bin/env python3
"""Root parked exec gate. Stdlib only. No source authority/product/resource IO.

Root must write launch.once before spawning this process, independently observe
its PID/PPID/interpreter and actual tool session, then write caller.once durably.
This child reports its PID only as a rendezvous hint, never independent proof.
"""
import argparse
import os
from pathlib import Path
import sys
import time
from b3_target_once import (closed_json,fd_read,canonical,sha,require,private_read,
    prelaunch_record,launch_record,write_new)


def gate(directory,launch_fd,*,pause=time.sleep):
    raw=fd_read(launch_fd,16384);launch=closed_json(raw)
    m={k:launch[k] for k in ('operation_nonce','started','deadline')}
    prelaunch_record(raw,m,launch['manifest_sha256'])
    actual,_=private_read(directory/'launch.once',16384);require(actual==raw)
    require(launch['started']<=time.monotonic()<launch['deadline'])
    write_new(directory/'parked.private.json',canonical(dict(pid=os.getpid(),ppid=os.getppid(),
        executable=sys.executable,launch_sha256=sha(raw),independent_observation=False)))
    while True:
        require(launch['started']<=time.monotonic()<launch['deadline'])
        try:caller_raw,_=private_read(directory/'caller.once',16384)
        except FileNotFoundError:pause(.1);continue
        value=closed_json(caller_raw)
        require(type(value) is dict and set(value)=={'launch_sha256','binding'} and value['launch_sha256']==sha(raw))
        launch_record(canonical(value['binding']),m,launch['manifest_sha256'])
        require(all(value['binding'][k]==launch[k] for k in launch if k!='format'))
        return


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',required=True)
    for name in ('launch','manifest','authority','report'):p.add_argument('--'+name+'-fd',type=int,required=True)
    args=p.parse_args();directory=Path(args.directory)
    require(directory.is_absolute() and len({args.launch_fd,args.manifest_fd,args.authority_fd,args.report_fd})==4)
    gate(directory,args.launch_fd)
    # Only now make the original three FDs survive exec. The same process/PID
    # enters the actual entry; no replacement child, shell or deadline reset.
    fds=(args.manifest_fd,args.authority_fd,args.report_fd)
    for fd in fds:os.set_inheritable(fd,True)
    os.close(args.launch_fd)
    entry=Path(__file__).with_name('verify-b3-target-lifecycle.py').resolve()
    argv=[sys.executable,'-B',str(entry),'--execute']
    for name,fd in zip(('manifest','authority','report'),fds):argv+=['--'+name+'-fd',str(fd)]
    env=dict(PATH='/usr/bin:/bin',LANG='C',LC_ALL='C',HOME=str(directory))
    os.execve(sys.executable,argv,env)


if __name__=='__main__':
    try:main()
    except BaseException:
        print('launch_gate_failed',file=sys.stderr)
        raise SystemExit(1)
