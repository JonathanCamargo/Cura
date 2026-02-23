# Building & Running Cura 5.12.0 from Source — Windows 11

## Root Causes of Common Build Failures

Three problems arise when running from source on Windows. They all stem from mixing Conan-built and pip-installed packages:

1. **pyArcus "module not found"** — Python 3.8+ changed Windows DLL loading. `PATH` no longer works for resolving `.dll` dependencies of `.pyd` extensions. The `.pyd` files in `venv\Lib\site-packages\` can't find their companion `.dll` files (Arcus.dll, protobuf, etc.).
2. **QtNetwork/OpenSSL failure** — Conan builds OpenSSL as static `.lib` only. The `cura_venv`'s PyQt6 was linked against these, so `Qt6Network.dll` can't find OpenSSL at runtime.
3. **pyArcus "initialization routine failed"** — Pip's PyQt6 ships its own Qt DLLs. If PyQt6 loads first, its Qt DLLs are already in memory when pyArcus.pyd tries to initialize against Conan's (different) Qt build, causing an access violation. The fix is to import native extensions *before* PyQt6 in `cura_app.py`. (This isn't needed in the official exe because PyInstaller bundles everything from Conan — there's only one set of Qt DLLs.)

**The fix** (confirmed working via [Issue #20084](https://github.com/Ultimaker/Cura/issues/20084)):

1. Create your own venv with pip-installed PyQt6 (ships dynamic OpenSSL) — fixes problem 2
2. Register Conan's native extension directory as a trusted DLL search path — fixes problem 1
3. Patch `cura_app.py` to import native extensions before PyQt6 — fixes problem 3
4. Override environment variables so your dev venv takes priority over Conan's `cura_venv`

---

## Prerequisites

- **Windows 11**
- **Visual Studio 2022** — "Desktop development with C++" workload (for MSVC compiler)
- **Python 3.12.x** — from [python.org](https://www.python.org/downloads/), on PATH
- **Git for Windows**

## Step 1 — Open the Right Shell

Find and open **"x64 Native Tools Command Prompt for VS 2022"** from the Start Menu. This puts MSVC on PATH. **Use cmd.exe for everything below** (`.bat` files don't propagate env vars in PowerShell).

## Step 2 — Create Conan Build Tools Environment

```cmd
python -m virtualenv C:\cura-tools --python=312
C:\cura-tools\Scripts\activate.bat
pip install "conan>=2.7.0,<3" "cmake==3.29.6" sip PyQt6-sip ninja gitpython
```

- `cmake==3.29.6` — CMake 3.30+ broke `cmake_minimum_required` compat, kills nlopt/2.7.1 build
- `sip` + `PyQt6-sip` — Required for Conan to build pyArcus SIP bindings

## Step 3 — Configure Conan (One Time)

```cmd
conan config install https://github.com/ultimaker/conan-config.git
conan profile detect --force
```

## Step 4 — Run Conan Install

```cmd
cd /d Downloads\Cura-main\Cura-main
conan install . --build=missing --update
```

**Takes 30–60+ minutes** on first run. It builds ~50 packages and generates:

| Path | Contents |
|------|----------|
| `venv\Lib\site-packages\` | Compiled `.pyd` + `.dll` files (pyArcus, pySavitar, pynest2d, etc.) |
| `build\generators\cura_venv\` | Python venv with pip packages |
| `build\generators\virtual_python_env.bat` | Activation script (sets PYTHONPATH, PATH, env vars) |
| `build\generators\pip_requirements_core_basic.txt` | Pip requirements list |
| `build\generators\pip_requirements_core_hashes.txt` | Pip requirements list (hashed) |
| `cura\CuraVersion.py` | Generated version file |
| `CuraEngine.exe` | Slicing engine binary |

## Step 5 — Create Development Environment

Create a separate venv with pip-installed packages. **Set `CURA_DEV` once here** — all later steps reference this variable, so change it in one place if you use a different path:

```cmd
deactivate
set "CURA_DEV=C:\cura-dev"

