"""Render the code-native Hongyan mark for the Windows executable."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from PySide6.QtWidgets import QApplication
from coremail_desktop.ui.branding import app_icon

def main():
    app=QApplication([])
    path=Path(__file__).resolve().parent.parent/'packaging/hongyan.ico'
    if not app_icon().pixmap(256,256).save(str(path),'ICO'): raise RuntimeError('Could not write brand icon')
    print('Hongyan icon rendered.')

if __name__=='__main__':main()
