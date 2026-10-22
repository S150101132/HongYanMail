"""Render the code-native Hongyan mark for the Windows executable."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from PySide6.QtWidgets import QApplication
import struct
from PySide6.QtCore import QBuffer,QIODevice
from PySide6.QtGui import QImage
from coremail_desktop.ui.branding import ICON_SIZES,draw_mark

def entry(size):
    """256 as PNG; smaller sizes as classic 32-bit DIBs, which every Explorer path reads
    (the desktop and title bars show a blank page for PNG-compressed small entries)."""
    pixmap=draw_mark(size)
    if size>=256:
        buffer=QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not pixmap.save(buffer,'PNG'): raise RuntimeError('Could not render brand icon')
        return bytes(buffer.data())
    image=pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    rows=[bytes(image.constScanLine(y))[:size*4] for y in range(size)]  # BGRA, top-down.
    mask=b'\0'*(((size+31)//32)*4*size)  # Alpha decides; the AND mask stays clear.
    head=struct.pack('<IiiHHIIiiII',40,size,size*2,1,32,0,size*size*4+len(mask),0,0,0,0)
    return head+b''.join(reversed(rows))+mask

def main():
    app=QApplication([])
    path=Path(__file__).resolve().parent.parent/'packaging/hongyan.ico'
    # Every size drawn on its own, so Explorer and the taskbar never scale.
    images=[entry(size) for size in ICON_SIZES]; offset=6+16*len(images); head=struct.pack('<HHH',0,1,len(images))
    for size,data in zip(ICON_SIZES,images):
        head+=struct.pack('<BBBBHHII',size%256,size%256,0,0,1,32,len(data),offset); offset+=len(data)
    path.write_bytes(head+b''.join(images))
    print('Hongyan icon rendered.')

if __name__=='__main__':main()
