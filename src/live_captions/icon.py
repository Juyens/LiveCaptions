"""Icono de la aplicacion dibujado en codigo.

Se dibuja en vez de cargarse de un archivo para que la ventana, la barra de tareas y el .exe
salgan siempre del mismo sitio: `tools/make_ico.py` genera el .ico con esta misma funcion.

Motivo: dos lineas de subtitulo sobre fondo oscuro y un punto verde de "en directo".
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QImage, QPainter, QPixmap

BACKGROUND = "#0a0a0a"
FOREGROUND = "#ededed"
LIVE = "#0cce6b"

SIZES = (16, 24, 32, 48, 64, 128, 256)

# Anchos relativos de las dos lineas de subtitulo: la segunda mas corta, como un texto real.
LINES = (0.62, 0.42)


def render(size: int) -> QImage:
    """Dibuja el icono al tamano pedido."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)

    painter.setBrush(QBrush(QColor(BACKGROUND)))
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)

    # Lineas de subtitulo, alineadas a la izquierda y apoyadas en la mitad inferior.
    thickness = size * 0.13
    gap = size * 0.1
    left = size * 0.19
    top = size * 0.42
    painter.setBrush(QBrush(QColor(FOREGROUND)))
    for width_ratio in LINES:
        bar = QRectF(left, top, size * width_ratio, thickness)
        painter.drawRoundedRect(bar, thickness / 2, thickness / 2)
        top += thickness + gap

    # Punto de "en directo" arriba a la derecha; en los tamanos minusculos se agranda un
    # poco para que no desaparezca.
    radius = size * (0.09 if size >= 32 else 0.11)
    painter.setBrush(QBrush(QColor(LIVE)))
    painter.drawEllipse(QRectF(size * 0.81 - radius, size * 0.19 - radius, radius * 2, radius * 2))
    painter.end()
    return image


def app_icon() -> QIcon:
    """Icono con todas las resoluciones que pide Windows (barra de tareas, Alt+Tab, titulo)."""
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(QPixmap.fromImage(render(size)))
    return icon
