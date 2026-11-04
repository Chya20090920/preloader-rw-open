from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

W = 24.0


def _p(r: QRectF, x: float, y: float) -> QPointF:
    return QPointF(r.x() + r.width() * x / W, r.y() + r.height() * y / W)


def _pen(color: str, width: float) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def draw_icon(p: QPainter, name: str, rect: QRectF, color: str, width: float = 1.5) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(_pen(color, width))
    p.setBrush(Qt.BrushStyle.NoBrush)

    if name == "chip":
        body = QRectF(_p(rect, 6, 6), _p(rect, 18, 18))
        p.drawRoundedRect(body, 2.2, 2.2)
        for x in (9.5, 14.5):
            p.drawLine(_p(rect, x, 6), _p(rect, x, 3))
            p.drawLine(_p(rect, x, 18), _p(rect, x, 21))
        for y in (9.5, 14.5):
            p.drawLine(_p(rect, 6, y), _p(rect, 3, y))
            p.drawLine(_p(rect, 18, y), _p(rect, 21, y))

    elif name == "box":
        p.drawRoundedRect(QRectF(_p(rect, 4, 8), _p(rect, 20, 20)), 2, 2)
        p.drawLine(_p(rect, 4, 12), _p(rect, 20, 12))
        p.drawLine(_p(rect, 9, 8), _p(rect, 9, 4.5))
        p.drawLine(_p(rect, 9, 4.5), _p(rect, 15, 4.5))
        p.drawLine(_p(rect, 15, 4.5), _p(rect, 15, 8))

    elif name == "bolt":
        poly = QPolygonF([_p(rect, 13, 2), _p(rect, 6.5, 13), _p(rect, 11, 13),
                          _p(rect, 9.5, 22), _p(rect, 17.5, 10.5), _p(rect, 12.5, 10.5)])
        p.drawPolygon(poly)

    elif name == "terminal":
        p.drawRoundedRect(QRectF(_p(rect, 3, 5), _p(rect, 21, 19)), 2.4, 2.4)
        p.drawPolyline(QPolygonF([_p(rect, 7, 9.5), _p(rect, 10.5, 12), _p(rect, 7, 14.5)]))
        p.drawLine(_p(rect, 12.5, 14.5), _p(rect, 17, 14.5))

    elif name == "clock":
        p.drawEllipse(QRectF(_p(rect, 3.5, 3.5), _p(rect, 20.5, 20.5)))
        p.drawLine(_p(rect, 12, 12), _p(rect, 12, 7))
        p.drawLine(_p(rect, 12, 12), _p(rect, 16, 13.5))

    elif name == "check":
        p.drawPolyline(QPolygonF([_p(rect, 4, 12.5), _p(rect, 10, 18), _p(rect, 20, 5.5)]))

    elif name == "alert":
        p.drawPolygon(QPolygonF([_p(rect, 12, 3.5), _p(rect, 21, 20), _p(rect, 3, 20)]))
        p.drawLine(_p(rect, 12, 9), _p(rect, 12, 14.5))
        p.drawPoint(_p(rect, 12, 17.5))

    elif name == "search":
        p.drawEllipse(QRectF(_p(rect, 4, 4), _p(rect, 15.5, 15.5)))
        p.drawLine(_p(rect, 14.5, 14.5), _p(rect, 20, 20))

    elif name == "folder":
        path = QPainterPath(_p(rect, 3, 6))
        path.lineTo(_p(rect, 9, 6))
        path.lineTo(_p(rect, 11, 9))
        path.lineTo(_p(rect, 21, 9))
        path.lineTo(_p(rect, 21, 19))
        path.lineTo(_p(rect, 3, 19))
        path.closeSubpath()
        p.drawPath(path)

    p.restore()
