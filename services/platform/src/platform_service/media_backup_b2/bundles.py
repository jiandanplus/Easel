"""Bounded archival configuration values. Item names never become restore paths."""
import base64
import hashlib
import json
import os
import stat
from media_backup.contract import canonical, ClosureError
from .contracts import B2Error, require, validate_hash, validate_int

MAX_BUNDLE_BYTES=16*1024**2
MAX_BUNDLES_BYTES=32*1024**2
MAX_FACTS_BYTES=8*1024**2
ALLOWLIST={'central':frozenset({'application','database','maintenance'}),
           'identity':frozenset({'application','database'}),
           'media':frozenset({'storage','gateway'})}

def canonical_bytes(value):
    try:return canonical(value)
    except ClosureError:raise B2Error() from None

def _unique(pairs):
    value={}
    for key,item in pairs:require(key not in value);value[key]=item
    return value

def _bad(_):raise B2Error()

def strict_json(raw,maximum):
    require(type(raw) is bytes and 0<len(raw)<=maximum)
    try:value=json.loads(raw.decode('utf-8'),object_pairs_hook=_unique,parse_float=_bad,parse_constant=_bad)
    except (ValueError,UnicodeError,RecursionError):raise B2Error() from None
    require(canonical_bytes(value)==raw)
    return value

def config_item(name,raw,uid,gid,mode):
    require(type(name) is str and name in set().union(*ALLOWLIST.values()))
    require(type(raw) is bytes and len(raw)<=MAX_BUNDLE_BYTES//2)
    validate_int(uid,2**32-1);validate_int(gid,2**32-1)
    require(type(mode) is int and mode in {0o600,0o640,0o644})
    return {'name':name,'uid':uid,'gid':gid,'mode':mode,'size':len(raw),
            'sha256':hashlib.sha256(raw).hexdigest(),'data_b64':base64.b64encode(raw).decode('ascii')}

def _validate_item(item,kind):
    require(type(item) is dict and item.keys()=={'name','uid','gid','mode','size','sha256','data_b64'})
    require(type(item['name']) is str and item['name'] in ALLOWLIST[kind])
    require(type(item['data_b64']) is str and len(item['data_b64'])<=MAX_BUNDLE_BYTES)
    try:raw=base64.b64decode(item['data_b64'],validate=True)
    except (ValueError,UnicodeError):raise B2Error() from None
    require(config_item(item['name'],raw,item['uid'],item['gid'],item['mode'])==item)

def encode_bundle(kind,items,*,source_facts=None):
    require(type(kind) is str and kind in ALLOWLIST and type(items) is list)
    value={'format':'easel-config-bundle-v1','kind':kind,'items':sorted(items,key=lambda v:v['name'])}
    if kind=='central':
        require(type(source_facts) is dict)
        facts=canonical_bytes(source_facts);require(len(facts)<=MAX_FACTS_BYTES)
        value.update(source_facts=source_facts,source_facts_sha256=hashlib.sha256(facts).hexdigest())
    else:require(source_facts is None)
    raw=canonical_bytes(value);decode_bundle(raw,kind);return raw

def decode_bundle(raw,kind):
    require(type(kind) is str and kind in ALLOWLIST)
    value=strict_json(raw,MAX_BUNDLE_BYTES)
    expected={'format','kind','items'}|({'source_facts','source_facts_sha256'} if kind=='central' else set())
    require(type(value) is dict and value.keys()==expected)
    require(value['format']=='easel-config-bundle-v1' and value['kind']==kind)
    items=value['items'];require(type(items) is list and len(items)<=len(ALLOWLIST[kind]))
    names=[]
    for item in items:_validate_item(item,kind);names.append(item['name'])
    require(names==sorted(set(names)))
    if kind=='central':
        require(type(value['source_facts']) is dict)
        facts=canonical_bytes(value['source_facts']);require(len(facts)<=MAX_FACTS_BYTES)
        validate_hash(value['source_facts_sha256']);require(hashlib.sha256(facts).hexdigest()==value['source_facts_sha256'])
    return value

def item_metadata(items):return [{k:v for k,v in row.items() if k!='data_b64'} for row in items]

def _stamp(info):
    return (info.st_dev,info.st_ino,info.st_ctime_ns,info.st_mtime_ns,info.st_size,
            info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode),info.st_nlink)

class ConfigInputs:
    """Trusted host mapping is fixed by the caller before capture; open only flat FDs."""
    def __init__(self,root_fd,mapping,*,owner):
        self._fds=[];self._rows={};self._root=None
        try:
            self._root=os.dup(root_fd);self._root_stamp=_stamp(os.fstat(self._root))
            require(type(mapping) is dict and mapping.keys()==ALLOWLIST.keys())
            root=os.fstat(self._root);require(stat.S_ISDIR(root.st_mode) and stat.S_IMODE(root.st_mode)==0o700 and (root.st_uid,root.st_gid)==owner)
            for kind,entries in mapping.items():
                require(type(entries) is dict and entries.keys()<=ALLOWLIST[kind])
                rows=[]
                for logical,name in sorted(entries.items()):
                    require(type(name) is str and name and '/' not in name and name not in {'.','..'} and '\x00' not in name)
                    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=self._root);self._fds.append((fd,name,_stamp(os.fstat(fd))))
                    info=os.fstat(fd);require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and (info.st_uid,info.st_gid)==owner and info.st_size<=MAX_BUNDLE_BYTES//2)
                    raw=os.pread(fd,info.st_size+1,0);require(len(raw)==info.st_size)
                    rows.append(config_item(logical,raw,info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode)))
                self._rows[kind]=rows
            self.verify()
        except Exception:self.close();raise B2Error() from None
        except BaseException:self.close();raise
    def items(self,kind):
        require(type(kind) is str and kind in self._rows)
        return [dict(row) for row in self._rows[kind]]
    def verify(self):
        try:
            require(_stamp(os.fstat(self._root))==self._root_stamp)
            for fd,name,stamp in self._fds:
                require(_stamp(os.fstat(fd))==stamp and _stamp(os.stat(name,dir_fd=self._root,follow_symlinks=False))==stamp)
                raw=os.pread(fd,stamp[4]+1,0);require(len(raw)==stamp[4])
                # The same FD bytes are compared to the original decoded item.
                matches=[row for rows in self._rows.values() for row in rows if row['sha256']==hashlib.sha256(raw).hexdigest()]
                require(matches)
        except (OSError,ValueError,TypeError):raise B2Error() from None
    def close(self):
        for fd,_,_ in self._fds:os.close(fd)
        self._fds=[]
        if self._root is not None:os.close(self._root);self._root=None
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
