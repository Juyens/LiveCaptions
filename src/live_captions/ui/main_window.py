"""Ventana principal: una barra (escuchar, estado, conmutadores, menu) y los paneles."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QImage,
    QTextBlockFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from live_captions.assistant import config as llm_config
from live_captions.assistant.brain import Assistant, ImageNote, Suggestion
from live_captions.pipeline import Pipeline
from live_captions.storage import format_clock
from live_captions.ui import theme
from live_captions.ui.assistant_pane import AssistantPane
from live_captions.ui.settings_dialog import SettingsDialog


def _repolish(widget: QWidget) -> None:
    """Fuerza a Qt a releer la hoja de estilo tras cambiar una propiedad dinamica."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class TranscriptPane(QFrame):
    """Tarjeta con titulo, boton Copiar y el texto; la ultima linea puede ser provisional."""

    def __init__(self, title: str, empty_hint: str) -> None:
        super().__init__()
        self.setObjectName("card")
        self._lines: list[str] = []
        self._partial: tuple[str, str] = ("", "")  # (confirmado, provisional)
        self._final_end = 0

        self._title = QLabel(title)
        self._title.setObjectName("paneTitle")
        self._count = QLabel("")
        self._count.setObjectName("meta")
        self._copy = QPushButton("Copiar")
        self._copy.setObjectName("ghost")
        self._copy.setEnabled(False)
        self._copy.clicked.connect(self.copy_to_clipboard)

        header = QFrame()
        header.setObjectName("paneHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 8, 8, 8)
        header_layout.addWidget(self._title)
        header_layout.addSpacing(8)
        header_layout.addWidget(self._count)
        header_layout.addStretch()
        header_layout.addWidget(self._copy)

        self._edit = QTextEdit()
        self._edit.setObjectName("pane")
        self._edit.setReadOnly(True)
        self._edit.setPlaceholderText(empty_hint)
        self._doc = self._edit.document()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self._edit, 1)

    # -- contenido ---------------------------------------------------------------------

    def append_line(self, seconds: float, text: str, tag: str = "") -> None:
        self._lines.append(f"{tag}: {text}" if tag else text)
        at_bottom = self._at_bottom()
        self._drop_partial()
        cursor = self._cursor_at_end(new_block=self._final_end > 0)
        stamp = (
            f'<span style="color:{theme.TEXT_FAINT}; font-family:{theme.MONO}; font-size:11px">'
            f"{format_clock(seconds)}</span>&nbsp;&nbsp;"
        )
        if tag:
            stamp += (
                f'<span style="color:{theme.ACCENT}; font-weight:600; font-size:12px">'
                f"{html.escape(tag)}</span>&nbsp; "
            )
        cursor.insertHtml(stamp + html.escape(text))
        self._final_end = cursor.position()
        self._render_partial()
        self._count.setText(str(len(self._lines)))
        self._copy.setEnabled(True)
        if at_bottom:
            self._scroll_to_bottom()

    def set_partial(self, committed: str, tentative: str = "") -> None:
        """Frase en curso: lo confirmado en gris claro, lo que aun puede cambiar en cursiva."""
        if (committed, tentative) == self._partial:
            return
        at_bottom = self._at_bottom()
        self._partial = (committed, tentative)
        self._drop_partial()
        self._render_partial()
        if at_bottom:
            self._scroll_to_bottom()

    def clear(self) -> None:
        self._lines.clear()
        self._partial = ("", "")
        self._final_end = 0
        self._edit.clear()
        self._count.setText("")
        self._copy.setEnabled(False)

    def plain_text(self) -> str:
        return "\n".join(self._lines)

    def copy_to_clipboard(self) -> None:
        QApplication.clipboard().setText(self.plain_text())
        self._copy.setText("Copiado")
        QTimer.singleShot(1200, lambda: self._copy.setText("Copiar"))

    # -- internos ----------------------------------------------------------------------

    def _cursor_at_end(self, *, new_block: bool) -> QTextCursor:
        cursor = QTextCursor(self._doc)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        block = QTextBlockFormat()
        block.setBottomMargin(7)
        if new_block:
            cursor.insertBlock(block)
        else:
            cursor.setBlockFormat(block)
        return cursor

    def _drop_partial(self) -> None:
        cursor = QTextCursor(self._doc)
        cursor.setPosition(self._final_end)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()

    def _render_partial(self) -> None:
        committed, tentative = self._partial
        if not committed and not tentative:
            return
        cursor = self._cursor_at_end(new_block=self._final_end > 0)
        parts = []
        if committed:
            parts.append(f'<span style="color:{theme.TEXT_DIM}">{html.escape(committed)}</span>')
        if tentative:
            parts.append(
                f'<span style="color:{theme.TEXT_FAINT}"><i>{html.escape(tentative)}</i></span>'
            )
        cursor.insertHtml(" ".join(parts))

    def _at_bottom(self) -> bool:
        bar = self._edit.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _scroll_to_bottom(self) -> None:
        bar = self._edit.verticalScrollBar()
        bar.setValue(bar.maximum())


