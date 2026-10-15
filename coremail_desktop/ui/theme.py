from PySide6.QtGui import QFont, QPalette, QColor

def apply_theme(app, dark=False):
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    bg, surface, ink, muted, line, accent = ('#111827','#1f2937','#f3f4f6','#b8c2d3','#445067','#60a5fa') if dark else ('#f6f8fb','#ffffff','#172033','#526078','#d7deea','#2563eb')
    pal=QPalette()
    for role,color in [(QPalette.ColorRole.Window,bg),(QPalette.ColorRole.Base,surface),(QPalette.ColorRole.AlternateBase,bg),(QPalette.ColorRole.Text,ink),(QPalette.ColorRole.WindowText,ink),(QPalette.ColorRole.Button,surface),(QPalette.ColorRole.ButtonText,ink),(QPalette.ColorRole.Highlight,accent),(QPalette.ColorRole.HighlightedText,'#ffffff')]: pal.setColor(role,QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(f'''
        QWidget {{ color:{ink}; }}
        QMainWindow, QDialog {{ background:{bg}; }}
        QLineEdit,QPlainTextEdit,QTextBrowser,QTreeWidget,QListView {{ background:{surface}; border:1px solid {line}; border-radius:5px; padding:7px; selection-background-color:{accent}; }}
        QLineEdit:focus,QPlainTextEdit:focus,QPushButton:focus {{ border:2px solid {accent}; }}
        QPushButton {{ background:{surface}; border:1px solid {line}; border-radius:5px; padding:7px 13px; min-height:19px; }}
        QPushButton:hover {{ border-color:{accent}; }}
        QPushButton:disabled {{ color:{muted}; background:{bg}; }}
        QPushButton[primary="true"] {{ background:#2563eb; color:white; border-color:#2563eb; }}
        QLabel[muted="true"] {{ color:{muted}; }}
        QLabel[heading="true"] {{ font-size:21px; font-weight:600; }}
        QToolBar {{ border:0; border-bottom:1px solid {line}; spacing:10px; padding:10px; }}
        QStatusBar {{ border-top:1px solid {line}; color:{muted}; }}
        QSplitter::handle {{ background:{line}; width:1px; }}
        QTreeWidget::item {{ padding:9px 5px; }}
        QListView::item {{ padding:10px 7px; border-bottom:1px solid {line}; }}
        QListView::item:selected {{ background:{accent}; color:white; }}
    ''')
