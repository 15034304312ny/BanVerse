"""生成 Qt 样式表。"""

from __future__ import annotations

from .tokens import DARK, LIGHT


def stylesheet(dark: bool = False, mobile: bool = False) -> str:
    c = DARK if dark else LIGHT
    mobile_rules = ""
    if mobile:
        mobile_rules = """
        * { font-size: 15px; }
        QPushButton { min-height: 44px; padding: 4px 10px; }
        QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
            min-height: 44px;
        }
        QGroupBox { margin-top: 10px; padding: 10px; }
        QGroupBox::title { left: 10px; }
        QTabBar::tab { min-height: 44px; padding: 4px 12px; }
        QDialogButtonBox QPushButton { min-width: 88px; }
        """
    return f"""
    * {{ font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 14px; }}
    QMainWindow, QWidget#root {{ background: {c['window']}; color: {c['text']}; }}
    QWidget#navBar {{ background: {c['nav']}; }}
    QWidget#sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
    QWidget#chatPage, QWidget#settingsPage {{ background: {c['surface_alt']}; }}
    QLabel {{ color: {c['text']}; }}
    QLabel#muted, QLabel[muted="true"] {{ color: {c['text_muted']}; }}
    QLabel#brand {{ color: white; font-size: 20px; font-weight: 700; }}
    QLabel#pageTitle {{ font-size: 18px; font-weight: 600; }}
    QPushButton {{
        min-height: 36px; padding: 4px 12px; border: 1px solid {c['border']};
        border-radius: 6px; background: {c['surface']}; color: {c['text']};
    }}
    QPushButton:hover {{ border-color: {c['primary']}; }}
    QPushButton:pressed {{ background: {c['surface_alt']}; }}
    QPushButton:focus {{ border: 2px solid {c['focus']}; }}
    QPushButton:disabled {{ color: {c['text_muted']}; background: {c['surface_alt']}; }}
    QPushButton#primaryButton {{
        background: {c['primary']}; color: white; border: none; font-weight: 600;
    }}
    QPushButton#primaryButton:hover {{ background: {c['primary_hover']}; }}
    QPushButton#primaryButton:pressed {{ background: {c['primary_pressed']}; }}
    QPushButton#navButton {{
        min-width: 52px; min-height: 52px; padding: 4px; border: none;
        border-radius: 8px; background: transparent; color: #D0D0D0;
    }}
    QPushButton#navButton:hover {{ background: {c['nav_hover']}; color: white; }}
    QPushButton#navButton:checked {{ background: {c['primary']}; color: white; }}
    QPushButton#newMessageButton {{
        background: {c['primary']}; color: white; border: none;
        border-radius: 16px; padding: 6px 14px; font-weight: 600;
    }}
    QPushButton#newMessageButton:hover {{ background: {c['primary_hover']}; }}
    QPushButton#newMessageButton:pressed {{ background: {c['primary_pressed']}; }}
    QPushButton#stickerButton {{
        font-family: "Segoe UI Emoji"; font-size: 24px; padding: 0;
        border-radius: 12px; background: {c['surface_alt']};
    }}
    QPushButton#stickerButton:hover {{
        background: {c['surface']}; border-color: {c['primary']};
    }}
    QLineEdit, QTextEdit, QComboBox {{
        border: 1px solid {c['border']}; border-radius: 6px;
        background: {c['surface']}; color: {c['text']}; padding: 8px;
        selection-background-color: {c['primary']};
    }}
    QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{ border: 2px solid {c['focus']}; }}
    QListWidget {{
        background: {c['sidebar']}; border: none; outline: none; color: {c['text']};
    }}
    QListWidget::item {{ min-height: 84px; padding: 0; border-bottom: 1px solid {c['border']}; }}
    QLabel#conversationName {{ font-size: 15px; font-weight: 600; }}
    QLabel#conversationPreview {{ font-size: 13px; line-height: 1.45; }}
    QLabel#characterName {{ font-size: 15px; font-weight: 600; }}
    QLabel#characterDescription {{ font-size: 13px; line-height: 1.4; }}
    QLabel#characterTag {{ color: {c['primary']}; font-size: 12px; }}
    QLabel#builtinBadge {{
        color: white; background: {c['primary']}; border-radius: 8px;
        padding: 1px 7px; font-size: 11px; font-weight: 600;
    }}
    QLabel#messageText {{ font-size: 14px; line-height: 1.55; }}
    QLabel#messageSticker {{ font-family: "Segoe UI Emoji"; font-size: 38px; }}
    QListWidget::item:hover {{ background: {c['surface_alt']}; }}
    QListWidget::item:selected {{ background: {c['surface']}; color: {c['text']}; }}
    QFrame#header {{ background: {c['surface']}; border-bottom: 1px solid {c['border']}; }}
    QFrame#composer {{ background: {c['surface']}; border-top: 1px solid {c['border']}; }}
    QFrame#assistantBubble {{
        background: {c['assistant_bubble']}; border: 1px solid {c['border']};
        border-radius: 8px;
    }}
    QFrame#userBubble {{ background: {c['user_bubble']}; border: none; border-radius: 8px; }}
    QFrame#errorBubble {{ background: {c['surface']}; border: 1px solid {c['danger']}; border-radius: 8px; }}
    QScrollArea {{ border: none; background: {c['surface_alt']}; }}
    QScrollArea > QWidget > QWidget {{ background: {c['surface_alt']}; }}
    QGroupBox {{
        background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px;
        margin-top: 12px; padding: 16px;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; font-weight: 600; }}
    {mobile_rules}
    """
