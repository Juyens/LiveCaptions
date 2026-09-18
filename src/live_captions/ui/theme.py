"""Paleta y hoja de estilo, siguiendo el sistema Geist de Vercel (misma base que Transcriptor)."""

from __future__ import annotations

BACKGROUND = "#000000"
SURFACE = "#0a0a0a"
SURFACE_HOVER = "#141414"
BORDER = "#2e2e2e"
BORDER_STRONG = "#454545"
TEXT = "#ededed"
TEXT_DIM = "#a1a1a1"
TEXT_FAINT = "#6f6f6f"
SUCCESS = "#0cce6b"
ACCENT = "#52a8ff"
DANGER = "#ff6369"
DANGER_DIM = "#3a1416"

FONT = '"Geist", "Segoe UI Variable Text", "Segoe UI", sans-serif'
MONO = '"Geist Mono", "Cascadia Mono", Consolas, monospace'

QSS = f"""
QWidget {{
    background: {BACKGROUND};
    color: {TEXT};
    font-family: {FONT};
    font-size: 13px;
}}

/* Sin esto heredan el negro del QWidget y pintan un recuadro sobre las tarjetas. */
QLabel, QCheckBox, QProgressBar {{ background: transparent; }}

QLabel#title {{ font-size: 15px; font-weight: 600; letter-spacing: -0.2px; }}
QLabel#badge {{ font-family: {MONO}; font-size: 11px; color: {TEXT_DIM}; }}
QLabel#badge[gpu="true"] {{ color: {SUCCESS}; }}
QLabel#status {{ font-size: 12px; color: {TEXT_DIM}; }}
QLabel#status[error="true"] {{ color: {DANGER}; }}
QLabel#clock {{ font-family: {MONO}; font-size: 13px; color: {TEXT_DIM}; }}
QLabel#clock[live="true"] {{ color: {TEXT}; }}
QLabel#paneTitle {{ font-size: 12px; font-weight: 600; color: {TEXT_DIM}; letter-spacing: 0.4px; }}
QLabel#meta {{ font-family: {MONO}; font-size: 11px; color: {TEXT_FAINT}; }}
QLabel#hint {{ color: {TEXT_FAINT}; font-size: 12px; }}
QLabel#cardTag {{ font-size: 11px; font-weight: 600; color: {SUCCESS}; letter-spacing: 0.3px; }}
QFrame#chatUser QLabel#cardTag {{ color: {ACCENT}; }}
QLabel#cardTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#cardDim {{ font-size: 12px; color: {TEXT_DIM}; }}
QLabel#answerEn {{ font-size: 14px; }}
QLabel#chatBody {{ font-size: 13px; }}

QFrame#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#paneHeader {{ border: none; border-bottom: 1px solid {BORDER}; border-radius: 0; }}
QFrame#suggestion, QFrame#chatAssistant, QFrame#chatUser {{
    background: {SURFACE_HOVER};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#chatUser {{ background: {SURFACE}; }}
QFrame#answer {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; }}
QFrame#preview {{ background: {SURFACE_HOVER}; border-top: 1px solid {BORDER}; border-radius: 0; }}
QLabel#thumb {{ border: 1px solid {BORDER}; border-radius: 4px; }}
QFrame#answer:hover {{ border-color: {BORDER_STRONG}; }}
QWidget#feed {{ background: transparent; }}
QScrollArea#feedScroll {{ background: transparent; border: none; }}
QScrollArea#feedScroll > QWidget > QWidget {{ background: transparent; }}

QLineEdit, QPlainTextEdit#context {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    selection-background-color: #333333;
}}
QLineEdit:focus, QPlainTextEdit#context:focus {{ border-color: {BORDER_STRONG}; }}
QPlainTextEdit#context {{
    border-left: none;
    border-right: none;
    border-top: none;
    border-radius: 0;
}}
QComboBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}}
QComboBox:hover {{ border-color: {BORDER_STRONG}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    selection-background-color: {SURFACE_HOVER};
    outline: none;
    padding: 4px;
}}
QDialog {{ background: {BACKGROUND}; }}

QPushButton {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 500;
}}
QPushButton:hover {{ background: {SURFACE_HOVER}; border-color: {BORDER_STRONG}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; }}
QPushButton#ghost {{ border-color: transparent; color: {TEXT_DIM}; padding: 4px 8px; }}
QPushButton#toggle {{
    border-color: transparent;
    color: {TEXT_FAINT};
    padding: 5px 10px;
    font-weight: 500;
}}
QPushButton#toggle:hover {{ background: {SURFACE_HOVER}; color: {TEXT_DIM}; }}
QPushButton#toggle:checked {{ background: {SURFACE_HOVER}; border-color: {BORDER}; color: {TEXT}; }}
QToolButton#more {{
    background: transparent;
    color: {TEXT_DIM};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 16px;
    font-weight: 700;
}}
QToolButton#more:hover {{ background: {SURFACE_HOVER}; color: {TEXT}; }}
QToolButton#more::menu-indicator {{ image: none; width: 0; }}
QMenu {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px;
}}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 4px; font-size: 12px; }}
QMenu::item:selected {{ background: {SURFACE_HOVER}; }}
QMenu::item:disabled {{ color: {TEXT_FAINT}; font-family: {MONO}; font-size: 11px; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 4px; }}
QMenu::indicator {{ width: 12px; height: 12px; margin-left: 6px; }}
QMenu::indicator:checked {{ background: {TEXT}; border-radius: 3px; }}
QMenu::indicator:unchecked {{ border: 1px solid {BORDER_STRONG}; border-radius: 3px; }}
QPushButton#ghost:hover {{ background: {SURFACE_HOVER}; color: {TEXT}; }}

QPushButton#record {{
    background: {TEXT};
    color: {BACKGROUND};
    border: 1px solid {TEXT};
    border-radius: 8px;
    padding: 9px 18px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#record:hover {{ background: #ffffff; }}
QPushButton#record:disabled {{
    background: {SURFACE};
    color: {TEXT_FAINT};
    border-color: {BORDER};
}}
QPushButton#record[live="true"] {{
    background: {DANGER_DIM};
    color: {DANGER};
    border: 1px solid {DANGER};
}}
QPushButton#record[live="true"]:hover {{ background: #4a181b; }}

QCheckBox {{ color: {TEXT_DIM}; font-size: 12px; spacing: 7px; }}
QCheckBox:hover {{ color: {TEXT}; }}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    background: {SURFACE};
}}
QCheckBox::indicator:checked {{ background: {TEXT}; border-color: {TEXT}; }}

QProgressBar#level {{
    background: {BORDER};
    border: none;
    border-radius: 2px;
    max-height: 4px;
    min-height: 4px;
}}
QProgressBar#level::chunk {{ background: {SUCCESS}; border-radius: 2px; }}

QTextEdit#pane {{
    background: transparent;
    border: none;
    padding: 6px 10px;
    font-size: 15px;
    selection-background-color: #333333;
}}

QSplitter::handle {{ background: transparent; width: 10px; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 7px;
}}
"""
