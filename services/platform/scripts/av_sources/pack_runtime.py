"""Bounded Linux AV runtime evidence. Importing this module has no native effects."""
import os
import platform
import re
import resource
import signal
import stat

LIMITS = {'AS': [536870912, 536870912], 'CPU': [30, 30], 'CORE': [0, 0],
          'FSIZE': [16777216, 16777216], 'NOFILE': [64, 64]}
KEYS = {'uid','gid','groups','pid','ppid','supervisor_pid','system','machine','limits',
        'timer_remaining','timer_interval','timer_arms','caps','no_new_privs','seccomp',
        'interfaces','routes','cgroup_path','cgroup_readonly','memory_max','memory_swap_max',
        'pids_max','cpu_max','mounts_validated','cgroup_scope','cgroup_namespace','supervisor_cgroup_namespace',
        'alarm_default','alarm_blocked','alarm_pending','supervisor_term_int_unblocked'}


def validate_runtime(p, profile):
    def need(value):
        if not value: raise ValueError('runtime proof rejected')
    need(type(p) is dict and set(p) == KEYS)
    for key in ('uid','gid','pid','ppid','supervisor_pid','timer_arms','no_new_privs','seccomp','memory_max','memory_swap_max','pids_max'):
        need(type(p[key]) is int)
    need(p['uid'] == profile['uid'] and p['gid'] == profile['gid'])
    need(p['pid'] > 1 and p['ppid'] == p['supervisor_pid'] == 1)
    need(p['system'] == 'Linux' and p['machine'] == 'aarch64')
    need(type(p['groups']) is list and all(type(x) is int and x == profile['gid'] for x in p['groups']))
    need(type(p['limits']) is dict and p['limits'] == LIMITS)
    for value in p['limits'].values(): need(type(value) is list and all(type(x) is int for x in value))
    need(type(p['timer_remaining']) in (int,float) and 0 < p['timer_remaining'] <= 120)
    need(type(p['timer_interval']) in (int,float) and p['timer_interval'] == 0 and p['timer_arms'] == 1)
    need(p['alarm_default'] is True and p['alarm_blocked'] is False and p['alarm_pending'] is False
         and p['supervisor_term_int_unblocked'] is True)
    need(p['caps'] == ['0000000000000000'] * 5)
    need(p['no_new_privs'] == 1 and p['seccomp'] == 2)
    need(p['interfaces'] == ['lo'] and p['routes'] == [])
    need(type(p['cgroup_path']) is str and p['cgroup_path'].startswith('/')
         and (p['cgroup_path']=='/' or all(x not in ('.','..','') for x in p['cgroup_path'].split('/')[1:])))
    need(p['cgroup_scope']=='namespace-relative;host-inspect-required')
    need(type(p['cgroup_namespace']) is str and re.fullmatch(r'cgroup:\[[1-9][0-9]{0,19}\]',p['cgroup_namespace']) is not None
         and p['supervisor_cgroup_namespace']==p['cgroup_namespace'])
    need(p['cgroup_readonly'] is True and p['mounts_validated'] is True)
    need(p['memory_max'] == 536870912 and p['memory_swap_max'] == 0 and p['pids_max'] == 16)
    q = p['cpu_max']
    need(type(q) is list and len(q) == 2 and all(type(x) is int and x > 0 for x in q) and q[0] <= 2*q[1])
    return p


