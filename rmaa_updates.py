"""User-triggered public GitHub updates. No credentials, telemetry or idle poll.

The downloaded bundle runs its helper outside the installed directory. Files
are journaled, replaced with rollback, and self-tested before a normal restart.
User data and files outside the incoming inventory are never replaced.
"""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import zipfile

REPOSITORY='evdokimov1996/rmaa-plus'
PROJECT_URL='https://github.com/'+REPOSITORY
LATEST_URL='https://api.github.com/repos/'+REPOSITORY+'/releases/latest'
MAX_DOWNLOAD=512*1024*1024
MAX_UNPACKED=1024*1024*1024
EXE='RMAA+.exe'

class UpdateCancelled(Exception):pass

def check_cancel(cancel):
    if cancel is not None and cancel.is_set():raise UpdateCancelled('Update cancelled')

def version_tuple(value):
    match=re.fullmatch(r'v?(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})',str(value))
    if not match:raise ValueError('Unsupported release version')
    return tuple(map(int,match.groups()))

def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()

def _request(url):
    return urllib.request.Request(url,headers={'User-Agent':'RMAA-Plus-Updater','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})

def fetch_latest(current,cancel=None):
    check_cancel(cancel)
    with urllib.request.urlopen(_request(LATEST_URL),timeout=15) as response:
        data=response.read(2_000_001)
    if len(data)>2_000_000:raise ValueError('Release metadata too large')
    check_cancel(cancel)
    return parse_release(json.loads(data),current)

def parse_release(data,current):
    if not isinstance(data,dict) or data.get('draft') or data.get('prerelease'):raise ValueError('Not a stable release')
    version=str(data.get('tag_name','')).removeprefix('v')
    if version_tuple(version)<=version_tuple(current):return None
    name=f'RMAA-Plus-v{version}-win-x64.zip'
    candidates=[a for a in data.get('assets',[]) if a.get('name')==name and a.get('state')=='uploaded']
    if len(candidates)!=1:raise ValueError('Windows x64 archive missing or ambiguous')
    asset=candidates[0];digest=asset.get('digest','');size=asset.get('size')
    if not re.fullmatch('sha256:[0-9a-f]{64}',digest):raise ValueError('GitHub has not provided a SHA-256 digest')
    if type(size)!=int or not 1<=size<=MAX_DOWNLOAD:raise ValueError('Invalid archive size')
    expected=f'{PROJECT_URL}/releases/download/v{version}/{name}'
    if asset.get('browser_download_url')!=expected:raise ValueError('Unexpected release download URL')
    return dict(version=version,name=name,url=expected,size=size,sha256=digest[7:],
                release_url=f'{PROJECT_URL}/releases/tag/v{version}')

def cache_root():
    return Path(os.environ.get('LOCALAPPDATA',tempfile.gettempdir()))/'RMAAOpenLab'/'updates'

def _owned_remove(path,parent):
    path=Path(path).resolve();parent=Path(parent).resolve()
    if path.parent!=parent or path==parent:raise ValueError('Cleanup outside update cache')
    if path.exists():shutil.rmtree(path)

def _relative(name):
    if not isinstance(name,str) or '\\' in name or ':' in name:raise ValueError('Unsafe bundle path')
    p=PurePosixPath(name)
    if p.is_absolute() or not p.parts or any(part in ('','..','.') or part.endswith((' ','.')) for part in p.parts):raise ValueError('Unsafe bundle path')
    if any(part.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))} for part in p.parts):raise ValueError('Reserved Windows path')
    if p.parts[0].lower() in ('data','.updates','.git'):raise ValueError('User data cannot be part of an update')
    return p

def unpack_verified(archive,destination,cancel=None):
    destination=Path(destination);destination.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist()
        if len(infos)>10000 or sum(i.file_size for i in infos)>MAX_UNPACKED:raise ValueError('Bundle exceeds extraction budget')
        seen=set()
        for info in infos:
            check_cancel(cancel)
            if info.is_dir():continue
            prefix='RMAA+/'
            if info.filename!=info.orig_filename:raise ValueError('Noncanonical archive filename')
            if not info.filename.startswith(prefix):raise ValueError('Unexpected archive root')
            relative=_relative(info.filename[len(prefix):]);key=str(relative).casefold()
            if key in seen or stat.S_ISLNK(info.external_attr>>16) or info.flag_bits&1:raise ValueError('Duplicate, linked or encrypted archive entry')
            seen.add(key);target=destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True,exist_ok=True)
            with z.open(info) as source,target.open('xb') as output:
                while True:
                    check_cancel(cancel);block=source.read(256*1024)
                    if not block:break
                    output.write(block)
    return validate_bundle(destination)

