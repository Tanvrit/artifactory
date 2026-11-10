# Linux JVM native prerequisites

Explicitly opt in from offscreen Compose/Skiko JVM test jobs on Ubuntu 24.04 ARM64.
This action does not prepare Android builds, Apple targets, publishing or browsers.
It does not start a display server or establish that JVM tests pass.

The five package owners were verified from Ubuntu's `noble/arm64` file lists:

| Package | Required library |
| --- | --- |
| [libgl1](https://packages.ubuntu.com/noble/arm64/libgl1/filelist) | `libGL.so.1` |
| [libegl1](https://packages.ubuntu.com/noble/arm64/libegl1/filelist) | `libEGL.so.1` |
| [libxi6](https://packages.ubuntu.com/noble/arm64/libxi6/filelist) | `libXi.so.6` |
| [libxrender1](https://packages.ubuntu.com/noble/arm64/libxrender1/filelist) | `libXrender.so.1` |
| [libxtst6](https://packages.ubuntu.com/noble/arm64/libxtst6/filelist) | `libXtst.so.6` |

SDK diagnostic `42784517731` measured these absent on an Ubuntu 24.04 AArch64
runner. The published Skiko 0.150.2 ELF requires GL/EGL even for raster tests;
the selected JDK 21 AWT library additionally requires Xi/Xrender/Xtst. Isolated
`ldd` can report JVM-preloaded `libjvm.so` unresolved; this is not a system package
to install, and the action does not treat it as one.

Installed packages are reused. A missing package requires root or already allowed
noninteractive `sudo -n`; no privilege is granted by this action. Apt uses existing
signed repositories, bounded lock/network waits, no recommendations, no upgrades
and no removals. A simulation rejects upgrade/removal plans and pins only its new
ARM64/all dependency closure before installation. The final pinned invocation is
simulated again and must match. Privileged GNU `timeout` encloses apt and dpkg
with TERM followed by KILL after ten seconds; the Python wait includes that cleanup
margin, including when `sudo` creates its own session. Every fixed package and
library is then verified. No repository, graphics driver, Xvfb, reboot or package upgrade command
is added. The unchanged full JVM test and coverage command remains the capability
gate after this action.

Offline tests (mocked commands/libraries; no host package changes):

```bash
python3 -B .github/actions/linux-jvm-native/test_prepare.py
```
