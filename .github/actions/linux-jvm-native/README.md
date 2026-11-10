# Job-local Linux native libraries

Opt in from offscreen Compose/Skiko JVM test jobs on Ubuntu 24.04 ARM64. No root,
sudo, host package installation or display server is required.
Native Apple/Android builds, publishing and browser workload execution remain separate.
The standard APT dependency closure can include Mesa/LLVM vendor libraries. These
are authenticated private job payloads; their inclusion is not a graphics-driver
installation on the host or proof that a display/GPU workload is supported.

SDK diagnostic `42784517731` measured missing GL/EGL and JDK AWT system imports.
The five package owners are verified by Ubuntu's official `noble/arm64` file lists:

| Package | Library |
| --- | --- |
| [libgl1](https://packages.ubuntu.com/noble/arm64/libgl1/filelist) | `libGL.so.1` |
| [libegl1](https://packages.ubuntu.com/noble/arm64/libegl1/filelist) | `libEGL.so.1` |
| [libxi6](https://packages.ubuntu.com/noble/arm64/libxi6/filelist) | `libXi.so.6` |
| [libxrender1](https://packages.ubuntu.com/noble/arm64/libxrender1/filelist) | `libXrender.so.1` |
| [libxtst6](https://packages.ubuntu.com/noble/arm64/libxtst6/filelist) | `libXtst.so.6` |

The action has a closed `runtime` input: `jvm` (the unchanged default above) or
`chromium`. It accepts no package names, library paths or commands from callers.
The Chromium profile uses the exact 21 runtime-library packages in
[Playwright 1.64.1's Ubuntu 24.04 ARM64 requirements](https://github.com/microsoft/playwright/blob/v1.64.1/packages/playwright-core/src/server/registry/nativeDeps.ts).
That architecture inherits the Ubuntu 24.04 x64 `chromium` list. Its `tools` list,
fonts, Xvfb and development tools are not selected. The fresh-child import check
covers all 26 known SONAMEs mapped to these packages by that pinned source.

Actual diagnostic `42797631092` on Linux ARM64 with Node 24.22.2 and
Playwright/core 1.64.1 failed its ordinary ten-second headless launch. Read-only
`ldd` of the exact registry-selected AArch64 headless-shell ELF identified these
seven missing runtime imports; their owners were independently checked against
official Noble ARM64 file lists and checksum-matched package payloads:

| Package | Measured missing library |
| --- | --- |
| `libxcomposite1` | `libXcomposite.so.1` |
| `libxdamage1` | `libXdamage.so.1` |
| `libxfixes3` | `libXfixes.so.3` |
| `libxrandr2` | `libXrandr.so.2` |
| `libatk1.0-0t64` | `libatk-1.0.so.0` |
| `libatspi2.0-0t64` | `libatspi.so.0` |
| `libgbm1` | `libgbm.so.1` |

The complete fixed Chromium list handles fleet differences; only packages missing
on the selected host are downloaded, along with their authenticated new closure.
It uses the same staging/security/size limits as the JVM profile below. No host
package or driver is installed. Its prerequisite receipt always says
`browserTestsPassed: false`: successful imports alone do not prove a browser can
launch, run pages or load application content.

The separate `diagnose-linux-chromium-startup.yml` workflow defaults
`prepare_chromium` to `false` to preserve the baseline measurement. Explicitly
enabling it stages this profile, then runs the same normal Playwright validation
and ten-second headless launch in a fresh Node process with the current job's
verified loader environment. That real launch must pass before consumers rely on
the profile. Browser fixtures, application boot and release checks stay required;
no host-validation bypass or test exclusion is provided.

An available runtime is reused. Otherwise the action creates a fresh private
directory under `RUNNER_TEMP`, snapshots installed-package metadata there, and
uses private APT state, indexes, cache, logs, configuration and temporary files.
`APT_CONFIG` is read before other configuration, so its empty private fragment
directory and main file prevent host APT hooks from running. Existing repository
and trust definitions remain read-only. Explicit source authentication/date-check
overrides are refused; unsigned, stale or unauthenticated indexes are not allowed.

APT simulates only the fixed missing packages and their new dependency closure.
Upgrades, removals and non-ARM64/all packages are refused. A second simulation
checks the exact pinned closure. **Every install-shaped command is download-only**,
also enforced in the private configuration. No package is installed and no host
dpkg database, keyring, repository or cache is modified. GNU timeout bounds child
process groups; no privilege is granted or requested.

Each downloaded archive must match the signed-index SHA-256 and size and the exact
package/version/architecture. At most 32 archives, 32 MiB each and 128 MiB combined
are accepted. `dpkg-deb --fsys-tarfile` exposes only the data archive; control and
maintainer scripts are never executed. Bounded tar validation selects only native
library files and basename-only SONAME links, strips privileged modes, and refuses
path escapes, special files, hardlinks, collisions, unresolved links or oversized
payloads. Each uncompressed tar member is limited to 192 MiB, each full tar stream
to 256 MiB, and all extracted libraries together to 256 MiB in the private prefix.
No other archive limit is increased. This stages the ordinary signed APT closure,
including vendor libraries that a particular test may never load, within explicit
resource bounds rather than maintaining a custom ELF dependency resolver.

The initial live gate `42791739978` failed at the former 32 MiB member bound.
Diagnostic gate `42793535520` identified the exact authenticated member:
`libllvm20` / `libLLVM.so.20.1`, 138,943,216 bytes. That single legitimate library
also exceeds the former 128 MiB aggregate extraction limit. The measured limits
are now 192 MiB per member and 256 MiB extracted total; compressed archives remain
32 MiB each / 128 MiB total / at most 32, and each uncompressed tar remains 256 MiB.
Hash, architecture, source trust, path, symlink, script and child-process fences
remain in force. The earlier failed runs remain failures.

A fresh isolated Python child uses the exact loader path to load the selected
profile's fixed imports (eight Skiko and three additional AWT imports by default).
Only after that passes
is the confined loader prefix added to the **current job's** `GITHUB_ENV`, preserving
its existing `LD_LIBRARY_PATH`. The payload is retained for that job and its normal
runner-temp cleanup; failed preparation removes only its own fresh directory.
No path, environment value, raw loader error or repository URL is logged.

This establishes native prerequisite availability, **not** a JVM or coverage pass.
Keep the existing read-only JDK probe and full JVM/coverage command after this
action. An isolated `ldd` report of JVM-preloaded `libjvm.so` is not a system package
to download. No SDK version, compiler flag or test exclusion is needed.

Pure synthetic tests (no APT, native import or host changes):

```bash
python3 -B -m unittest discover -s .github/actions/linux-jvm-native -p 'test_*.py'
```

The self-test workflow defaults to these fixtures only. Its explicit
`workflow_dispatch` input `live_prepare=true` additionally checks the actual job-local
action on a Linux ARM64 runner without changing the host.

Primary contracts: [APT configuration order](https://manpages.ubuntu.com/manpages/noble/en/man5/apt.conf.5.html),
[APT download-only and simulation](https://manpages.ubuntu.com/manpages/noble/en/man8/apt-get.8.html),
[signed archive indexes](https://manpages.ubuntu.com/manpages/noble/en/man8/apt-secure.8.html),
[dpkg data-only streaming](https://manpages.ubuntu.com/manpages/noble/en/man1/dpkg-deb.1.html).
