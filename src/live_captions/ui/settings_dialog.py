"""Dialogo de ajustes: proveedor, modelo, API key, nombre del usuario y vocabulario."""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from live_captions.assistant.config import PRESETS, LLMConfig
from live_captions.assistant.llm import ChatClient, LLMError


class SettingsDialog(QDialog):
    _tested = Signal(bool, str)  # resultado de la prueba, desde el hilo de red
    _models_loaded = Signal(object, str)  # lista de modelos (o None) y mensaje

    def __init__(self, config: LLMConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Asistente")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._initial_provider = config.provider

        self._provider = QComboBox()
        for key, preset in PRESETS.items():
            self._provider.addItem(preset.label, key)
        self._provider.setCurrentIndex(max(0, self._provider.findData(config.provider)))
        self._provider.currentIndexChanged.connect(self._apply_preset)

        self._base_url = QLineEdit(config.base_url)
        self._model = QComboBox()
        self._model.setEditable(True)
        self._model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._model.setEditText(config.model)
        self._load_models = QPushButton("Cargar modelos")
        self._load_models.setObjectName("ghost")
        self._load_models.setToolTip("Pide al proveedor la lista de modelos disponibles")
        self._load_models.clicked.connect(self._fetch_models)
        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.addWidget(self._model, 1)
        model_row.addWidget(self._load_models)
        self._vision = QComboBox()
        self._vision.setEditable(True)
        self._vision.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._vision.setEditText(config.vision_model)
        self._vision.lineEdit().setPlaceholderText("Vacio = sin imagenes en el chat")
        self._key = QLineEdit(config.api_key)
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key.setPlaceholderText("Se guarda en el Administrador de credenciales de Windows")
        show = QPushButton("Mostrar")
        show.setObjectName("ghost")
        show.setCheckable(True)
        show.toggled.connect(
            lambda on: self._key.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self._key, 1)
        key_row.addWidget(show)
        self._name = QLineEdit(config.user_name)
        self._name.setPlaceholderText("Nombre completo y apodos, separados por comas")
        self._name.setToolTip(
            "Cualquiera de estas palabras en la transcripcion cuenta como que te nombran "
            "(sin distinguir mayusculas ni tildes)."
        )
        self._vocabulary = QLineEdit(config.vocabulary)
        self._vocabulary.setPlaceholderText("Postgres, Kubernetes, Acme Corp, ...")
        self._vocabulary.setToolTip(
            "Nombres, productos y siglas que se dicen en tus reuniones. Whisper los recibe "
            "como pista, igual que tu nombre, y los reconoce mejor."
        )

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.addRow("Proveedor", self._provider)
        form.addRow("URL base", self._base_url)
        form.addRow("Modelo", model_row)
        form.addRow("Modelo con vision", self._vision)
        form.addRow("API key", key_row)
        form.addRow("Tu nombre", self._name)
        form.addRow("Vocabulario", self._vocabulary)

        hint = QLabel(
            "Groq, Cerebras, NVIDIA y OpenRouter tienen niveles gratuitos; la key se crea en su "
            "web. «Local» apunta a un llama-server en tu PC y no necesita key."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        self._result = QLabel("")
        self._result.setObjectName("status")
        self._result.setWordWrap(True)
        self._test = QPushButton("Probar conexion")
        self._test.clicked.connect(self._run_test)
        self._tested.connect(self._show_test)
        self._models_loaded.connect(self._fill_models)
        ok = QPushButton("Guardar")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self._test)
        buttons.addWidget(self._result, 1)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addLayout(buttons)

    def _apply_preset(self) -> None:
        preset = PRESETS[self._provider.currentData()]
        self._base_url.setText(preset.base_url)
        self._model.clear()
        self._model.setEditText(preset.model)
        self._vision.clear()
        self._vision.setEditText(preset.vision_model)
        self._key.setText(LLMConfig(provider=self._provider.currentData()).api_key)
        self._key.setEnabled(preset.needs_key)

    def config(self) -> LLMConfig:
        return LLMConfig(
            provider=self._provider.currentData(),
            base_url=self._base_url.text().strip(),
            model=self._model.currentText().strip(),
            user_name=self._name.text().strip(),
            vision_model=self._vision.currentText().strip(),
            vocabulary=self._vocabulary.text().strip(),
        )

    def api_key(self) -> str:
        return self._key.text().strip()

    def _run_test(self) -> None:
        config = self.config()
        key = self.api_key()
        self._test.setEnabled(False)
        self._show_test(True, "Probando...")

        def run() -> None:
            client = ChatClient(config.base_url, key, config.model, timeout=20)
            try:
                reply = client.ping()
            except LLMError as exc:
                self._tested.emit(False, str(exc))
            except Exception as exc:
                self._tested.emit(False, f"Error: {exc}")
            else:
                self._tested.emit(True, f"Conectado. El modelo respondio: {reply[:40]!r}")
            finally:
                client.close()

        threading.Thread(target=run, daemon=True).start()

    def _fetch_models(self) -> None:
        config = self.config()
        key = self.api_key()
        self._load_models.setEnabled(False)
        self._show_test(True, "Consultando modelos...")

        def run() -> None:
            client = ChatClient(config.base_url, key, config.model, timeout=20)
            try:
                models = client.list_models()
            except LLMError as exc:
                self._models_loaded.emit(None, str(exc))
            except Exception as exc:
                self._models_loaded.emit(None, f"Error: {exc}")
            else:
                self._models_loaded.emit(models, f"{len(models)} modelos disponibles")
            finally:
                client.close()

        threading.Thread(target=run, daemon=True).start()

    def _fill_models(self, models: object, message: str) -> None:
        self._load_models.setEnabled(True)
        self._show_test(models is not None, message)
        if not isinstance(models, list):
            return
        for combo in (self._model, self._vision):
            current = combo.currentText().strip()
            combo.clear()
            combo.addItems(models)
            if current in models:
                combo.setCurrentIndex(models.index(current))
            else:
                combo.setEditText(current)
                if current and combo is self._model:
                    self._show_test(False, f"{message}; «{current}» ya no esta en la lista")
        # Se abre la lista sin esperar al clic en la flecha: es lo que se acaba de pedir.
        self._model.setFocus()
        self._model.showPopup()

    def _show_test(self, ok: bool, message: str) -> None:
        self._result.setText(message)
        self._result.setProperty("error", not ok)
        self._result.style().unpolish(self._result)
        self._result.style().polish(self._result)
        if message not in {"Probando...", "Consultando modelos..."}:
            self._test.setEnabled(True)
