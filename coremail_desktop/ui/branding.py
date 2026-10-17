"""Hongyan's visible identity. Existing local storage names stay compatible."""
APP_NAME='鸿雁'

def app_icon():
    from PySide6.QtCore import Qt,QRectF,QPointF
    from PySide6.QtGui import QIcon,QPixmap,QPainter,QColor,QPolygonF
    pixmap=QPixmap(256,256); pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor('#2563eb')); painter.drawRoundedRect(QRectF(8,8,240,240),54,54)
    painter.setBrush(QColor('#ffffff'))
    painter.drawPolygon(QPolygonF([QPointF(44,91),QPointF(121,115),QPointF(215,60),QPointF(165,196),QPointF(125,144),QPointF(78,172),QPointF(97,128)]))
    painter.setBrush(QColor('#bfdbfe')); painter.drawPolygon(QPolygonF([QPointF(125,144),QPointF(215,60),QPointF(144,157)]))
    painter.end(); return QIcon(pixmap)
