"""Deja las DLL de CUDA (cuBLAS y cuDNN) al alcance de CTranslate2 en Windows.

Las ruedas `nvidia-*-cu12` colocan los .dll dentro de site-packages, donde el cargador de
Windows no mira por defecto. Sin esto, CTranslate2 falla al abrir el modelo en GPU.

Hay que hacer las dos cosas: `add_dll_directory` cubre las cargas que piden busqueda por
directorios, y el PATH cubre a CTranslate2, que llama a LoadLibrary con el nombre pelado y
por tanto ignora los directorios anadidos.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def enable_cuda_dlls() -> list[Path]:
    """Registra los directorios de DLL de las ruedas NVIDIA. Devuelve los que existian."""
    if sys.platform != "win32":
        return []

    try:
        import nvidia
    except ImportError:
        return []

    # `nvidia` es un paquete de espacio de nombres: reparte sus subpaquetes por varias raices.
    added: list[Path] = []
    for root in nvidia.__path__:
        for path in sorted(Path(root).glob("*/bin")):
            if not path.is_dir():
                continue
            os.add_dll_directory(str(path))
            added.append(path)

    if added:
        prefix = os.pathsep.join(str(p) for p in added)
        os.environ["PATH"] = prefix + os.pathsep + os.environ.get("PATH", "")
    return added
