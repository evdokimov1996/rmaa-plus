"""Windows release builder. Pinned native sources; no user data in bundles."""
from pathlib import Path
import ctypes
import hashlib
import importlib.metadata as md
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from rmaa_version import VERSION
from rmaa_updates import validate_bundle,sha256

def run(*args):subprocess.run(list(map(str,args)),cwd=ROOT,check=True)

def download(url,path,digest):
    if path.is_file() and sha256(path)==digest:return
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'RMAA-Plus-build'}),timeout=60) as r,path.open('wb') as out:shutil.copyfileobj(r,out)
    if sha256(path)!=digest:raise ValueError('Native source hash mismatch: '+path.name)

def build_native(work):
    pins=json.loads((ROOT/'native-deps.json').read_text());native=work/'native';native.mkdir(parents=True,exist_ok=True)
    for name in ('portaudio','asio'):download(pins[name+'_url'],native/(name+'.zip'),pins[name+'_sha256'])
    source=native/('portaudio-'+pins['portaudio_commit'])
    if not source.exists():
        with zipfile.ZipFile(native/'portaudio.zip') as z:z.extractall(native)
    with zipfile.ZipFile(native/'asio.zip') as z:
        license_text=z.read('ASIOSDK/LICENSE.txt').decode('utf8',errors='replace')
        if 'General Public License' not in license_text:raise ValueError('ASIO GPL option missing')
    import _sounddevice_data
    for enabled in (False,True):
        build=native/('build-asio' if enabled else 'build-standard')
        run('cmake','-S',source,'-B',build,'-A','x64','-DPA_USE_ASIO='+('ON' if enabled else 'OFF'),
            '-DPA_DLL_LINK_WITH_STATIC_RUNTIME=ON','-DPA_BUILD_EXAMPLES=OFF','-DPA_BUILD_TESTS=OFF',
            '-DPA_BUILD_SHARED_LIBS=ON','-DASIO_SDK_ZIP_PATH='+str((native/'asio.zip').resolve()))
        run('cmake','--build',build,'--config','Release','--parallel','2')
        dlls=list((build/'Release').glob('portaudio*.dll'))
        if len(dlls)!=1:raise ValueError('Ambiguous native DLL output')
        if enabled:
            dll=dlls[0];library=ctypes.CDLL(str(dll));getattr(library,'PaAsio_GetAvailableBufferSizes')
        filename='libportaudio64bit'+('-asio' if enabled else '')+'.dll'
        destinations=list(Path(_sounddevice_data.__file__).parent.rglob(filename))
        if len(destinations)!=1:raise ValueError('Cannot locate x64 PortAudio runtime')
        shutil.copy2(dlls[0],destinations[0])
    return native,pins,dll

def license_files(stage):
    packages=('numpy','scipy','matplotlib','sounddevice','soundfile','cffi','pycparser','pillow',
              'contourpy','cycler','kiwisolver','packaging','python-dateutil','six','fonttools','pyparsing','pyinstaller')
    versions={}
    for name in packages:
        dist=md.distribution(name);versions[name]=dist.version
        for item in dist.files or ():
            p=Path(str(item));base=p.name.lower()
            if ('.dist-info' in str(p) and any(word in base for word in ('license','copying','notice'))) or (name=='pyinstaller' and base=='copying.txt'):
                target=stage/'licenses'/name/p.name
                # Preserve separate license files with the same basename.
                if target.exists():target=stage/'licenses'/name/Path(*p.parts[1:])
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dist.locate_file(item),target)
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.exists():shutil.copy2(python_license,stage/'licenses/Python-LICENSE.txt')
    import _soundfile_data
    for p in Path(_soundfile_data.__file__).parent.rglob('*'):
        if p.is_file() and any(word in p.name.lower() for word in ('license','copying','notice')):
            target=stage/'licenses/soundfile-runtime'/p.name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    return versions

