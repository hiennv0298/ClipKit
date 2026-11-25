# PyInstaller spec: two programs sharing one folder (dist\ClipKit).
#   clipkit.exe     command line, runs in a console
#   ClipKitApp.exe  opens the app window, no console window
# Build with: python -m PyInstaller --noconfirm --clean --distpath dist --workpath build scripts\clipkit.spec
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas, binaries, hidden = collect_data_files("clipkit"), [], ["clipkit.gui"]
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "av"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h
hidden += collect_submodules("yt_dlp")

a = Analysis([os.path.join(SPECPATH, "clipkit_entry.py")], binaries=binaries, datas=datas, hiddenimports=hidden)
pyz = PYZ(a.pure)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="clipkit", console=True)
app = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ClipKitApp", console=False)
coll = COLLECT(cli, app, a.binaries, a.datas, name="ClipKit")