def _read(path, cap=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        out = bytearray()
        while True:
            chunk = os.read(fd, min(4096, cap + 1 - len(out)))
            if not chunk: break
            out.extend(chunk)
            if len(out) > cap: raise ValueError('runtime field over budget')
        return out.decode('ascii')
    finally: os.close(fd)


def _mounts(text, profile):
    mounts = {}
    for line in text.splitlines():
        left, sep, right = line.partition(' - ')
        a, b = left.split(), right.split()
        if not sep or len(a) < 6 or len(b) != 3: raise ValueError('mount format')
        path = a[4]
        if '\\' in path or path in mounts: raise ValueError('ambiguous mount')
        mounts[path] = (set(a[5].split(',')), b[0], set(b[2].split(',')), a[3])
    for path in ('/', '/code','/inputs'):
        if path not in mounts or 'ro' not in mounts[path][0]: raise ValueError('readonly mount required')
    if '/work' not in mounts: raise ValueError('missing work mount')
    flags, fs, opts, root = mounts['/work']
    if fs != 'tmpfs' or not {'rw','noexec','nosuid','nodev'} <= flags: raise ValueError('work flags')
    required = {'size=32768k','mode=700',f'uid={profile["uid"]}',f'gid={profile["gid"]}'}
    if not required <= opts: raise ValueError('work options')
    # Explicit standard pseudo files only. No extra writable data mount is admitted.
    pseudo = {'/proc','/proc/bus','/proc/fs','/proc/irq','/proc/sys','/proc/sysrq-trigger',
              '/proc/acpi','/proc/interrupts','/proc/kcore','/proc/keys','/proc/latency_stats',
              '/proc/timer_list','/proc/scsi','/dev','/dev/pts','/dev/mqueue','/dev/shm',
              '/etc/resolv.conf','/etc/hostname','/etc/hosts'}
    for path, (flags, fs, opts, root) in mounts.items():
        if 'rw' in flags and path != '/work' and path not in pseudo: raise ValueError('unknown writable mount')
        if path in pseudo:
            if path.startswith('/etc/'):
                if fs not in ('ext4','xfs','virtiofs','tmpfs'): raise ValueError('engine etc mount')
            elif path.startswith('/proc'):
                if fs not in ('proc','tmpfs'): raise ValueError('proc mount')
            elif fs not in ('tmpfs','devpts','mqueue'): raise ValueError('dev mount')
    if '/dev/shm' not in mounts or 'size=1024k' not in mounts['/dev/shm'][2]: raise ValueError('shm bound')
    cg = mounts.get('/sys/fs/cgroup')
    if cg is None or cg[1] != 'cgroup2' or 'ro' not in cg[0]: raise ValueError('readonly cgroup required')
    return True


def collect_runtime(profile, *, parent_pid, timer_arms, supervisor_signals_ok):
    if platform.system() != 'Linux' or platform.machine() != 'aarch64': raise ValueError('unsupported runtime')
    fields = {}
    for line in _read('/proc/self/status').splitlines():
        key, sep, value = line.partition(':')
        if sep:
            if key in fields: raise ValueError('duplicate status key')
            fields[key] = value.strip()
    cg = _read('/proc/self/cgroup', 4096).strip()
    if not re.fullmatch(r'0::/[^\n]*', cg): raise ValueError('cgroupv2 required')
    cgpath = cg[3:]
    # '/' is the normal private cgroup namespace root, not a host identity.
    # Host inspect must independently establish CgroupnsMode=private.
    if cgpath != '/' and any(x in ('.','..','') for x in cgpath.split('/')[1:]): raise ValueError('unsafe cgroup path')
    mount_text = _read('/proc/self/mountinfo')
    mounts = _mounts(mount_text, profile)
    # Mount-root-relative cgroup view: resolve only the unique cgroup2 mount.
    cgroot = '/sys/fs/cgroup'
    mount_lines = mount_text.splitlines()
    rows = [s.split(' - ')[0].split() for s in mount_lines if ' - cgroup2 ' in s]
    if len(rows) != 1 or rows[0][4] != cgroot: raise ValueError('ambiguous cgroup mount')
    mount_root = rows[0][3]
    if mount_root == cgpath: base = cgroot
    elif mount_root == '/': base = cgroot + cgpath
    else: raise ValueError('cgroup mount identity mismatch')
    route = _read('/proc/net/route').splitlines()
    route6 = _read('/proc/net/ipv6_route').splitlines()
    # IPv6 loopback local route is harmless; every non-lo interface/route fails.
    routes = [line for line in route[1:] if line.strip()]
    routes += [line for line in route6 if line.split()[-1:] != ['lo']]
    timer = signal.getitimer(signal.ITIMER_REAL)
    mask = signal.pthread_sigmask(signal.SIG_BLOCK,set())
    parent_fields={}
    for line in _read('/proc/1/status').splitlines():
        key,sep,value=line.partition(':')
        if sep:
            if key in parent_fields: raise ValueError('duplicate supervisor status')
            parent_fields[key]=value.strip()
    parent_mask=parent_fields.get('SigBlk','')
    if not re.fullmatch(r'[0-9a-f]{16}',parent_mask): raise ValueError('supervisor signal mask missing')
    cancellation_bits=(1 << (signal.SIGTERM-1)) | (1 << (signal.SIGINT-1))
    p = dict(uid=os.geteuid(), gid=os.getegid(), groups=os.getgroups(), pid=os.getpid(),
             ppid=os.getppid(), supervisor_pid=parent_pid, system=platform.system(), machine=platform.machine(),
             limits={n:list(resource.getrlimit(getattr(resource,'RLIMIT_'+n))) for n in LIMITS},
             timer_remaining=timer[0], timer_interval=timer[1], timer_arms=timer_arms,
             alarm_default=signal.getsignal(signal.SIGALRM)==signal.SIG_DFL,
             alarm_blocked=signal.SIGALRM in mask,alarm_pending=signal.SIGALRM in signal.sigpending(),
             supervisor_term_int_unblocked=supervisor_signals_ok is True and not (int(parent_mask,16)&cancellation_bits),
             caps=[fields[k] for k in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')],
             no_new_privs=int(fields['NoNewPrivs']), seccomp=int(fields['Seccomp']),
             interfaces=sorted(os.listdir('/sys/class/net')), routes=routes, cgroup_path=cgpath,
             cgroup_scope='namespace-relative;host-inspect-required',
             cgroup_namespace=os.readlink('/proc/self/ns/cgroup'),
             supervisor_cgroup_namespace=os.readlink('/proc/1/ns/cgroup'),
             cgroup_readonly=True, mounts_validated=mounts,
             memory_max=int(_read(base+'/memory.max',128).strip()),
             memory_swap_max=int(_read(base+'/memory.swap.max',128).strip()),
             pids_max=int(_read(base+'/pids.max',128).strip()),
             cpu_max=[int(x) for x in _read(base+'/cpu.max',128).split()])
    return validate_runtime(p, profile)