class MainWindow(QWidget):
    def __init__(self, transcripts_dir: Path) -> None:
        super().__init__()
        self.setWindowTitle("Live Captions")
        self.setMinimumSize(720, 400)
        self._transcripts_dir = transcripts_dir
        self._settings = QSettings("Juyens", "LiveCaptions")
        self._closing = False
        self._elapsed = 0

        self._pipeline = Pipeline(transcripts_dir, self)
        self._pipeline.status.connect(self._set_status)
        self._pipeline.ready.connect(self._on_ready)
        self._pipeline.failed.connect(self._on_failed)
        self._pipeline.warning.connect(lambda m: self._set_status(m, error=True))
        self._pipeline.started.connect(self._on_started)
        self._pipeline.stopped.connect(self._on_stopped)
        self._pipeline.level.connect(self._on_level)
        self._pipeline.partial.connect(self._on_partial)
        self._pipeline.final.connect(self._on_final)
        self._pipeline.translated.connect(self._on_translated)
        self._pipeline.partial_translated.connect(self._on_partial_translated)
        self._translated_index = 0

        self._assistant = Assistant(self)
        self._assistant.suggestion.connect(self._on_suggestion)
        self._assistant.image_read.connect(self._on_image_read)
        self._assistant.chat_started.connect(self._on_chat_started)
        self._assistant.chat_delta.connect(lambda d: self._assistant_pane.chat_delta(d))
        self._assistant.chat_done.connect(self._on_chat_done)
        self._assistant.status.connect(lambda t: self._assistant_pane.set_status(t))
        self._assistant.error.connect(self._on_assistant_error)

        self._build()
        self._restore_settings()
        self._configure_assistant(llm_config.load(self._settings))
        self._pipeline.load_models()

    # -- construccion ------------------------------------------------------------------

    @staticmethod
    def _toggle_button(text: str, tooltip: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("toggle")
        button.setCheckable(True)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _build(self) -> None:
        self._record = QPushButton("Escuchar")
        self._record.setObjectName("record")
        self._record.setEnabled(False)
        self._record.setCursor(Qt.CursorShape.PointingHandCursor)
        self._record.clicked.connect(self._toggle)
        self._status = QLabel("Cargando modelos...")
        self._status.setObjectName("status")
        self._level = QProgressBar()
        self._level.setObjectName("level")
        self._level.setRange(0, 100)
        self._level.setTextVisible(False)
        self._level.setFixedWidth(70)
        self._clock = QLabel("00:00:00")
        self._clock.setObjectName("clock")

        self._show_spanish = self._toggle_button("Español", "Mostrar u ocultar la traduccion")
        self._show_spanish.toggled.connect(lambda on: self._spanish.setVisible(on))
        self._show_assistant = self._toggle_button("Asistente", "Mostrar u ocultar el asistente")
        self._show_assistant.toggled.connect(lambda on: self._assistant_pane.setVisible(on))
        self._mic = self._toggle_button("Mic", "Transcribir tambien lo que dices tu (Tú:)")
        self._mic.toggled.connect(self._pipeline.set_mic_enabled)

        self._on_top = QAction("Siempre encima", self)
        self._on_top.setCheckable(True)
        self._on_top.toggled.connect(self._toggle_on_top)
        self._backend_action = QAction("cargando modelos...", self)
        self._backend_action.setEnabled(False)
        menu = QMenu(self)
        menu.addAction("Limpiar transcripcion", self._clear)
        menu.addAction("Abrir carpeta de sesiones", self._open_folder)
        menu.addSeparator()
        menu.addAction(self._on_top)
        menu.addAction("Ajustes del asistente...", self._open_settings)
        menu.addSeparator()
        menu.addAction(self._backend_action)
        more = QToolButton()
        more.setObjectName("more")
        more.setText("⋯")
        more.setToolTip("Mas")
        more.setMenu(menu)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more.setCursor(Qt.CursorShape.PointingHandCursor)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        bar.addWidget(self._record)
        bar.addSpacing(4)
        bar.addWidget(self._status, 1)
        bar.addWidget(self._level)
        bar.addWidget(self._clock)
        bar.addSpacing(10)
        bar.addWidget(self._show_spanish)
        bar.addWidget(self._show_assistant)
        bar.addWidget(self._mic)
        bar.addWidget(more)

        self._english = TranscriptPane("ENGLISH", "Lo que se oiga en el escritorio aparecera aqui.")
        self._spanish = TranscriptPane(
            "ESPAÑOL", "Traduccion de cada frase, unos segundos despues."
        )
        self._assistant_pane = AssistantPane()
        self._assistant_pane.ask.connect(self._assistant.ask)
        self._assistant_pane.send_image.connect(self._on_send_image)
        self._assistant_pane.remove_image.connect(self._assistant.remove_image)
        self._assistant_pane.cleared.connect(self._assistant.clear_images)
        self._assistant_pane.context_changed.connect(self._assistant.set_context)
        self._assistant_pane.open_settings.connect(self._open_settings)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._english)
        self._splitter.addWidget(self._spanish)
        self._splitter.addWidget(self._assistant_pane)
        self._splitter.setChildrenCollapsible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(12)
        layout.addLayout(bar)
        layout.addWidget(self._splitter, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    def _restore_settings(self) -> None:
        geometry = self._settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1440, 720)
        splitter = self._settings.value("splitter")
        if splitter is not None:
            self._splitter.restoreState(splitter)
        self._on_top.setChecked(self._settings.value("on_top", False, type=bool))
        self._mic.setChecked(self._settings.value("mic", True, type=bool))
        self._pipeline.set_mic_enabled(self._mic.isChecked())
        self._show_spanish.setChecked(self._settings.value("spanish", True, type=bool))
        self._spanish.setVisible(self._show_spanish.isChecked())
        self._show_assistant.setChecked(self._settings.value("assistant", True, type=bool))
        self._assistant_pane.setVisible(self._show_assistant.isChecked())
        self._assistant_pane.set_context(str(self._settings.value("context", "")))

    def _save_settings(self) -> None:
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("splitter", self._splitter.saveState())
        self._settings.setValue("on_top", self._on_top.isChecked())
        self._settings.setValue("mic", self._mic.isChecked())
        self._settings.setValue("spanish", self._show_spanish.isChecked())
        self._settings.setValue("assistant", self._show_assistant.isChecked())
        self._settings.setValue("context", self._assistant_pane.context())

    # -- acciones ----------------------------------------------------------------------

    def _toggle(self) -> None:
        if self._pipeline.is_running:
            self._record.setEnabled(False)
            self._set_status("Cerrando la frase en curso...")
            self._pipeline.stop()
        else:
            self._record.setEnabled(False)
            self._assistant.reset()
            self._pipeline.start()

    def _clear(self) -> None:
        self._english.clear()
        self._spanish.clear()

    def _open_folder(self) -> None:
        self._transcripts_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._transcripts_dir)))

    def _toggle_on_top(self, checked: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
        self.show()

    def _tick(self) -> None:
        self._elapsed += 1
        self._clock.setText(format_clock(self._elapsed))

    def _open_settings(self) -> None:
        dialog = SettingsDialog(llm_config.load(self._settings), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        config = dialog.config()
        config.set_api_key(dialog.api_key())
        llm_config.save(self._settings, config)
        self._configure_assistant(config)

    def _configure_assistant(self, config: llm_config.LLMConfig) -> None:
        self._pipeline.set_hotwords(config.hotwords)
        self._assistant.configure(config)
        self._assistant.set_context(self._assistant_pane.context())
        if self._assistant.is_configured:
            vision = " · imagenes" if config.has_vision else ""
            self._assistant_pane.set_badge(f"{config.model} · {config.provider}{vision}")
            self._assistant_pane.set_status("")
        else:
            self._assistant_pane.set_badge("sin configurar")
            self._assistant_pane.set_status(
                "Pulsa Ajustes y pega la API key del proveedor para activar el asistente.",
                error=True,
            )

    # -- senales del pipeline ----------------------------------------------------------

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self._status.setText(text)
        self._status.setProperty("error", error)
        _repolish(self._status)

    def _on_ready(self, backend: str) -> None:
        gpu = backend.startswith("cuda")
        self._backend_action.setText(("● " if gpu else "○ ") + backend)
        self._record.setEnabled(True)
        self._set_status("Listo." if gpu else "Listo (sin GPU: ira lento).")

    def _on_failed(self, message: str) -> None:
        self._set_status(message, error=True)
        self._record.setEnabled(self._pipeline.is_ready and not self._pipeline.is_running)

    def _on_started(self, device: str, path: str) -> None:
        self._elapsed = 0
        self._translated_index = 0  # los indices de frase vuelven a empezar
        self._clock.setText("00:00:00")
        self._clock.setProperty("live", True)
        _repolish(self._clock)
        self._clock.setToolTip(path)
        self._timer.start()
        self._record.setText("Detener")
        self._record.setProperty("live", True)
        _repolish(self._record)
        self._record.setEnabled(True)
        self._set_status(f"Escuchando {device}")

    def _on_stopped(self) -> None:
        self._timer.stop()
        self._clock.setProperty("live", False)
        _repolish(self._clock)
        self._level.setValue(0)
        self._record.setText("Escuchar")
        self._record.setProperty("live", False)
        _repolish(self._record)
        self._record.setEnabled(True)
        self._set_status("Sesion guardada.")
        if self._closing:
            self.close()

    def _on_level(self, rms: float) -> None:
        self._level.setValue(min(100, int((rms**0.5) * 130)))

    def _on_partial(self, committed: str, tentative: str) -> None:
        self._english.set_partial(committed, tentative)

    def _on_final(self, _index: int, seconds: float, speaker: str, text: str) -> None:
        self._english.append_line(seconds, text, tag="Tú" if speaker == "me" else "")
        self._assistant.on_line(speaker, seconds, text)

    def _on_translated(self, index: int, seconds: float, text: str) -> None:
        self._translated_index = max(self._translated_index, index)
        self._spanish.set_partial("")
        self._spanish.append_line(seconds, text)

    def _on_partial_translated(self, index: int, text: str) -> None:
        # Puede llegar tarde, cuando su frase ya tiene traduccion final: entonces sobra.
        if index > self._translated_index:
            self._spanish.set_partial("", text)

    # -- senales del asistente ---------------------------------------------------------

    def _on_suggestion(self, suggestion: Suggestion) -> None:
        self._assistant_pane.add_suggestion(suggestion)
        answers = " | ".join(a["en"] for a in suggestion.answers)
        self._pipeline.log_assistant(f"Sugerencia para «{suggestion.question}»: {answers}")

    def _on_send_image(self, data_url: str, note: str, image: QImage) -> None:
        pending = self._assistant.attach_image(data_url, note)
        if pending is None:
            return
        self._assistant_pane.add_image(pending.id, image, note)
        self._pipeline.log_assistant(f"Imagen {pending.id} enviada: {note or '(sin nota)'}")

    def _on_image_read(self, image: ImageNote) -> None:
        self._assistant_pane.image_read(image)
        self._pipeline.log_assistant(f"Imagen {image.id} leida: {image.description}")

    def _on_chat_started(self, question: str) -> None:
        self._assistant_pane.chat_started(question)
        self._pipeline.log_assistant(f"Tú: {question}")

    def _on_chat_done(self, answer: str) -> None:
        self._assistant_pane.chat_done(answer)
        self._pipeline.log_assistant(f"Asistente: {answer}")

    def _on_assistant_error(self, message: str) -> None:
        self._assistant_pane.chat_failed()
        self._assistant_pane.set_status(message, error=True)

    # -- cierre ------------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (API de Qt)
        self._save_settings()
        if self._pipeline.is_running and not self._closing:
            self._closing = True
            self._set_status("Guardando la sesion...")
            self._pipeline.stop()
            event.ignore()
            return
        event.accept()
