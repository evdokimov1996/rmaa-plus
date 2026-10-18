# License and third-party components

RMAA+ source and the distributed application are GPL-3.0-or-later.
Copyright (c) 2026 RMAA+ contributors. See LICENSE for the complete terms.
Source, exact build commit, native sources, build instructions and checksums
are distributed on the same GitHub Releases page as the Windows binary.

RMAA+ is an independent project, not an official RightMark product. No original
RMAA program files, visual resources, private recordings or user profiles are
part of the distribution.

The bundle includes Python (PSF), Tcl/Tk (Tcl/Tk license), NumPy and SciPy
(BSD and bundled component licenses), Matplotlib (Matplotlib/PSF-style license),
python-sounddevice (MIT), python-soundfile (BSD), libsndfile (LGPL), CFFI (MIT),
Pillow and the other dependencies listed by exact version in bundle.json.
The actual third-party license notices are copied into licenses/ and retained
in package data. PyInstaller's bootloader exception permits redistribution
of the frozen application; its license notice is also included.

The public Windows x64 PortAudio binaries are rebuilt from the source revision
and archives pinned by native-deps.json. PortAudio's license is MIT. The ASIO
SDK GPLv3 option is selected; no proprietary ASIO agreement is used. The
original SDK archive and PortAudio source archive are published as native
sources with the release. PortAudio's pinned CMake scripts describe any SDK
compatibility patch used during compilation.

ASIO is a trademark of Steinberg Media Technologies GmbH. No Steinberg logo
is used, and no affiliation or endorsement is claimed. The open-source SDK
option is described at https://www.steinberg.net/developers/ .

For modified builds, retain these notices and fulfill the corresponding source
and license obligations of the components you redistribute. Standard dependency
source releases are available from their projects/PyPI under the versions in
bundle.json; libsndfile source is at https://github.com/libsndfile/libsndfile .