def main():
    if os.name!='nt':raise OSError('Use a Windows x64 build host with Visual Studio C++ / CMake')
    work=ROOT/'build-release';work.mkdir(exist_ok=True)
    native,pins,dll=build_native(work)
    run(sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--windowed','--name','RMAA+',
        '--distpath',work/'dist','--workpath',work/'pyinstaller','--specpath',work,ROOT/'rmaa_classic.py')
    stage=work/'dist/RMAA+'
    # No prebuilt ASIO DLL with unknown SDK provenance is redistributed.
    for path in (stage/'_internal').rglob('libportaudio*.dll'):
        if path.name not in ('libportaudio64bit-asio.dll','libportaudio64bit.dll'):path.unlink()
        elif path.name.endswith('-asio.dll') and sha256(path)!=sha256(dll):raise ValueError('Unexpected bundled ASIO DLL')
    if not list((stage/'_internal').rglob('libportaudio64bit-asio.dll')):raise ValueError('ASIO runtime was not bundled')
    for name in ('README.md','BUILD.md','UPDATES.md','THIRD_PARTY.md','LICENSE','QUALITY.md'):
        shutil.copy2(ROOT/name,stage/name)
    (stage/'portable.flag').touch()
    packages=license_files(stage)
    (stage/'licenses/ASIO-SDK-LICENSE.txt').write_text(zipfile.ZipFile(native/'asio.zip').read('ASIOSDK/LICENSE.txt').decode('utf8'),encoding='utf8')
    shutil.copy2(next((native/('portaudio-'+pins['portaudio_commit'])).glob('LICENSE*')),stage/'licenses/PortAudio-LICENSE.txt')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    files={p.relative_to(stage).as_posix():sha256(p) for p in stage.rglob('*') if p.is_file()}
    manifest={'schema':1,'product':'RMAA+','version':VERSION,'platform':'Windows-x64','source_commit':commit,
              'license':'GPL-3.0-or-later','python_version':sys.version,'packages':packages,'native_sources':pins,'asio_dll_sha256':sha256(dll),'files':files}
    (stage/'bundle.json').write_text(json.dumps(manifest,indent=2),encoding='utf8');validate_bundle(stage,VERSION)
    release=ROOT/'release';release.mkdir(exist_ok=True)
    prefix=f'RMAA-Plus-v{VERSION}'
    windows=release/(prefix+'-win-x64.zip')
    with zipfile.ZipFile(windows,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(stage.rglob('*')):
            if p.is_file():z.write(p,'RMAA+/'+p.relative_to(stage).as_posix())
    source=release/(prefix+'-source.zip')
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT,text=True).split('\0')
    with zipfile.ZipFile(source,'x',zipfile.ZIP_DEFLATED) as z:
        for name in filter(None,tracked):
            p=ROOT/name
            if p.is_file():z.write(p,prefix+'-source/'+name)
    native_source=release/(prefix+'-native-sources.zip')
    with zipfile.ZipFile(native_source,'x',zipfile.ZIP_DEFLATED) as z:
        for name in ('portaudio.zip','asio.zip'):z.write(native/name,name)
        z.write(ROOT/'native-deps.json','native-deps.json');z.write(ROOT/'LICENSE','LICENSE')
    metadata={k:v for k,v in manifest.items() if k!='files'}
    metadata.update(exe_sha256=sha256(stage/'RMAA+.exe'),archive_sha256=sha256(windows),authenticode='unsigned',
                    verification='Automated tests in GitHub Actions; hardware validation documented separately; original-RMAA noninferiority not established')
    (release/(prefix+'-metadata.json')).write_text(json.dumps(metadata,indent=2),encoding='utf8')
    (release/(prefix+'-SHA256SUMS.txt')).write_text('\n'.join(sha256(p)+'  '+p.name for p in sorted(release.iterdir()) if p.is_file())+'\n',encoding='ascii')
    for p in release.glob('*.zip'):
        with zipfile.ZipFile(p) as z:
            if z.testzip() is not None:raise ValueError('Archive CRC failed')
    print(json.dumps(metadata,indent=2))

if __name__=='__main__':main()
