# PyInstaller spec for the Windows app folder (onedir). Built by build_exe.ps1.
# Run from the repo root:  pyinstaller --noconfirm --clean packaging\chatmerge.spec
# The generated YouTube code (chatmerge/_gen) must exist first: build_exe.ps1 makes it.
from pathlib import Path

ROOT = Path(SPECPATH).parent
GEN = ROOT / "chatmerge" / "_gen"
if not (GEN / "stream_list_pb2.py").exists():
    raise SystemExit(f'Missing "{GEN}". Run: python -c "from chatmerge import protogen; protogen.load()"')

a = Analysis(
    [str(ROOT / "chatmerge_app.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "chatmerge" / "web"), "chatmerge/web"),
        (str(GEN / "stream_list_pb2.py"), "chatmerge/_gen"),
        (str(GEN / "stream_list_pb2_grpc.py"), "chatmerge/_gen"),
    ],
    hiddenimports=[
        # used only by the generated YouTube code, which PyInstaller can't see
        "google.protobuf.duration_pb2",
        "google.protobuf.timestamp_pb2",
        "google.protobuf.internal.builder",
        "google.protobuf.runtime_version",
        "grpc.experimental",
    ],
    excludes=["grpc_tools", "tkinter", "unittest", "pydoc"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="chatmerge",
    console=True,  # the black window is the "it's running" signal and shows errors
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="chatmerge")
