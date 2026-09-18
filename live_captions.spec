# -*- mode: python ; coding: utf-8 -*-
"""Empaquetado con PyInstaller. Se construye con `uv run python tools/build.py`."""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH)
SITE = Path(sys.prefix) / "Lib" / "site-packages"

# Prescindibles: cuDNN 9 carga los motores bajo demanda y, sin las precompiladas, tira de
# las que compila en caliente con nvrtc. Probado en Transcriptor con la RTX 4060: misma
# velocidad, 811 MB menos.
CUDA_SKIP = {
    "cudnn_engines_precompiled64_9.dll",  # 542 MB, kernels precompilados por arquitectura
    "cudnn_adv64_9.dll",  # 269 MB, operaciones de RNN que Whisper no usa
    "nvblas64_12.dll",  # BLAS de sustitucion, CTranslate2 no lo mira
}


def cuda_binaries() -> list[tuple[str, str]]:
    """DLL de CUDA de las ruedas nvidia-*, en la misma ruta relativa que en el venv."""
    found = []
    for dll in (SITE / "nvidia").glob("*/bin/*.dll"):
        if dll.name not in CUDA_SKIP:
            found.append((str(dll), str(dll.parent.relative_to(SITE))))
    if not found:
        raise SystemExit("No se encontraron las DLL de CUDA: ejecuta `uv sync` antes")
    return found


binaries = (
    cuda_binaries()
    + collect_dynamic_libs("ctranslate2")
    + collect_dynamic_libs("pyaudiowpatch")
    + collect_dynamic_libs("soxr")
    + collect_dynamic_libs("sentencepiece")
)
datas = collect_data_files("faster_whisper")  # incluye el modelo de VAD (silero)

a = Analysis(
    ["src/live_captions/__main__.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "ctranslate2",
        "onnxruntime",
        "pyaudiowpatch",
        "soxr",
        "sentencepiece",
        "keyring.backends.Windows",
        "win32ctypes.core",
    ],
    excludes=[
        "tkinter",
        "unittest",
        "pytest",
        "PySide6.QtWebEngineCore",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.Qt3DCore",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtWebEngineWidgets",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LiveCaptions",
    console=False,
    icon=str(ROOT / "tools" / "live_captions.ico"),
    version=str(ROOT / "tools" / "version.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,  # UPX sobre cublasLt (668 MB) tarda una eternidad y no aporta
    name="LiveCaptions",
)