def validate_bundle(folder,version=None):
    folder=Path(folder);manifest=folder/'bundle.json'
    if not manifest.is_file():raise ValueError('Bundle manifest is missing')
    if manifest.stat().st_size>2_000_000:raise ValueError('Bundle manifest too large')
    value=json.loads(manifest.read_text(encoding='utf8'))
    if value.get('product')!='RMAA+' or value.get('schema')!=1:raise ValueError('Unknown bundle')
    version_tuple(value['version'])
    if version is not None and value['version']!=version:raise ValueError('Bundle version mismatch')
    files=value.get('files')
    if not isinstance(files,dict) or not 2<=len(files)<=10000 or EXE not in files or 'portable.flag' not in files:raise ValueError('Incomplete bundle inventory')
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() and p.relative_to(folder).as_posix()!='bundle.json'}
    if actual!=set(files):raise ValueError('Bundle inventory mismatch')
    for relative,digest in files.items():
        p=_relative(relative)
        if not re.fullmatch('[0-9a-f]{64}',str(digest)) or sha256(folder.joinpath(*p.parts))!=digest:raise ValueError('Bundle file hash mismatch: '+relative)
    return value

def prepare_update(release,cancel=None,progress=None,root=None):
    root=Path(root) if root else cache_root();root.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(root).free<MAX_UNPACKED+release['size']*2:raise OSError('At least 1.2 GB free space is required for an update')
    work=root/str(uuid.uuid4());work.mkdir();archive=work/'download.zip'
    try:
        digest=hashlib.sha256();received=0
        with urllib.request.urlopen(_request(release['url']),timeout=15) as response,archive.open('xb') as output:
            while True:
                check_cancel(cancel);block=response.read(256*1024)
                if not block:break
                received+=len(block)
                if received>release['size']:raise ValueError('Archive larger than declared size')
                digest.update(block);output.write(block)
                if progress:progress(received/release['size'])
        if received!=release['size'] or digest.hexdigest()!=release['sha256']:raise ValueError('Downloaded archive SHA-256/size mismatch')
        unpack_verified(archive,work/'bundle',cancel)
        validate_bundle(work/'bundle',release['version'])
        check_cancel(cancel)
        (work/'verified.json').write_text(json.dumps(release),encoding='utf8')
        return work
    except BaseException:
        _owned_remove(work,root);raise

def _json_write(path,value):
    path=Path(path);temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8');os.replace(temporary,path)

def install_bundle(bundle,target,backup,health_check):
    """Transactional owned-file update. No recursive move/delete of user data."""
    bundle=Path(bundle).resolve();target=Path(target).resolve();backup=Path(backup).resolve()
    if bundle==target or target in bundle.parents or bundle in target.parents:raise ValueError('Staging and installed app must be separate')
    if not (target/EXE).is_file() or not (target/'portable.flag').is_file():raise ValueError('Select the existing portable RMAA+ folder')
    if target in backup.parents or bundle in backup.parents:raise ValueError('Rollback must be outside the application bundle')
    manifest=validate_bundle(bundle);backup.mkdir(exist_ok=False)
    names=set(manifest['files'])|{'bundle.json'}
    old_manifest=target/'bundle.json'
    if old_manifest.is_file() and old_manifest.stat().st_size<2_000_000:
        old=json.loads(old_manifest.read_text(encoding='utf8'))
        if old.get('product')=='RMAA+' and old.get('schema')==1:
            for name in old.get('files',{}):_relative(name);names.add(name)
    inventory=sorted(names);journal=[];prepared=False;touched=[];old_hashes={}
    try:
        for name in inventory:
            relative=_relative(name);dest=target.joinpath(*relative.parts)
            if dest.is_symlink() or any(p.is_symlink() for p in dest.parents if p!=target.parent):raise ValueError('Linked installation paths are unsupported')
            if dest.exists() and not dest.is_file():raise ValueError('Bundle path conflicts with a directory')
            journal.append((name,dest.exists()))
            if dest.is_file():old_hashes[name]=sha256(dest)
        needed=sum((target/name).stat().st_size for name,exists in journal if exists)+32*1024*1024
        if shutil.disk_usage(backup).free<needed:raise OSError('Not enough free space for rollback')
        for name,exists in journal:
            if not exists:continue
            saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target/name,saved)
            if sha256(saved)!=old_hashes[name]:raise OSError('Rollback copy failed integrity check')
        _json_write(backup/'journal.json',{'target':str(target),'files':journal,'old_sha256':old_hashes,'version':manifest['version'],'state':'applying'})
        prepared=True
        for name in inventory:
            source=bundle/name;dest=target/name
            if not source.is_file():
                if dest.is_file():dest.unlink();touched.append(name)
                continue
            dest.parent.mkdir(parents=True,exist_ok=True)
            fd,temporary=tempfile.mkstemp(prefix='.rmaa-update-',dir=dest.parent)
            try:
                with os.fdopen(fd,'wb') as output,source.open('rb') as input_file:shutil.copyfileobj(input_file,output)
                os.replace(temporary,dest);touched.append(name)
            finally:
                if os.path.exists(temporary):os.unlink(temporary)
        if any(sha256(target/name)!=digest for name,digest in manifest['files'].items()):raise ValueError('Installed files failed hash verification')
        if not health_check(target,manifest['version']):raise RuntimeError('New application failed its startup self-test')
        _json_write(backup/'journal.json',{'target':str(target),'files':journal,'old_sha256':old_hashes,'state':'complete','version':manifest['version']})
        return manifest
    except BaseException:
        failures=[]
        for name,existed in reversed(journal) if prepared else ():
            if name not in touched:continue
            try:
                dest=target/name
                if existed:
                    if sha256(backup/name)!=old_hashes[name]:raise OSError('Rollback copy was modified')
                    shutil.copy2(backup/name,dest)
                    if sha256(dest)!=old_hashes[name]:raise OSError('Restored file hash mismatch')
                elif dest.is_file():dest.unlink()
            except OSError as error:failures.append(str(error))
        _json_write(backup/'journal.json',{'target':str(target),'files':journal,'old_sha256':old_hashes,'state':'rollback_failed' if failures else 'rolled_back','errors':failures})
        if failures:raise RuntimeError('Rollback incomplete; preserve update cache: '+str(backup))
        raise

