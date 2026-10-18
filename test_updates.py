"""Update integrity, transactions and cancellation; no real network or devices."""
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
import rmaa_updates as u

def bundle(path,version='6.4.1',extra=None):
    path.mkdir();files={'RMAA+.exe':b'new executable','portable.flag':b'', '_internal/runtime.bin':b'new runtime', 'README.md':b'new docs'}
    files.update(extra or {})
    for name,data in files.items():
        p=path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    meta={'schema':1,'product':'RMAA+','version':version,'files':{name:u.sha256(path/name) for name in files}}
    (path/'bundle.json').write_text(json.dumps(meta),encoding='utf8');return meta

def archive(path,folder):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for p in folder.rglob('*'):
            if p.is_file():z.write(p,'RMAA+/'+p.relative_to(folder).as_posix())

def old_app(path):
    bundle(path,'6.4.0',{'obsolete.bin':b'old-only'})
    (path/'RMAA+.exe').write_bytes(b'old executable')
    (path/'data').mkdir();(path/'data/preferences.json').write_bytes(b'{"device":"keep me"}')
    (path/'my-measurement.json').write_bytes(b'private local result')
    return {p.relative_to(path).as_posix():p.read_bytes() for p in path.rglob('*') if p.is_file()}

class UpdateTests(unittest.TestCase):
    def release(self,version='6.4.1'):
        name=f'RMAA-Plus-v{version}-win-x64.zip'
        return {'tag_name':'v'+version,'draft':False,'prerelease':False,'assets':[{'name':name,'state':'uploaded','digest':'sha256:'+'a'*64,'size':1024,'browser_download_url':f'{u.PROJECT_URL}/releases/download/v{version}/{name}'}]}

    def test_numeric_versions_and_release_trust_boundary(self):
        self.assertEqual(u.parse_release(self.release('6.10.0'),'6.9.0')['version'],'6.10.0')
        self.assertIsNone(u.parse_release(self.release(),'6.4.1'))
        for version in ('1.2','1.2.3beta','01.2.3','../../1.2.3'):
            with self.assertRaises(ValueError):u.version_tuple(version)
        for key,value in (('digest',''),('size',0),('browser_download_url','https://attacker.invalid/a.zip')):
            data=self.release();data['assets'][0][key]=value
            with self.assertRaises(ValueError):u.parse_release(data,'6.4.0')

    def test_rejects_prerelease_and_duplicate_assets(self):
        data=self.release();data['prerelease']=True
        with self.assertRaises(ValueError):u.parse_release(data,'6.4.0')
        data=self.release();data['assets']*=2
        with self.assertRaises(ValueError):u.parse_release(data,'6.4.0')

    def test_archive_paths_cannot_overwrite_user_data_or_escape(self):
        for name in ('RMAA+/../outside','RMAA+/data/preferences.json','RMAA+/_internal/a:stream','RMAA+/CON.txt','RMAA+/a\\b','outside.exe'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as d:
                root=Path(d).resolve();z=root/'bad.zip'
                with zipfile.ZipFile(z,'w') as f:
                    info=zipfile.ZipInfo('entry');info.filename=name;f.writestr(info,b'evil')
                with self.assertRaises(ValueError):u.unpack_verified(z,root/'out')
                self.assertFalse((root/'outside').exists())

    def test_archive_rejects_symlink_case_collision_and_size_budget(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for mode in ('symlink','duplicate','budget'):
                z=root/(mode+'.zip')
                with zipfile.ZipFile(z,'w') as f:
                    if mode=='symlink':
                        info=zipfile.ZipInfo('RMAA+/link');info.create_system=3;info.external_attr=0o120777<<16;f.writestr(info,'../../outside')
                    else:f.writestr('RMAA+/a',b'abc');f.writestr('RMAA+/A',b'def')
                with patch.object(u,'MAX_UNPACKED',1 if mode=='budget' else u.MAX_UNPACKED):
                    with self.assertRaises(ValueError):u.unpack_verified(z,root/mode)

    def test_bundle_detects_corruption_and_extra_files(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bundle';bundle(p);u.validate_bundle(p,'6.4.1')
            (p/'_internal/runtime.bin').write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'hash'):u.validate_bundle(p)
            (p/'extra').write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'inventory'):u.validate_bundle(p)

    def test_download_integrity_and_cancel_leave_no_cache(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();b=root/'b';bundle(b);z=root/'bundle.zip';archive(z,b);payload=z.read_bytes()
            release={'url':'https://github.com/test','size':len(payload),'sha256':u.sha256(z),'version':'6.4.1'}
            with patch.object(u.urllib.request,'urlopen',return_value=io.BytesIO(payload)):
                work=u.prepare_update(release,root=root/'cache');u.validate_bundle(work/'bundle')
            u._owned_remove(work,root/'cache')
            release['sha256']='0'*64
            with patch.object(u.urllib.request,'urlopen',return_value=io.BytesIO(payload)):
                with self.assertRaisesRegex(ValueError,'SHA-256'):u.prepare_update(release,root=root/'cache')
            self.assertEqual(list((root/'cache').iterdir()),[])
            cancel=threading.Event();cancel.set()
            with patch.object(u.urllib.request,'urlopen',return_value=io.BytesIO(payload)):
                with self.assertRaises(u.UpdateCancelled):u.prepare_update(release,cancel,root=root/'cache')
            self.assertEqual(list((root/'cache').iterdir()),[])

    def test_success_preserves_all_user_data_and_removes_obsolete_owned_files(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();b=root/'new';bundle(b);target=root/'installed';old=old_app(target)
            u.install_bundle(b,target,root/'rollback',lambda path,version:version=='6.4.1')
            self.assertEqual((target/'RMAA+.exe').read_bytes(),b'new executable')
            for name in ('data/preferences.json','my-measurement.json'):self.assertEqual((target/name).read_bytes(),old[name])
            self.assertFalse((target/'obsolete.bin').exists())
            self.assertEqual(json.loads((root/'rollback/journal.json').read_text())['state'],'complete')

    def test_failed_startup_restores_exact_original_tree(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();b=root/'new';bundle(b,extra={'new-file':b'new'});target=root/'installed';old=old_app(target)
            with self.assertRaisesRegex(RuntimeError,'self-test'):u.install_bundle(b,target,root/'rollback',lambda *args:False)
            actual={p.relative_to(target).as_posix():p.read_bytes() for p in target.rglob('*') if p.is_file()}
            self.assertEqual(actual,old)
            self.assertEqual(json.loads((root/'rollback/journal.json').read_text())['state'],'rolled_back')

    def test_locked_executable_rolls_back_only_files_actually_touched(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();b=root/'new';bundle(b);target=root/'installed';old=old_app(target);replace=u.os.replace
            def locked(source,dest):
                if Path(dest)==target/'RMAA+.exe':raise PermissionError('second instance holds EXE')
                return replace(source,dest)
            with patch.object(u.os,'replace',side_effect=locked):
                with self.assertRaises(PermissionError):u.install_bundle(b,target,root/'rollback',lambda *args:True)
            self.assertEqual({p.relative_to(target).as_posix():p.read_bytes() for p in target.rglob('*') if p.is_file()},old)

    def test_cleanup_cannot_delete_parent_or_other_directories(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();(root/'sentinel').write_text('keep')
            with self.assertRaises(ValueError):u._owned_remove(root,root)
            with self.assertRaises(ValueError):u._owned_remove(root/'nested/child',root)
            self.assertTrue((root/'sentinel').exists())

    def test_offline_recovery_restores_interrupted_program_update(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();cache=root/'cache';cache.mkdir();work=cache/'owned';work.mkdir()
            target=root/'app';old=old_app(target);b=root/'new';bundle(b,extra={'new-file':b'new'})
            with self.assertRaises(RuntimeError):u.install_bundle(b,target,work/'rollback',lambda *args:False)
            (work/'plan.json').write_text(json.dumps({'schema':1,'target':str(target)}))
            (target/'RMAA+.exe').write_bytes(b'interrupted partial upgrade');(target/'new-file').write_bytes(b'new')
            with patch.object(u,'cache_root',return_value=cache):self.assertEqual(u.recover_update(work/'plan.json'),0)
            self.assertEqual({p.relative_to(target).as_posix():p.read_bytes() for p in target.rglob('*') if p.is_file()},old)

    def test_corrupted_recovery_copy_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();cache=root/'cache';cache.mkdir();work=cache/'owned';work.mkdir()
            target=root/'app';old_app(target);b=root/'new';bundle(b)
            with self.assertRaises(RuntimeError):u.install_bundle(b,target,work/'rollback',lambda *args:False)
            (work/'plan.json').write_text(json.dumps({'schema':1,'target':str(target)}))
            (work/'rollback/RMAA+.exe').write_bytes(b'corrupt')
            with patch.object(u,'cache_root',return_value=cache):
                with self.assertRaisesRegex(ValueError,'hash'):u.recover_update(work/'plan.json')

    def test_no_network_at_import_or_idle(self):
        import importlib
        with patch.object(u.urllib.request,'urlopen',side_effect=AssertionError('network at import')):importlib.reload(u)
