"""Hongyan's visible identity. Existing local storage names stay compatible."""
APP_NAME='鸿雁'
APP_ID='Inspur.Hongyan.Mail'
ICON_SIZES=(16,20,24,32,40,48,64,128,256)

def draw_mark(size):
    """鸿雁传书: a goose in flight above a letter, on a blue tile. Small sizes drop the
    goose and enlarge the letter so the mark stays crisp in the taskbar and tray."""
    from PySide6.QtCore import Qt,QRectF,QPointF
    from PySide6.QtGui import QPixmap,QPainter,QColor,QLinearGradient,QPainterPath,QPen
    pixmap=QPixmap(size,size); pixmap.fill(Qt.GlobalColor.transparent)
    p=QPainter(pixmap); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.scale(size/256,size/256)
    small=size<=24
    tile=QRectF(4,4,248,248) if small else QRectF(12,12,232,232)
    bg=QLinearGradient(tile.topLeft(),tile.bottomRight()); bg.setColorAt(0,QColor('#4c8dff')); bg.setColorAt(1,QColor('#1a46c9'))
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(bg); p.drawRoundedRect(tile,56,56)
    if not small:  # Soft top sheen.
        sheen=QLinearGradient(QPointF(0,12),QPointF(0,128)); sheen.setColorAt(0,QColor(255,255,255,46)); sheen.setColorAt(1,QColor(255,255,255,0))
        p.setBrush(sheen); p.drawRoundedRect(tile,56,56)
    env=QRectF(34,70,188,128) if small else QRectF(52,112,152,100)
    p.setBrush(QColor(0,20,80,50)); p.drawRoundedRect(env.translated(0,6),16,16)
    p.setBrush(QColor('#ffffff')); p.drawRoundedRect(env,16,16)
    flap=QPainterPath(); flap.moveTo(env.left()+14,env.top()+14); flap.lineTo(env.center().x(),env.top()+env.height()*0.56); flap.lineTo(env.right()-14,env.top()+14)
    p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(QColor('#2f6bf0'),22 if small else 11,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin)); p.drawPath(flap)
    if not small:
        # Two geese in formation (雁阵), the lead one larger.
        for path,width in ((((112,84),(138,48),(164,78),(192,40),(222,58)),12),(((48,62),(60,46),(72,58),(86,40),(102,46)),8)):
            goose=QPainterPath(); goose.moveTo(*path[0]); goose.quadTo(QPointF(*path[1]),QPointF(*path[2])); goose.quadTo(QPointF(*path[3]),QPointF(*path[4]))
            p.setPen(QPen(QColor(255,255,255,255 if width>10 else 190),width,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin)); p.drawPath(goose)
    p.end(); return pixmap

def app_icon():
    from PySide6.QtGui import QIcon
    icon=QIcon()
    for size in ICON_SIZES: icon.addPixmap(draw_mark(size))
    return icon

def set_app_id():
    """Own taskbar identity: one button with the 鸿雁 icon instead of python's."""
    import sys
    if sys.platform!='win32': return
    try:
        import ctypes; ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError,OSError):
        pass
