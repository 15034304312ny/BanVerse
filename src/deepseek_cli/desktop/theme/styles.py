"""生成 BanVerse 的 Qt 样式表。"""

from __future__ import annotations

from .tokens import DARK, LIGHT


def stylesheet(dark: bool = False, mobile: bool = False) -> str:
    c = DARK if dark else LIGHT
    mobile_rules = ""
    if mobile:
        mobile_rules = """
        * { font-size: 15px; }
        QPushButton { min-height: 44px; padding: 3px 11px; }
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox,
        QDoubleSpinBox, QDateEdit, QTimeEdit { min-height: 44px; }
        QGroupBox { margin-top: 12px; padding: 12px; padding-top: 18px; }
        QGroupBox::title { left: 12px; }
        QTabBar::tab { min-height: 44px; padding: 4px 14px; }
        QDialogButtonBox QPushButton { min-width: 88px; }
        QLabel#pageTitle { font-size: 20px; }
        QFrame#assistantBubble, QFrame#userBubble, QFrame#errorBubble,
        QFrame#narrationBubble { border-radius: 14px; }
        """
    return f"""
    * {{
        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
        font-size: 14px;
    }}
    QMainWindow, QDialog, QWidget#root {{
        background: {c["window"]}; color: {c["text"]};
    }}
    QWidget#navBar {{ background: {c["nav"]}; }}
    QWidget#sidebar {{
        background: {c["sidebar"]};
        border-right: 1px solid {c["border"]};
    }}
    QWidget#chatPage, QWidget#settingsPage, QWidget#charactersPage {{
        background: {c["surface_alt"]};
    }}
    QLabel {{ color: {c["text"]}; }}
    QLabel#muted, QLabel[muted="true"] {{ color: {c["text_muted"]}; }}
    QLabel#brand {{ color: white; font-size: 20px; font-weight: 700; }}
    QLabel#pageTitle {{
        color: {c["text"]}; font-size: 21px; font-weight: 700;
    }}
    QLabel#pageSubtitle {{ color: {c["text_muted"]}; font-size: 12px; }}

    QPushButton {{
        min-height: 38px; padding: 3px 14px;
        border: 1px solid {c["border_strong"]}; border-radius: 10px;
        background: {c["surface"]}; color: {c["text"]}; font-weight: 500;
    }}
    QPushButton:hover {{
        border-color: {c["primary"]}; background: {c["surface_hover"]};
    }}
    QPushButton:pressed {{
        border-color: {c["primary_pressed"]}; background: {c["surface_selected"]};
    }}
    QPushButton:focus {{ border: 2px solid {c["focus"]}; }}
    QPushButton:disabled {{
        color: {c["text_subtle"]}; background: {c["surface_alt"]};
        border-color: {c["border"]};
    }}
    QPushButton#primaryButton {{
        background: {c["primary"]}; color: {c["primary_text"]};
        border: 1px solid {c["primary"]}; font-weight: 700;
    }}
    QPushButton#primaryButton:hover {{
        background: {c["primary_hover"]}; border-color: {c["primary_hover"]};
    }}
    QPushButton#primaryButton:pressed {{
        background: {c["primary_pressed"]}; border-color: {c["primary_pressed"]};
    }}
    QPushButton#dangerButton {{
        color: {c["danger"]}; background: {c["danger_soft"]};
        border-color: {c["danger"]};
    }}
    QPushButton#dangerButton:hover {{
        color: {c["primary_text"]}; background: {c["danger_hover"]};
        border-color: {c["danger_hover"]};
    }}
    QPushButton#quietButton, QPushButton#headerActionButton,
    QPushButton#messageActionButton {{
        background: transparent; border-color: transparent;
        color: {c["text_muted"]};
    }}
    QPushButton#quietButton:hover, QPushButton#headerActionButton:hover,
    QPushButton#messageActionButton:hover {{
        color: {c["accent_text"]}; background: {c["primary_soft"]};
        border-color: {c["primary_soft"]};
    }}
    QPushButton#composerToolButton {{
        color: {c["text_muted"]}; background: {c["surface_alt"]};
        border-color: {c["border"]};
    }}
    QPushButton#composerToolButton:hover {{
        color: {c["accent_text"]}; background: {c["primary_soft"]};
        border-color: {c["primary"]};
    }}
    QPushButton#navButton {{
        min-width: 52px; min-height: 52px; padding: 4px; border: none;
        border-radius: 12px; background: transparent; color: {c["nav_text"]};
        font-weight: 600;
    }}
    QPushButton#navButton:hover {{ background: {c["nav_hover"]}; color: white; }}
    QPushButton#navButton:checked {{
        background: {c["primary"]}; color: {c["primary_text"]};
    }}
    QPushButton#newMessageButton {{
        background: {c["primary"]}; color: {c["primary_text"]}; border: none;
        border-radius: 18px; padding: 6px 16px; font-weight: 700;
    }}
    QPushButton#newMessageButton:hover {{ background: {c["primary_hover"]}; }}
    QPushButton#newMessageButton:pressed {{ background: {c["primary_pressed"]}; }}
    QPushButton#stickerButton {{
        font-family: "Segoe UI Emoji"; font-size: 24px; padding: 0;
        border-radius: 12px; background: {c["surface_alt"]};
    }}
    QPushButton#stickerButton:hover {{
        background: {c["primary_soft"]}; border-color: {c["primary"]};
    }}

    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox,
    QDoubleSpinBox, QDateEdit, QTimeEdit {{
        border: 1px solid {c["border_strong"]}; border-radius: 10px;
        background: {c["surface"]}; color: {c["text"]}; padding: 8px 10px;
        selection-background-color: {c["primary"]};
        selection-color: {c["primary_text"]};
    }}
    QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QComboBox:hover,
    QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {c["primary"]}; }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{ border: 2px solid {c["focus"]}; }}
    QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled,
    QSpinBox:disabled, QDoubleSpinBox:disabled {{
        color: {c["text_subtle"]}; background: {c["surface_alt"]};
        border-color: {c["border"]};
    }}
    QLineEdit#searchInput {{
        background: {c["surface"]}; border-color: {c["border"]};
        padding-left: 13px;
    }}
    QTextEdit#messageEditor {{
        border-radius: 13px; padding: 10px 12px;
        background: {c["surface_alt"]};
    }}
    QComboBox {{ padding-right: 28px; }}
    QComboBox#modelSelector {{
        color: {c["accent_text"]}; background: {c["primary_soft"]};
        border-color: {c["primary_soft"]}; font-weight: 600;
    }}
    QComboBox#modelSelector:hover, QComboBox#modelSelector:focus {{
        border-color: {c["primary"]};
    }}
    QComboBox::drop-down {{
        width: 26px; border: none; border-left: 1px solid {c["border"]};
    }}
    QComboBox QAbstractItemView {{
        background: {c["surface"]}; color: {c["text"]};
        border: 1px solid {c["border_strong"]}; border-radius: 8px;
        padding: 4px; selection-background-color: {c["surface_selected"]};
        selection-color: {c["text"]}; outline: none;
    }}

    QListWidget {{
        background: {c["sidebar"]}; border: none; outline: none; color: {c["text"]};
    }}
    QListWidget::item {{
        min-height: 84px; padding: 0; margin: 3px 0;
        border: 1px solid transparent; border-radius: 11px;
    }}
    QListWidget::item:hover {{
        background: {c["surface_hover"]}; border-color: {c["border"]};
    }}
    QListWidget::item:selected {{
        background: {c["surface_selected"]}; border-color: {c["primary"]};
        color: {c["text"]};
    }}
    QLabel#conversationName {{ font-size: 15px; font-weight: 700; }}
    QLabel#conversationPreview {{ font-size: 13px; line-height: 1.45; }}
    QLabel#characterName {{ font-size: 16px; font-weight: 700; }}
    QLabel#characterDescription {{ font-size: 13px; line-height: 1.4; }}
    QLabel#characterTag {{
        color: {c["accent_text"]}; font-size: 12px; font-weight: 600;
    }}
    QLabel#builtinBadge {{
        color: {c["accent_text"]}; background: {c["primary_soft"]};
        border-radius: 9px; padding: 2px 8px; font-size: 11px; font-weight: 700;
    }}

    QLabel#messageText {{ font-size: 14px; line-height: 1.6; }}
    QLabel#messageSticker {{ font-family: "Segoe UI Emoji"; font-size: 38px; }}
    QLabel#messageImage, QLabel#attachmentPreview {{
        background: {c["surface_alt"]}; border: 1px solid {c["border"]};
        border-radius: 10px;
    }}
    QFrame#header, QWidget#header {{
        background: {c["surface"]}; border-bottom: 1px solid {c["border"]};
    }}
    QFrame#composer {{
        background: {c["surface"]}; border-top: 1px solid {c["border"]};
    }}
    QFrame#attachmentCard {{
        background: {c["surface_alt"]}; border: 1px solid {c["border"]};
        border-radius: 11px;
    }}
    QFrame#assistantBubble {{
        background: {c["assistant_bubble"]}; border: 1px solid {c["border"]};
        border-radius: 14px;
    }}
    QFrame#narrationBubble {{
        background: {c["narration_bubble"]}; border: 1px solid {c["border"]};
        border-radius: 14px;
    }}
    QFrame#userBubble {{
        background: {c["user_bubble"]}; border: 1px solid {c["primary_soft"]};
        border-radius: 14px;
    }}
    QFrame#errorBubble {{
        background: {c["danger_soft"]}; border: 1px solid {c["danger"]};
        border-radius: 14px;
    }}

    QScrollArea {{ border: none; background: {c["surface_alt"]}; }}
    QScrollArea > QWidget > QWidget {{ background: {c["surface_alt"]}; }}
    QGroupBox {{
        background: {c["surface"]}; border: 1px solid {c["border"]};
        border-radius: 13px; margin-top: 14px; padding: 18px;
        padding-top: 22px; font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; left: 14px; padding: 1px 7px;
        color: {c["text"]}; background: {c["surface"]}; font-weight: 700;
    }}
    QTabWidget::pane {{
        background: {c["surface"]}; border: 1px solid {c["border"]};
        border-radius: 11px; top: -1px;
    }}
    QTabBar::tab {{
        min-height: 38px; padding: 3px 16px; color: {c["text_muted"]};
        background: transparent; border: none;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:hover {{
        color: {c["accent_text"]}; background: {c["primary_soft"]};
    }}
    QTabBar::tab:selected {{
        color: {c["accent_text"]}; border-bottom-color: {c["primary"]};
        font-weight: 700;
    }}
    QMenu {{
        background: {c["surface"]}; color: {c["text"]};
        border: 1px solid {c["border_strong"]}; border-radius: 9px; padding: 5px;
    }}
    QMenu::item {{ padding: 8px 24px 8px 12px; border-radius: 6px; }}
    QMenu::item:selected {{
        background: {c["surface_selected"]}; color: {c["text"]};
    }}
    QToolTip {{
        color: {c["text"]}; background: {c["surface"]};
        border: 1px solid {c["border_strong"]}; border-radius: 6px; padding: 6px;
    }}
    QScrollBar:vertical {{ width: 10px; margin: 2px; background: transparent; }}
    QScrollBar::handle:vertical {{
        min-height: 34px; background: {c["scrollbar"]}; border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {c["scrollbar_hover"]}; }}
    QScrollBar:horizontal {{ height: 10px; margin: 2px; background: transparent; }}
    QScrollBar::handle:horizontal {{
        min-width: 34px; background: {c["scrollbar"]}; border-radius: 4px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {c["scrollbar_hover"]}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QProgressBar {{
        min-height: 10px; max-height: 10px; background: {c["surface_alt"]};
        border: none; border-radius: 5px; text-align: center;
    }}
    QProgressBar::chunk {{ background: {c["primary"]}; border-radius: 5px; }}
    QStatusBar {{
        background: {c["surface"]}; color: {c["text_muted"]};
        border-top: 1px solid {c["border"]};
    }}
    {mobile_rules}
    """