python -m virtualenv %CURA_DEV% --python=312
%CURA_DEV%\Scripts\activate.bat
pip install -r build\generators\pip_requirements_core_basic.txt
pip install -r build\generators\pip_requirements_core_hashes.txt
```

This installs PyQt6, numpy, scipy, trimesh, etc. **from pip** — crucially, pip's PyQt6 bundles its own dynamically-linked OpenSSL, which fixes the QtNetwork issue.

> **Important:** If `pip install` reports packages as "already satisfied" from `cura_venv`, temporarily clear PYTHONPATH first: `set PYTHONPATH=` then re-run the pip commands.

## Step 6 — Make Native Extensions Discoverable

Conan compiled native extensions (`pyArcus.pyd`, `pySavitar.pyd`, `pynest2d.pyd`) and their companion `.dll` files into `venv\Lib\site-packages\`. However, since Python 3.8, Windows no longer uses `PATH` to resolve `.dll` dependencies of `.pyd` files — so simply activating an environment or adding a directory to `PATH` won't help. The `.pyd` files will fail with "DLL load failed" unless their `.dll` dependencies are on a [trusted DLL search path](https://docs.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-search-order).

The simplest fix is to add `venv\Lib\site-packages` to the `cura-dev` virtual environment so Python can find the `.pyd` files, and register it as a trusted DLL directory so Windows can find their companion `.dll` files.

Run this **from the Cura source directory** with `cura-dev` active:

```cmd
REM Make sure cura-dev is active (from Step 5) and you're in the Cura source dir
cd /d D:\Downloads\Cura-main\Cura-main
set NATIVE_DIR=%CD%\venv\Lib\site-packages
for /f "tokens=*" %d in ('python -c "import sysconfig; print(sysconfig.get_path('purelib'))"') do set SITE=%d

REM 1) Add as a site-packages directory so Python finds .pyd files
echo %NATIVE_DIR%> "%SITE%\cura_native.pth"

REM 2) Register as trusted DLL directory so Windows finds companion .dll files
echo import os; os.add_dll_directory(r"%NATIVE_DIR%")> "%SITE%\sitecustomize.py"

REM 3) Verify
python -c "import pyArcus; print('pyArcus OK')"
```

### Alternative: copy into Python's DLLs directory

If the above doesn't work (e.g. `os.add_dll_directory` issues on your Windows version), you can copy the files into Python's built-in `DLLs\` directory, which Windows always trusts. This is typically something like `C:\Python312\DLLs\` or `C:\Users\<you>\AppData\Local\Programs\Python\Python312\DLLs\`.

```cmd
REM Find your Python's DLLs directory
for /f "tokens=*" %d in ('python -c "import sys,os;print(os.path.join(sys.base_prefix,'DLLs'))"') do set PYDLLS=%d
echo Copying native extensions to: %PYDLLS%

REM Copy everything EXCEPT python312.dll (would conflict with your Python installation)
for %f in (venv\Lib\site-packages\*) do (
    if /I not "%~nxf"=="python312.dll" copy /Y "%f" "%PYDLLS%\"
)
```

> **Note:** This modifies your system Python installation. If you use that Python for other projects, the copied files could cause conflicts. Prefer the `.pth` + `add_dll_directory` approach above.

## Step 7 — Patch cura_app.py Import Order

Pip's PyQt6 and Conan's native extensions ship different builds of the same Qt DLLs. Whichever loads first claims the DLL names in memory. If PyQt6 loads first, pyArcus crashes with "DLL initialization routine failed".

The fix: import native extensions **before** any PyQt6 import. In `cura_app.py`, add these lines just above the `from PyQt6.QtNetwork ...` import (around line 27):

```python
# Load Conan-built native extensions before PyQt6 to avoid DLL conflicts on Windows.
# pyArcus (and others) were compiled against specific Qt/library versions; if PyQt6
# loads its own DLLs first, pyArcus initialization fails with an access violation.
try:
    import pyArcus
    import pySavitar
    import pynest2d
except ImportError:
    pass

