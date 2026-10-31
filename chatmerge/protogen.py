"""Compile proto/stream_list.proto with the installed grpcio-tools so generated code
always matches the installed protobuf runtime."""
from __future__ import annotations

import importlib.resources
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
PROTO = ROOT / "proto" / "stream_list.proto"
GEN_DIR = Path(__file__).resolve().parent / "_gen"
STAMP = GEN_DIR / "versions.txt"


def _toolchain() -> str:
    """protobuf runtime + grpcio-tools versions. Generated code only works with a runtime at
    least as new as the generator, so any change here means regenerate."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return f"protobuf {version('protobuf')}, grpcio-tools {version('grpcio-tools')}"
    except PackageNotFoundError as exc:
        raise RuntimeError(f"Missing package {exc}. Run Setup.bat again.") from exc


def _needs_build() -> bool:
    if getattr(sys, "frozen", False):
        return False  # packaged .exe: generated at build time and bundled in chatmerge/_gen
    pb2 = GEN_DIR / "stream_list_pb2.py"
    grpc_pb2 = GEN_DIR / "stream_list_pb2_grpc.py"
    if not (pb2.exists() and grpc_pb2.exists() and STAMP.exists()):
        return True
    if STAMP.read_text(encoding="utf-8").strip() != _toolchain():
        return True  # packages were upgraded or downgraded since the last build
    return PROTO.stat().st_mtime > min(pb2.stat().st_mtime, grpc_pb2.stat().st_mtime)


def build() -> None:
    from grpc_tools import protoc

    if not PROTO.exists():
        raise FileNotFoundError(f'Proto file missing: "{PROTO}"')
    GEN_DIR.mkdir(exist_ok=True)
    well_known = str(importlib.resources.files("grpc_tools") / "_proto")
    args = [
        "grpc_tools.protoc",
        f"-I{PROTO.parent}",
        f"-I{well_known}",
        f"--python_out={GEN_DIR}",
        f"--grpc_python_out={GEN_DIR}",
        str(PROTO),
    ]
    code = protoc.main(args)
    if code != 0:
        raise RuntimeError(f"protoc failed with exit code {code} for \"{PROTO}\"")
    STAMP.write_text(_toolchain() + "\n", encoding="utf-8")


def load() -> tuple[ModuleType, ModuleType]:
    """Return (stream_list_pb2, stream_list_pb2_grpc), building them first if needed."""
    if _needs_build():
        build()
    if not (GEN_DIR / "stream_list_pb2.py").exists():
        raise FileNotFoundError(f'Generated YouTube code missing: "{GEN_DIR}". Rebuild the app.')
    if str(GEN_DIR) not in sys.path:
        sys.path.insert(0, str(GEN_DIR))  # generated grpc module imports pb2 by bare name
    import stream_list_pb2  # type: ignore[import-not-found]
    import stream_list_pb2_grpc  # type: ignore[import-not-found]

    return stream_list_pb2, stream_list_pb2_grpc
