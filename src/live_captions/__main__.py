"""Arranque de la aplicacion de escritorio."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path


def transcripts_dir() -> Path:
    """Carpeta de sesiones: siempre en Documentos, aunque el .exe viva en otro sitio."""
    return Path.home() / "Documents" / "LiveCaptions"


def selftest(report: Path) -> int:
    """Carga los modelos, traduce una frase y localiza el loopback; deja el resultado en JSON.

    Sirve para comprobar el .exe empaquetado, que no tiene consola donde mirar: prueba de una
    vez que las DLL de CUDA, el VAD, CTranslate2, SentencePiece y PortAudio viajaron dentro.
    """
    import numpy as np

    result: dict[str, object] = {"ok": False, "frozen": getattr(sys, "frozen", False)}
    started = time.monotonic()
    try:
        from live_captions import vad
        from live_captions.audio import default_loopback
        from live_captions.pipeline import detect_backend
        from live_captions.transcriber import Transcriber
        from live_captions.translator import Translator

        device, compute_type = detect_backend()
        result["backend"] = f"{device}/{compute_type}"
        vad.warm_up()
        result["silence"] = Transcriber(device, compute_type).transcribe(
            np.zeros(16_000, dtype=np.float32)
        )
        result["translation"] = Translator(device).translate("Good morning everyone.")

        import pyaudiowpatch as pa

        audio = pa.PyAudio()
        try:
            result["loopback"] = default_loopback(audio).name
        finally:
            audio.terminate()
        import keyring

        result["keyring"] = type(keyring.get_keyring()).__name__

        # La ventana: pythonnet, WinForms y el runtime WebView2 (si falta, pywebview cae al
        # motor de Internet Explorer y la interfaz no funcionaria).
        from webview.platforms import winforms

        result["webview"] = winforms.renderer
        result["ok"] = (
            bool(result["translation"])
            and result["silence"] == ""
            and result["keyring"] == "WinVaultKeyring"
            and result["webview"] == "edgechromium"
        )
    except Exception:
        result["error"] = traceback.format_exc()
    result["seconds"] = round(time.monotonic() - started, 1)

    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


def prepare_headless() -> None:
    """Un .exe sin consola arranca con stdout/stderr a None y tqdm revienta al escribir.

    Las descargas de Hugging Face pintan barras de progreso aunque el modelo ya este en cache,
    asi que se apagan, y de paso se da un destino mudo a todo lo que quiera escribir.
    """
    # Se dejan abiertos a proposito: viven lo que el proceso.
    if sys.stdout is None:
        sys.stdout = Path(os.devnull).open("w")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = Path(os.devnull).open("w")  # noqa: SIM115
    from huggingface_hub.utils import disable_progress_bars

    disable_progress_bars()


def claim_taskbar_identity() -> None:
    """Sin un AppUserModelID propio, Windows agrupa la ventana bajo el icono del interprete."""
    if sys.platform != "win32":
        return
    import ctypes

    with contextlib.suppress(AttributeError, OSError):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Juyens.LiveCaptions")


def main() -> int:
    prepare_headless()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if sys.argv[1:2] == ["--selftest"]:
        return selftest(Path(sys.argv[2]))

    claim_taskbar_identity()

    try:
        from live_captions.app import App

        App(transcripts_dir()).run()
    except Exception:
        report_crash(traceback.format_exc())
        return 1
    return 0


def report_crash(details: str) -> None:
    """Un fallo al arrancar: se guarda entero en un log y se avisa con un mensaje legible.

    Sin esto, PyInstaller muestra su dialogo de "Unhandled exception" con una traza cortada,
    que en el PC de otra persona no hay forma de copiar entera.
    """
    folder = Path(os.environ.get("APPDATA") or Path.home()) / "LiveCaptions"
    log = folder / "error.log"
    with contextlib.suppress(OSError):
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with log.open("a", encoding="utf-8") as file:
            file.write(f"--- {stamp}\n{details}\n")
    hint = ""
    if "Python.Runtime" in details:
        hint = (
            "\n\nSuele pasar con un zip descargado: en Propiedades del zip marca "
            "«Desbloquear» y vuelve a extraerlo."
        )
    message = (
        "Live Captions no pudo abrir la ventana.\n\n"
        f"{details.strip().splitlines()[-1]}{hint}\n\nDetalles completos en:\n{log}"
    )
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Live Captions", 0x10)  # MB_ICONERROR


if __name__ == "__main__":
    sys.exit(main())
