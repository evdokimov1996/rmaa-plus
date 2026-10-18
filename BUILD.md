# Windows x64 build

Python 3.13, Tk 8.6, dependencies pinned in requirements.txt.
Source entry point: rmaa_classic.py. Run it after installing the dependencies.
The public release builder also requires Visual Studio C++ Build Tools and CMake.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m unittest discover -v
.venv/Scripts/python tools/build_release.py
```

Outputs are in release/. Keep the complete RMAA+ folder from the ZIP.
The EXE alone is not portable.
The bundled PortAudio ASIO library is selected by SD_ENABLE_ASIO before importing sounddevice.
No original RMAA EXE/DLL/resource or user audio is needed for the build.
Windows desktop application: there is no Android package/APK for this project.
The Windows EXE has no Authenticode signing certificate. Previous versions are retained.

The builder rebuilds PortAudio with ASIO from pinned, SHA-256-verified native
archives. Its ASIO SDK selects the GPLv3 option. libsndfile 1.2.2 is also
rebuilt from pinned source, with external codecs disabled for WAV/RF64 use.
The native source archive includes all three libraries and original licenses. Original archives and the
exact build instructions are distributed with each release. The ZIP includes
per-file bundle.json hashes and third-party notices. No user data is packaged.

GitHub Actions runs the tests on Windows. A matching vX.Y.Z tag builds and
publishes the release after independently verifying each stored asset digest.
Use workflow_dispatch for a test build without publishing a stable release.
See THIRD_PARTY.md, UPDATES.md and RELEASE_NOTES.md.

Portable distribution: rename the bundled EXE to RMAA+.exe, keep _internal
and place an empty portable.flag beside the EXE. Bundle the entire folder
as RMAA+. Do not package data/preferences.json from a user installation.

PortAudio is pinned to the 19.7.0 ABI supported by sounddevice 0.5.6.
The builder compiles a native header probe and compares WASAPI and common
PortAudio struct sizes with the actual Python binding before packaging.
A larger incompatible WASAPI stream-info structure is rejected.
