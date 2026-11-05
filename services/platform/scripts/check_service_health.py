#!/usr/bin/env python3
"""Fixed 154 deployment health checks. Local JSON/journal only; no notifications."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import subprocess
import tempfile
import time
import urllib.request

TARGET='ser362540173134'
STATE=Path('/var/lib/easel-health/status.json')
SERVICES=('easel-central-production','easel-console-production','easel-gateway-tunnel','nginx')
CONTAINERS=('easel-central-pg-20261024','easel-identity-pg-v2-20261024','easel-logto-v2-20261024')
HOSTS=('accounts.drawbookai.cn','login.drawbookai.cn')

def evaluate(value,now):
    alerts=[]
    def add(code,severity='error'):alerts.append({'code':code,'severity':severity})
    for unit in SERVICES:
        if value.get('services',{}).get(unit)!='active':add('service_inactive:'+unit)
    for container in CONTAINERS:
        if value.get('containers',{}).get(container) is not True:add('container_inactive:'+container)
    for probe in ('api','console'):
        if value.get(probe) is not True:add(probe+'_unhealthy')
    if value.get('backup_timer') is not True:add('backup_timer_inactive')
    if value.get('backup_result')!='success':add('backup_failed')
    mtime=value.get('backup_mtime')
    if not isinstance(mtime,(int,float)):add('backup_missing')
    elif now-mtime>48*3600:add('backup_stale')
    elif now-mtime>30*3600:add('backup_stale','warning')
    for key,threshold,critical,code,inverse in [('disk_percent',80,90,'disk_pressure',False),('memory_available_mib',512,256,'memory_pressure',True)]:
        metric=value.get(key)
        if not isinstance(metric,(int,float)):add('metric_unavailable:'+('disk' if key=='disk_percent' else 'memory'))
        elif (metric<=critical if inverse else metric>=critical):add(code)
        elif (metric<=threshold if inverse else metric>=threshold):add(code,'warning')
    for host in HOSTS:
        expires=value.get('tls_expires',{}).get(host)
        if not isinstance(expires,(int,float)):add('tls_unavailable:'+host)
        elif expires-now<3*86400:add('tls_expiring:'+host)
        elif expires-now<14*86400:add('tls_expiring:'+host,'warning')
    return sorted(alerts,key=lambda row:(row['code'],row['severity']))

def alert_signature(alerts):
    return hashlib.sha256(json.dumps(sorted(alerts,key=lambda row:(row['code'],row['severity'])),sort_keys=True,separators=(',',':')).encode()).hexdigest()

def command(args):
    result=subprocess.run(args,capture_output=True,timeout=5,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C','HOME':'/root'})
    if result.returncode!=0 or len(result.stdout)>1024*1024:raise ValueError('probe_failed')
    return result.stdout

def attempt(fn,default=None):
    try:return fn()
    except Exception:return default

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def health(port,host,console=False):
    request=urllib.request.Request('http://127.0.0.1:'+str(port)+'/health',headers={'Host':host,'Accept':'application/json'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=3) as response:
        if response.status!=200:return False
        body=response.read(4097)
        if len(body)>4096:return False
        value=json.loads(body)
        return value.get('ok') is True and (not console or value.get('assets_ready') is True)

def certificate_expiry(host):
    context=ssl.create_default_context()
    with socket.create_connection(('127.0.0.1',443),timeout=3) as stream:
        with context.wrap_socket(stream,server_hostname=host) as connection:
            return ssl.cert_time_to_seconds(connection.getpeercert()['notAfter'])

def backup_mtime():
    values=[]
    for directory in (Path('/var/backups/easel-encrypted'),Path('/var/backups/easel-encrypted-v2-drill')):
        if not directory.exists():continue
        meta=directory.lstat()
        if not stat.S_ISDIR(meta.st_mode) or meta.st_uid!=0 or stat.S_IMODE(meta.st_mode)!=0o700:continue
        for path in directory.glob('*.sealed'):
            if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f-]{36}\.sealed',path.name):continue
            meta=path.lstat()
            if stat.S_ISREG(meta.st_mode) and meta.st_uid==0 and meta.st_nlink==1 and stat.S_IMODE(meta.st_mode)==0o600 and meta.st_size>0:
                values.append(meta.st_mtime)
    return max(values) if values else None

def collect():
    disk=os.statvfs('/');memory={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key,text=line.split(':',1);memory[key]=int(text.strip().split()[0])
    return {'services':{unit:attempt(lambda u=unit:command(['/usr/bin/systemctl','is-active',u]).decode().strip()) for unit in SERVICES},
            'containers':{name:attempt(lambda n=name:json.loads(command(['/usr/bin/docker','inspect','--format','{{json .State.Running}}',n]))) for name in CONTAINERS},
            'api':attempt(lambda:health(19874,'accounts.drawbookai.cn'),False),'console':attempt(lambda:health(19875,'127.0.0.1:18885',True),False),
            'backup_timer':attempt(lambda:command(['/usr/bin/systemctl','is-active','easel-encrypted-backup.timer']).decode().strip()=='active',False),
            'backup_result':attempt(lambda:command(['/usr/bin/systemctl','show','--property=Result','--value','easel-encrypted-backup.service']).decode().strip()),
            'backup_mtime':attempt(backup_mtime),'disk_percent':round(100*(1-disk.f_bavail/disk.f_blocks),2),
            'memory_available_mib':memory.get('MemAvailable',0)/1024 if 'MemAvailable' in memory else None,
            'tls_expires':{host:attempt(lambda h=host:certificate_expiry(h)) for host in HOSTS}}

def record(value):
    root=STATE.parent;root.mkdir(mode=0o700,exist_ok=True);meta=root.lstat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid!=0 or stat.S_IMODE(meta.st_mode)!=0o700:raise ValueError('private_state_required')
    previous=None
    if STATE.exists():
        meta=STATE.lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=0 or meta.st_nlink!=1 or stat.S_IMODE(meta.st_mode)!=0o600:raise ValueError('private_state_required')
        if meta.st_size>65536:raise ValueError('state_limit')
        previous=json.loads(STATE.read_bytes())
    payload=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    with tempfile.NamedTemporaryFile(dir=root,prefix='.health-',delete=False) as output:
        temporary=Path(output.name);output.write(payload);output.flush();os.fsync(output.fileno())
    os.replace(temporary,STATE)
    changed=not previous or previous.get('signature')!=value['signature']
    if changed:print(json.dumps({'event':'easel_health_changed','status':value['status'],'alerts':value['alerts']},sort_keys=True))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--record',action='store_true');args=parser.parse_args()
    if os.geteuid()!=0 or socket.gethostname()!=TARGET:raise ValueError('unsupported_target')
    os.umask(0o077);now=time.time();snapshot=collect();alerts=evaluate(snapshot,now)
    value={'format':'easel-health-v1','checked_at':int(now),'status':'error' if any(a['severity']=='error' for a in alerts) else 'warning' if alerts else 'ok',
           'signature':alert_signature(alerts),'alerts':alerts,'checks':snapshot}
    if args.record:record(value)
    else:print(json.dumps(value,sort_keys=True))
    return 1 if value['status']=='error' else 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        print('{"event":"easel_health_probe_failed"}');raise SystemExit(1)
