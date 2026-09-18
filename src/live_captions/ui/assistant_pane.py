"""Panel del asistente: contexto, tarjetas de sugerencia, imagenes y chat."""

from __future__ import annotations

import base64
from collections.abc import Callable

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QKeyEvent, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from live_captions.assistant.brain import ImageNote, Suggestion
from live_captions.storage import format_clock

MAX_IMAGE_SIDE = 1280  # las capturas de pantalla se reducen antes de enviarse
THUMB_HEIGHT = 120


def _label(text: str, name: str = "", *, selectable: bool = True) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _copy_button(text_source: Callable[[], str]) -> QPushButton:
    button = QPushButton("Copiar")
    button.setObjectName("ghost")
    button.setCursor(Qt.CursorShape.PointingHandCursor)

    def copy() -> None:
        QApplication.clipboard().setText(text_source())
        button.setText("Copiado")
        QTimer.singleShot(1200, lambda: button.setText("Copiar"))

    button.clicked.connect(copy)
    return button


def image_to_data_url(image: QImage) -> str:
    """JPEG reducido en base64, listo para un bloque `image_url`."""
    if max(image.width(), image.height()) > MAX_IMAGE_SIDE:
        image = image.scaled(
            MAX_IMAGE_SIDE,
            MAX_IMAGE_SIDE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    storage = QByteArray()
    buffer = QBuffer(storage)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.convertToFormat(QImage.Format.Format_RGB32).save(buffer, "JPEG", 88)
    buffer.close()
    return "data:image/jpeg;base64," + base64.b64encode(bytes(storage)).decode("ascii")


def _thumbnail(image: QImage, height: int = THUMB_HEIGHT) -> QLabel:
    label = QLabel()
    label.setObjectName("thumb")
    label.setPixmap(
        QPixmap.fromImage(image).scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
    )
    return label


class ChatInput(QLineEdit):
    """Campo de chat que acepta pegar una imagen del portapapeles (capturas de pantalla)."""

    image_pasted = Signal(QImage)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (API de Qt)
        if event.matches(QKeySequence.StandardKey.Paste):
            image = QApplication.clipboard().image()
            if not image.isNull():
                self.image_pasted.emit(image)
                return
        super().keyPressEvent(event)


class SuggestionCard(QFrame):
    def __init__(self, suggestion: Suggestion) -> None:
        super().__init__()
        self.setObjectName("suggestion")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.addWidget(_label(format_clock(suggestion.seconds), "meta", selectable=False))
        head.addWidget(_label("Te preguntan", "cardTag", selectable=False))
        head.addStretch()
        layout.addLayout(head)
        layout.addWidget(_label(suggestion.question_es or suggestion.question, "cardTitle"))
        if suggestion.question_es:
            layout.addWidget(_label(suggestion.question, "cardDim"))

        for answer in suggestion.answers:
            row = QFrame()
            row.setObjectName("answer")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 6, 6, 6)
            row_layout.setSpacing(8)
            texts = QVBoxLayout()
            texts.setSpacing(2)
            texts.addWidget(_label(answer["en"], "answerEn"))
            if answer.get("es"):
                texts.addWidget(_label(answer["es"], "cardDim"))
            row_layout.addLayout(texts, 1)
            copy = _copy_button(lambda text=answer["en"]: text)
            row_layout.addWidget(copy, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(row)


class ChatCard(QFrame):
    def __init__(self, who: str, text: str = "") -> None:
        super().__init__()
        self.setObjectName("chatUser" if who == "user" else "chatAssistant")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(_label("Tú" if who == "user" else "Asistente", "cardTag", selectable=False))
        head.addStretch()
        if who != "user":
            head.addWidget(_copy_button(lambda: self._body.text()))
        layout.addLayout(head)
        self._body = _label(text, "chatBody")
        layout.addWidget(self._body)

    def append(self, delta: str) -> None:
        self._body.setText(self._body.text() + delta)

    def set_text(self, text: str) -> None:
        self._body.setText(text)


class ImageCard(QFrame):
    """Imagen enviada: miniatura, lo que dijo el usuario y, al llegar, lo que leyo el modelo."""

    removed = Signal(int)

    def __init__(self, image_id: int, image: QImage, note: str) -> None:
        super().__init__()
        self.setObjectName("chatUser")
        self.image_id = image_id
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(_label(f"Imagen {image_id}", "cardTag", selectable=False))
        head.addStretch()
        remove = QPushButton("Quitar del contexto")
        remove.setObjectName("ghost")
        remove.clicked.connect(lambda: self.removed.emit(self.image_id))
        head.addWidget(remove)
        layout.addLayout(head)
        layout.addWidget(_thumbnail(image), 0, Qt.AlignmentFlag.AlignLeft)
        if note:
            layout.addWidget(_label(note, "chatBody"))
        self._reading = _label("Leyendo la imagen...", "cardDim")
        layout.addWidget(self._reading)
        self._description = _label("", "cardDim")
        self._description.hide()
        layout.addWidget(self._description)

    def set_description(self, text: str) -> None:
        self._reading.hide()
        self._description.setText(text)
        self._description.show()

    def set_failed(self) -> None:
        self._reading.setText("No se pudo leer la imagen.")


class AssistantPane(QFrame):
    ask = Signal(str)
    send_image = Signal(str, str, QImage)  # data_url, nota del usuario, imagen (para la tarjeta)
    remove_image = Signal(int)
    context_changed = Signal(str)
    open_settings = Signal()
    cleared = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        self._streaming: ChatCard | None = None
        self._pending_image: QImage | None = None
        self._image_cards: dict[int, ImageCard] = {}

        title = QLabel("ASISTENTE")
        title.setObjectName("paneTitle")
        self._badge = QLabel("")
        self._badge.setObjectName("meta")
        settings = QPushButton("Ajustes")
        settings.setObjectName("ghost")
        settings.clicked.connect(self.open_settings)
        clear = QPushButton("Limpiar")
        clear.setObjectName("ghost")
        clear.setToolTip("Vacia las tarjetas y quita las imagenes del contexto")
        clear.clicked.connect(self.clear_feed)

        header = QFrame()
        header.setObjectName("paneHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 8, 8, 8)
        header_layout.addWidget(title)
        header_layout.addSpacing(8)
        header_layout.addWidget(self._badge)
        header_layout.addStretch()
        header_layout.addWidget(clear)
        header_layout.addWidget(settings)

        self._context = QPlainTextEdit()
        self._context.setObjectName("context")
        self._context.setPlaceholderText(
            "Contexto: quien eres, de que va la reunion, que quieres decir y que no..."
        )
        self._context.setFixedHeight(74)
        self._context.textChanged.connect(
            lambda: self.context_changed.emit(self._context.toPlainText())
        )

        self._feed_layout = QVBoxLayout()
        self._feed_layout.setContentsMargins(10, 10, 10, 10)
        self._feed_layout.setSpacing(8)
        self._feed_layout.addStretch()
        feed = QWidget()
        feed.setObjectName("feed")
        feed.setLayout(self._feed_layout)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("feedScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(feed)
        self._empty: QLabel | None = _label(
            "Cuando detecte que te preguntan algo, aqui apareceran respuestas sugeridas. "
            "Tambien puedes preguntarme lo que quieras sobre la reunion, o enviarme una imagen "
            "(boton Imagen o Ctrl+V con una captura) para que la tenga en cuenta.",
            "hint",
            selectable=False,
        )
        self._feed_layout.insertWidget(0, self._empty)

        self._status = QLabel("")
        self._status.setObjectName("status")
        self._status.setWordWrap(True)
        self._status.setContentsMargins(10, 0, 10, 4)

        # Vista previa de la imagen pendiente de enviar, encima del campo de texto.
        self._preview = QFrame()
        self._preview.setObjectName("preview")
        preview_layout = QHBoxLayout(self._preview)
        preview_layout.setContentsMargins(10, 6, 10, 6)
        preview_layout.setSpacing(10)
        self._preview_thumb = QLabel()
        preview_layout.addWidget(self._preview_thumb)
        preview_layout.addWidget(
            _label(
                "Imagen lista. Escribe de que trata y pulsa Enter.", "cardDim", selectable=False
            ),
            1,
        )
        discard = QPushButton("Descartar")
        discard.setObjectName("ghost")
        discard.clicked.connect(self._discard_image)
        preview_layout.addWidget(discard)
        self._preview.hide()

        self._input = ChatInput()
        self._input.setPlaceholderText("Pregunta al asistente... (Enter para enviar)")
        self._input.returnPressed.connect(self._send)
        self._input.image_pasted.connect(self.set_pending_image)
        attach = QPushButton("Imagen")
        attach.setToolTip("Adjunta una imagen (o pega una captura con Ctrl+V en el campo)")
        attach.clicked.connect(self._pick_image)
        send = QPushButton("Enviar")
        send.clicked.connect(self._send)
        input_row = QHBoxLayout()
        input_row.setContentsMargins(10, 0, 10, 10)
        input_row.setSpacing(8)
        input_row.addWidget(self._input, 1)
        input_row.addWidget(attach)
        input_row.addWidget(send)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self._context)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(self._status)
        layout.addWidget(self._preview)
        layout.addLayout(input_row)

    # -- estado ------------------------------------------------------------------------

    def set_badge(self, text: str) -> None:
        self._badge.setText(text)

    def set_context(self, text: str) -> None:
        self._context.setPlainText(text)

    def context(self) -> str:
        return self._context.toPlainText()

    def set_status(self, text: str, *, error: bool = False) -> None:
        self._status.setText(text)
        self._status.setProperty("error", error)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def clear_feed(self) -> None:
        while self._feed_layout.count() > 1:
            item = self._feed_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._streaming = None
        self._empty = None
        self._image_cards.clear()
        self.cleared.emit()

    # -- entradas del asistente --------------------------------------------------------

    def add_suggestion(self, suggestion: Suggestion) -> None:
        self._add_card(SuggestionCard(suggestion))

    def add_image(self, image_id: int, image: QImage, note: str) -> None:
        card = ImageCard(image_id, image, note)
        card.removed.connect(self._on_image_removed)
        self._image_cards[image_id] = card
        self._add_card(card)

    def image_read(self, image: ImageNote) -> None:
        card = self._image_cards.get(image.id)
        if card is not None:
            card.set_description(image.description)

    def chat_started(self, question: str) -> None:
        self._add_card(ChatCard("user", question))
        self._streaming = ChatCard("assistant")
        self._add_card(self._streaming)
        self._input.setEnabled(False)

    def chat_delta(self, delta: str) -> None:
        if self._streaming is not None:
            self._streaming.append(delta)
            self._scroll_to_bottom()

    def chat_done(self, answer: str) -> None:
        if self._streaming is not None:
            self._streaming.set_text(answer)
        self._streaming = None
        self._input.setEnabled(True)
        self._input.setFocus()

    def chat_failed(self) -> None:
        self._streaming = None
        self._input.setEnabled(True)
        for card in self._image_cards.values():
            if card._reading.isVisible():
                card.set_failed()

    # -- imagenes ----------------------------------------------------------------------

    def set_pending_image(self, image: QImage) -> None:
        self._pending_image = image
        self._preview_thumb.setPixmap(
            QPixmap.fromImage(image).scaledToHeight(48, Qt.TransformationMode.SmoothTransformation)
        )
        self._preview.show()
        self._input.setPlaceholderText("De que trata la imagen? (Enter para enviar)")
        self._input.setFocus()

    def _discard_image(self) -> None:
        self._pending_image = None
        self._preview.hide()
        self._input.setPlaceholderText("Pregunta al asistente... (Enter para enviar)")

    def _pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Elegir imagen", "", "Imagenes (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
        )
        if not path:
            return
        image = QImage(path)
        if image.isNull():
            self.set_status("No se pudo abrir esa imagen.", error=True)
            return
        self.set_pending_image(image)

    def _on_image_removed(self, image_id: int) -> None:
        card = self._image_cards.pop(image_id, None)
        if card is not None:
            card.deleteLater()
        self.remove_image.emit(image_id)

    # -- internos ----------------------------------------------------------------------

    def _send(self) -> None:
        text = self._input.text().strip()
        if self._pending_image is not None:
            image = self._pending_image
            self._discard_image()
            self._input.clear()
            self.send_image.emit(image_to_data_url(image), text, image)
            return
        if not text:
            return
        self._input.clear()
        self.ask.emit(text)

    def _add_card(self, card: QWidget) -> None:
        if self._empty is not None:
            self._empty.deleteLater()
            self._empty = None
        self._feed_layout.insertWidget(self._feed_layout.count() - 1, card)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