from PyQt6.QtNetwork import QSslConfiguration, QSslSocket
```

> **Why isn't this in the official repo?** The distributed exe is built entirely from Conan — there's only one set of Qt DLLs, so there's no conflict. This patch is only needed when mixing pip PyQt6 with Conan native extensions in a dev setup.

## Step 8 — Launch Cura

The Conan-generated `virtual_python_env.bat` sets `PYTHONPATH` (for Uranium, plugins, etc.) but also sets `VIRTUAL_ENV` to `cura_venv` and puts its site-packages first. We need to override those so `cura-dev` takes priority:

```cmd
cd /d D:\Downloads\Cura-main\Cura-main

REM Set CURA_DEV to the same path used in Step 5
set "CURA_DEV=C:\cura-dev"

REM Activate Conan's env script (sets PYTHONPATH to Uranium, Cura source, plugins, etc.)
call build\generators\virtual_python_env.bat

REM Override to use cura-dev instead of cura_venv
set "VIRTUAL_ENV=%CURA_DEV%"
set "PYTHONPATH=%CURA_DEV%\Lib\site-packages;%PYTHONPATH%"
set "PATH=%CURA_DEV%\Scripts;%PATH%"

REM Run Cura
python cura_app.py
```

This gives you the best of both worlds:
- **PYTHONPATH** from the official activation script (knows all Conan cache paths for the Uranium `UM` module, etc.)
- **cura-dev's site-packages first** on PYTHONPATH so pip's PyQt6 (with dynamic OpenSSL) wins over Conan's
- **VIRTUAL_ENV** pointing to `cura-dev` so Python doesn't silently prioritize `cura_venv`
- **Native extensions** registered via `os.add_dll_directory` (from Step 6) so Windows finds their DLLs

---

## Quick Reference — After First Build

For subsequent sessions, only Steps 1 and 8 are needed:

```cmd
REM Open x64 Native Tools Command Prompt, then:
cd /d D:\Downloads\Cura-main\Cura-main
set "CURA_DEV=C:\cura-dev"
%CURA_DEV%\Scripts\activate.bat
call build\generators\virtual_python_env.bat
set "VIRTUAL_ENV=%CURA_DEV%"
set "PYTHONPATH=%CURA_DEV%\Lib\site-packages;%PYTHONPATH%"
set "PATH=%CURA_DEV%\Scripts;%PATH%"
python cura_app.py
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `nlopt/2.7.1: Package build failed` | CMake >= 3.30 | `pip install cmake==3.29.6` |
| `sip-build not recognized` | Missing SIP | `pip install sip PyQt6-sip` |
| `DLL load failed: pyArcus` (module not found) | `.dll` not on trusted DLL path | Re-run Step 6 |
| `DLL load failed: pyArcus` (initialization failed) | PyQt6 loaded conflicting Qt DLLs first | Apply Step 7 patch |
| `DLL load failed: QtNetwork` | Static OpenSSL in Conan's PyQt6 | Ensure `%CURA_DEV%` site-packages is first on PYTHONPATH (Step 8) |
| PyQt6 still loads from `cura_venv` | `VIRTUAL_ENV` points to `cura_venv` | Set `VIRTUAL_ENV=%CURA_DEV%` (Step 8) |
| `pip install` says "already satisfied" from `cura_venv` | PYTHONPATH includes `cura_venv` | Clear PYTHONPATH first: `set PYTHONPATH=` then re-run pip |
| `.bat` does nothing in PowerShell | `.bat` can't set PS env vars | Use cmd.exe as instructed |
| `ModuleNotFoundError: UM` | PYTHONPATH not set | Ensure `call virtual_python_env.bat` ran first |

---

## References

- [Running Cura from Source (Official Wiki)](https://github.com/Ultimaker/Cura/wiki/Running-Cura-from-Source)
- [Building Cura from Source (Official Wiki)](https://github.com/Ultimaker/Cura/wiki/Building-Cura-from-Source)
- [Issue #20084 — Missing virtual_python_env.ps1 (zebin-xlogic's confirmed fix)](https://github.com/Ultimaker/Cura/issues/20084)
- [Issue #19192 — pyArcus DLL load failure](https://github.com/Ultimaker/Cura/issues/19192)
- [Issue #19299 — pyArcus access violation](https://github.com/Ultimaker/Cura/issues/19299)
