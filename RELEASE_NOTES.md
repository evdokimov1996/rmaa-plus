# RMAA+ public release

- Open source under GPL-3.0-or-later; source and native build inputs published.
- Built-in offline Russian/English guide: F1 / Help → User guide.
- User-triggered GitHub update check, verified download, transactional install,
  startup self-test, rollback and preservation of user data.
- No background update polling or upload of audio/results.
- PortAudio / ASIO rebuilt from pinned public source and GPL-enabled SDK.
- Existing precision measurements, live setup monitor and 0.5 dB FR grid retained.

Windows x64 portable ZIP: extract the entire RMAA+ folder and run RMAA+.exe.
Keep _internal and portable.flag together. The executable is unsigned
Authenticode. Hardware validation and mathematical tests are documented
separately; universal noninferiority to original RMAA is not established.
# RMAA+ 6.4.5

Fix full-scale PCM24 corruption in MME: explicitly disable PortAudio conversion
dither on all measurement streams. The PortAudio integer down-conversion dither
can wrap near full scale when MME selects a 16-bit host format. The generator
remains at 0 dBFS sample peak; no attenuation or curve smoothing was added.

Show persistent frequency-response precision/clock warnings above results,
including realization spread and random error. Preserve warnings in PNG,
HTML and TXT exports. Record actual host API and conversion-dither state.
Clock validation limits remain unchanged; report six decimal places so a
slightly exceeded limit is visible instead of two identical rounded values.
