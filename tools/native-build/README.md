# Native build tools for Qlib and Nautilus

Pinned sources and hashes are in `assets.lock.json`. `prepare.py` downloads and verifies MSVC and the Windows SDK, caching them in `.cache/` in this folder. The cache can be deleted at any time and is prepared again when needed; nothing depends on earlier output.

Run from the repository root:

```powershell
& .\.venv\Scripts\python.exe -B tools/native-build/prepare.py
```

Add `--check` to only check the prepared tools without downloading. The Qlib upstream build entry point `modules/quant/runtime/build_qlib_upstream.py` and the Nautilus build entry point `modules/quant/runtime/build/nautilus.py` both run the preparation step automatically (see [modules/quant/runtime/README.md](../../modules/quant/runtime/README.md)). Everyday use relies on prebuilt wheels and needs no compilation.

The cache keeps the files shipped with each package. Those packages are used under their original licenses and are not relicensed as part of TalyxTerminal.
