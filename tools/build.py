"""Construye el ejecutable, resume que ocupa cada cosa dentro y lo instala en DevTools.

uv run python tools/build.py              # construye y copia a ~/Documents/DevTools/LiveCaptions
uv run python tools/build.py --no-install # solo construye en dist/
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "LiveCaptions"
INSTALL = Path.home() / "Documents" / "DevTools" / "LiveCaptions"


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in {"B", "KB"} else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def build() -> int:
    if not (ROOT / "tools" / "live_captions.ico").exists():
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_ico.py")], check=True)

    for stale in (ROOT / "build", ROOT / "dist"):
        shutil.rmtree(stale, ignore_errors=True)

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "live_captions.spec"],
        cwd=ROOT,
        check=False,
    )
    if result.returncode != 0:
        return result.returncode

    exe = DIST / "LiveCaptions.exe"
    # Sin esto, una copia descargada (zip con Mark of the Web) no abre la ventana: ver el
    # comentario del propio archivo.
    shutil.copy2(ROOT / "tools" / "LiveCaptions.exe.config", DIST / "LiveCaptions.exe.config")
    print(f"\n{exe}")
    print(f"  ejecutable         {human(size_of(exe))}")
    print(f"  carpeta completa   {human(size_of(DIST))}")

    internal = DIST / "_internal"
    biggest = sorted(
        (p for p in internal.rglob("*") if p.is_file()),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )[:6]
    print("\n  lo que mas pesa:")
    for path in biggest:
        print(f"    {human(size_of(path)):>10}  {path.relative_to(internal)}")
    return 0


def install() -> None:
    """Sustituye la copia de DevTools por la recien construida."""
    if INSTALL.exists():
        shutil.rmtree(INSTALL)
    INSTALL.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DIST, INSTALL)
    print(f"\ninstalado en {INSTALL}")


def main() -> int:
    code = build()
    if code == 0 and "--no-install" not in sys.argv:
        install()
    return code


if __name__ == "__main__":
    sys.exit(main())
