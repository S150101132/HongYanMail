from PySide6.QtGui import QFont, QPalette, QColor

def apply_theme(app, dark=False):
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    bg, surface, ink, muted, line, accent = ('#111827','#1f2937','#f3f4f6','#b8c2d3','#445067','#2563eb') if dark else ('#f3f6fb','#ffffff','#172033','#526078','#dce3ef','#2563eb')
    hover='#29364b' if dark else '#edf3ff'
    pal=QPalette()
    for role,color in [(QPalette.ColorRole.Window,bg),(QPalette.ColorRole.Base,surface),(QPalette.ColorRole.AlternateBase,hover),(QPalette.ColorRole.Text,ink),(QPalette.ColorRole.WindowText,ink),(QPalette.ColorRole.PlaceholderText,muted),(QPalette.ColorRole.Mid,line),(QPalette.ColorRole.Button,surface),(QPalette.ColorRole.ButtonText,ink),(QPalette.ColorRole.Highlight,accent),(QPalette.ColorRole.HighlightedText,'#ffffff')]: pal.setColor(role,QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(f'''
        QWidget {{ color:{ink}; }}
        QMainWindow, QDialog {{ background:{bg}; }}
        QWidget#loginCard, QWidget#readingPane {{ background:{surface}; border:1px solid {line}; border-radius:12px; }}
        QLineEdit,QPlainTextEdit,QTextBrowser,QTreeWidget,QListView {{ background:{surface}; border:2px solid {line}; border-radius:7px; padding:7px; selection-background-color:{accent}; selection-color:white; }}
        QLineEdit:focus,QPlainTextEdit:focus,QPushButton:focus {{ border:2px solid {accent}; }}
        QLineEdit {{ min-height:24px; }}
        QPushButton {{ background:{surface}; border:2px solid {line}; border-radius:7px; padding:7px 13px; min-height:20px; }}
        QPushButton:hover {{ border-color:{accent}; background:{hover}; }}
        QPushButton:pressed {{ background:{line}; }}
        QPushButton:checked {{ background:{accent}; color:white; border-color:{accent}; }}
        QPushButton:disabled {{ color:{muted}; background:{bg}; }}
        QPushButton[primary="true"] {{ background:#2563eb; color:white; border-color:#2563eb; }}
        QPushButton[primary="true"]:hover {{ background:#1d4ed8; }}
        QPushButton[primary="true"]:disabled {{ background:{line}; color:{muted}; border-color:{line}; }}
        QLabel[muted="true"] {{ color:{muted}; }}
        QLabel[heading="true"] {{ font-size:21px; font-weight:600; }}
        QLabel[eyebrow="true"] {{ color:{muted}; font-size:11px; font-weight:600; }}
        QToolBar {{ background:{surface}; border:0; border-bottom:1px solid {line}; spacing:8px; padding:12px 18px; }}
        QToolButton {{ border:1px solid transparent; border-radius:6px; padding:8px 10px; }}
        QToolButton:hover,QToolButton:checked {{ background:{hover}; border-color:{line}; }}
        QToolButton:focus {{ border-color:{accent}; }}
        QMenu {{ background:{surface}; border:1px solid {line}; padding:6px; }}
        QMenu::item {{ padding:9px 22px; }}
        QMenu::item:selected {{ background:{accent}; color:white; }}
        QStatusBar {{ background:{surface}; border-top:1px solid {line}; color:{muted}; padding:5px 12px; }}
        QSplitter::handle {{ background:{bg}; width:7px; }}
        QTreeWidget {{ border:0; background:{bg}; padding:10px 4px; }}
        QTreeWidget::item {{ padding:12px 10px; border-radius:6px; }}
        QTreeWidget::item:selected {{ background:{accent}; color:white; }}
        QTreeWidget::item:hover:!selected {{ background:{hover}; }}
        QListView#mailList {{ border:1px solid {line}; padding:0; }}
        QScrollBar:vertical {{ background:{bg}; width:10px; margin:0; }}
        QScrollBar::handle:vertical {{ background:{line}; min-height:32px; border-radius:5px; }}
        QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{ height:0; }}
    ''')
