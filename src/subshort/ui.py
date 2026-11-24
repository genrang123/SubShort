"""Shared studio palette and plain-text message boxes."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QLabel, QMessageBox

APP, SIDEBAR, SURFACE, ELEVATED = '#10131b', '#151a25', '#1b2230', '#252e40'
TEXT, MUTED, BORDER = '#f1f5f9', '#a9b7cb', '#39465b'
ACCENT, ACCENT_HOVER, FOCUS = '#4056ba', '#4c64cc', '#a4b5ff'
SUCCESS, WARNING, ERROR = '#86efac', '#fcd382', '#fca5a5'


def page_header(layout, title, subtitle=''):
    heading = QLabel(title, objectName='heading')
    layout.addWidget(heading)
    if subtitle:
        description = QLabel(subtitle, objectName='muted')
        description.setWordWrap(True)
        layout.addWidget(description)


def apply_theme(app):
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI', 10))
    palette = QPalette()
    for role, color in {
        'Window': APP, 'WindowText': TEXT, 'Base': SURFACE,
        'AlternateBase': '#202838', 'Text': TEXT, 'Button': ELEVATED,
        'ButtonText': TEXT, 'Highlight': ACCENT, 'HighlightedText': '#ffffff',
        'ToolTipBase': ELEVATED, 'ToolTipText': TEXT, 'PlaceholderText': MUTED,
    }.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#8793a8"))
    app.setPalette(palette)
    app.setStyleSheet(f'''
        QWidget#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
        QWidget#queueSummary, QWidget#emptyQueue, QWidget#jobDetails {{
            background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
        }}
        QLabel#brand {{ color: {FOCUS}; font-size: 21px; font-weight: 700; }}
        QLabel#heading {{ font-size: 24px; font-weight: 600; }}
        QLabel#section {{ font-size: 16px; font-weight: 600; }}
        QLabel#muted, QLabel#navSection {{ color: {MUTED}; }}
        QLabel#navSection {{ font-size: 11px; font-weight: 600; }}
        QPushButton {{ background: {ELEVATED}; border: 1px solid {BORDER};
            border-radius: 6px; padding: 5px 10px; min-height: 20px; }}
        QPushButton:hover {{ background: {BORDER}; }}
        QPushButton:pressed {{ background: {SURFACE}; }}
        QPushButton[variant="primary"] {{ background: {ACCENT}; color: white; border-color: {ACCENT}; }}
        QPushButton[variant="primary"]:hover {{ background: {ACCENT_HOVER}; }}
        QPushButton[variant="primary"]:pressed {{ background: #354691; }}
        QPushButton[variant="danger"] {{ color: {ERROR}; border-color: #82464d; }}
        QPushButton[variant="danger"]:hover {{ background: #533038; }}
        QPushButton[variant="nav"] {{ background: transparent; border-color: transparent; text-align: left; }}
        QPushButton[variant="nav"]:hover {{ background: {ELEVATED}; }}
        QPushButton[variant="nav"]:pressed {{ background: {BORDER}; }}
        QPushButton[variant="nav"]:checked {{ background: #263359; border-left: 3px solid {FOCUS}; }}
        QPushButton:disabled, QPushButton[variant]:disabled {{
            background: {SURFACE}; color: #8793a8; border-color: {BORDER};
        }}
        QPushButton:focus, QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTableView:focus {{
            border: 1px solid {FOCUS};
        }}
        QPushButton[variant]:focus {{ border-color: {FOCUS}; }}
        QFrame#voiceCard {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
        QPushButton#voiceTitle {{ text-align: left; font-weight: 600; }}
        QPushButton#voiceTitle:checked {{ background: {ACCENT}; color: white; }}
        QLabel#voiceBadge {{ color: {FOCUS}; font-weight: 600; }}
        QHeaderView::section {{ background: {ELEVATED}; border: none; padding: 7px 9px;
            color: {MUTED}; font-weight: 600; }}
        QTableView {{ border: 1px solid {BORDER}; border-radius: 8px; }}
        QTableView::item {{ padding: 0 8px; }}
        QLineEdit, QComboBox {{ min-height: 22px; padding: 4px 6px;
            border: 1px solid {BORDER}; border-radius: 5px; }}
        QPlainTextEdit {{ border: 1px solid {BORDER}; border-radius: 5px; }}
        QGroupBox {{ margin-top: 12px; padding: 10px; border: 1px solid {BORDER}; border-radius: 6px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
        QTabBar::tab {{ padding: 6px 10px; background: {ELEVATED}; }}
        QTabBar::tab:selected {{ border-bottom: 2px solid {FOCUS}; }}
        QProgressBar {{ border: none; background: {ELEVATED}; border-radius: 4px; min-height: 6px; }}
        QProgressBar::chunk {{ background: {FOCUS}; border-radius: 4px; }}
        QToolTip {{ background: {ELEVATED}; color: {TEXT}; border: 1px solid {BORDER}; }}
        QStatusBar {{ background: {SIDEBAR}; border-top: 1px solid {BORDER}; }}
        QStatusBar::item {{ border: none; }}
    ''')


def show_message(parent, title, text, icon=QMessageBox.Icon.Critical):
    box = QMessageBox(icon, title, "", QMessageBox.StandardButton.Ok, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(text)
    box.exec()


def ask_confirmation(parent, title, text):
    buttons = QMessageBox.StandardButton
    box = QMessageBox(QMessageBox.Icon.Question, title, "", buttons.Yes | buttons.No, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(text)
    box.setDefaultButton(buttons.No)
    return box.exec() == buttons.Yes