def recover_update(plan_path):
    """Explicit offline recovery after power loss; runs from the staged EXE."""
    work=Path(plan_path).resolve().parent
    if work.parent!=cache_root().resolve():raise ValueError('Unowned recovery directory')
    plan=json.loads(Path(plan_path).read_text(encoding='utf8'))
    journal=json.loads((work/'rollback/journal.json').read_text(encoding='utf8'))
    target=Path(journal['target']).resolve()
    if plan.get('schema')!=1 or Path(plan['target']).resolve()!=target or not (target/'portable.flag').is_file():raise ValueError('Recovery target mismatch')
    for name,exists in journal['files']:
        _relative(name);dest=target/name
        if dest.is_symlink() or any(p.is_symlink() for p in dest.parents if p!=target.parent):raise ValueError('Linked recovery path')
        if exists:
            if sha256(work/'rollback'/name)!=journal['old_sha256'][name]:raise ValueError('Recovery copy hash mismatch')
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(work/'rollback'/name,dest)
        elif dest.is_file():dest.unlink()
    journal['state']='recovered';_json_write(work/'rollback/journal.json',journal)
    return 0

def wait_process(pid,seconds=60):
    if type(pid)!=int or pid<=0 or pid==os.getpid():raise ValueError('Invalid application process')
    if os.name!='nt':raise OSError('Portable self-update requires Windows')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32];kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32];kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x100000,False,pid)
    if not handle:
        if ctypes.get_last_error()==87:return
        raise OSError('Cannot wait for the running application')
    try:
        if kernel.WaitForSingleObject(handle,int(seconds*1000))!=0:raise TimeoutError('Close every RMAA+ window, then try again')
    finally:kernel.CloseHandle(handle)

def launch_installer(work,target):
    work=Path(work).resolve();target=Path(target).resolve()
    validate_bundle(work/'bundle')
    if work.parent!=cache_root().resolve():raise ValueError('Unowned staging directory')
    plan={'schema':1,'target':str(target),'pid':os.getpid()}
    _json_write(work/'plan.json',plan)
    return subprocess.Popen([str(work/'bundle'/EXE),'--apply-update',str(work/'plan.json')],cwd=work,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

def _health_check(target,version,work):
    receipt=work/'health.json';nonce=str(uuid.uuid4())
    result=subprocess.run([str(target/EXE),'--update-self-test',version,str(receipt),nonce],timeout=30,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return result.returncode==0 and receipt.is_file() and json.loads(receipt.read_text())=={'version':version,'nonce':nonce,'healthy':True}

def apply_update_main(plan_path):
    plan_path=Path(plan_path).resolve();work=plan_path.parent
    if work.parent!=cache_root().resolve() or not (work/'verified.json').is_file():return 2
    plan=json.loads(plan_path.read_text());target=Path(plan['target']).resolve()
    try:
        wait_process(plan['pid']);time.sleep(.3) # Release the PyInstaller parent EXE handle.
        manifest=install_bundle(work/'bundle',target,work/'rollback',lambda t,v:_health_check(t,v,work))
        _json_write(work/'complete.json',{'target':str(target),'version':manifest['version']})
        subprocess.Popen([str(target/EXE),'--cleanup-update',str(work),str(os.getpid())],cwd=target)
        return 0
    except BaseException as error:
        _json_write(work/'failure.json',{'error':str(error),'target':str(target)})
        ctypes.windll.user32.MessageBoxW(None,'Обновление не установлено. Предыдущая версия восстановлена, если доступ к файлам позволял откат.\n'+str(error),'RMAA+ update',0x10)
        return 1

def cleanup_after_update(work,pid):
    work=Path(work).resolve()
    if work.parent!=cache_root().resolve() or not (work/'complete.json').is_file():raise ValueError('Unowned update cleanup')
    receipt=json.loads((work/'complete.json').read_text())
    if Path(receipt['target']).resolve()!=Path(sys.executable).resolve().parent:raise ValueError('Cleanup belongs to another installation')
    wait_process(pid,30);time.sleep(.3)
    for attempt in range(3):
        try:_owned_remove(work,cache_root());return
        except PermissionError:
            if attempt==2:raise
            time.sleep(.25)
