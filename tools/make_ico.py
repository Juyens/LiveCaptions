"""Escribe tools/live_captions.ico, el icono que PyInstaller incrusta en el .exe.

Qt solo sabe guardar un .ico de una resolucion, y Windows necesita varias (32 px en la barra
de tareas, 256 en el Explorador), asi que el contenedor se arma aqui a mano.

Los tamanos pequenos van como BMP y solo el de 256 como PNG. Windows admite PNG dentro de un
.ico unicamente en esa resolucion: con las demas comprimidas, el shell no sabe leerlas y la
barra de tareas cae al icono generico aunque el Explorador parezca correcto.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray
from PySide6.QtGui import QGuiApplication, QImage

from icon import SIZES, render

HEADER = struct.Struct("<HHH")  # reservado, tipo (1 = icono), numero de imagenes
ENTRY = struct.Struct("<BBBBHHII")  # ancho, alto, colores, reservado, planos, bits, bytes, offset
DIB_HEADER = struct.Struct("<IiiHHIIiiII")  # BITMAPINFOHEADER
PNG_FROM = 256  # a partir de aqui se guarda comprimido


def png_bytes(size: int) -> bytes:
    # El QByteArray tiene que sobrevivir al QBuffer: si se pasa como temporal, Qt escribe
    # sobre memoria ya liberada y el proceso se cae.
    storage = QByteArray()
    buffer = QBuffer(storage)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    render(size).save(buffer, "PNG")
    buffer.close()
    return bytes(storage)


def dib_bytes(size: int) -> bytes:
    """Imagen en el formato que espera un .ico clasico: cabecera DIB, pixeles y mascara."""
    image = render(size).convertToFormat(QImage.Format.Format_ARGB32)
    # ARGB32 en memoria y little-endian ya es BGRA, que es justo el orden que pide el DIB.
    raw = bytes(image.constBits())
    stride = image.bytesPerLine()
    # El alto se declara doble porque la imagen y su mascara van una detras de otra, y las
    # filas se guardan de abajo arriba.
    pixels = b"".join(raw[y * stride : (y + 1) * stride] for y in reversed(range(size)))

    header = DIB_HEADER.pack(DIB_HEADER.size, size, size * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0)
    # Mascara a ceros: la transparencia real la lleva el canal alfa de cada pixel.
    mask = bytes(((size + 31) // 32) * 4 * size)
    return header + pixels + mask


def build_ico(sizes: tuple[int, ...]) -> bytes:
    images = [png_bytes(s) if s >= PNG_FROM else dib_bytes(s) for s in sizes]
    offset = HEADER.size + ENTRY.size * len(images)

    directory = b""
    for size, data in zip(sizes, images, strict=True):
        # En el formato ICO, 256 se codifica como 0 porque el campo es de un solo byte.
        side = 0 if size >= 256 else size
        directory += ENTRY.pack(side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)

    return HEADER.pack(0, 1, len(images)) + directory + b"".join(images)


def main() -> None:
    QGuiApplication([])
    target = Path(__file__).parent / "live_captions.ico"
    target.write_bytes(build_ico(SIZES))
    print(f"escrito {target} ({target.stat().st_size} bytes, {len(SIZES)} resoluciones)")


if __name__ == "__main__":
    main()
