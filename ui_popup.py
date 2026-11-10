"""Monitor-local popup placement, shared by the compact and Library views."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PopupRect:
    x: int
    y: int
    width: int
    height: int


def monitor_size(monitor):
    width, height = monitor['width'], monitor['height']
    if monitor.get('transform', 0) % 2:
        width, height = height, width
    scale = monitor.get('scale', 1)
    return round(width / scale), round(height / scale)


def valid_anchor(anchor, name, width, height):
    if not isinstance(anchor, dict) or anchor.get('screen') != name or anchor.get('edge') not in ('top', 'bottom', 'left', 'right'):
        return False
    values = [anchor.get(key) for key in ('x', 'y', 'width', 'height')]
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
        return False
    x, y, w, h = values
    return 0 < w <= width and 0 < h <= height and 0 <= x < width and 0 <= y < height and x + w <= width + 1 and y + h <= height + 1


def place_popup(monitor, width, height, anchor=None, gap=8, inset=12):
    """Respect reserved bars, center on the icon, and clamp to logical pixels."""
    mw, mh = monitor_size(monitor)
    reserved = monitor.get('reserved', [0, 0, 0, 0])
    margins = [max(inset, value + gap if value else inset) for value in reserved]
    left, top, right, bottom = margins[0], margins[1], mw - margins[2], mh - margins[3]
    # A transient broken/very small workarea must never produce negative sizes.
    right, bottom = max(left + 1, right), max(top + 1, bottom)
    width, height = min(width, right - left), min(height, bottom - top)
    x, y = right - width, top
    if valid_anchor(anchor, monitor.get('name'), mw, mh):
        ax, ay, aw, ah = (anchor[key] for key in ('x', 'y', 'width', 'height'))
        edge = anchor['edge']
        if edge in ('top', 'bottom'):
            x = ax + (aw - width) / 2
            if edge == 'top':
                y = max(top, ay + ah + gap)
                height = min(height, max(1, bottom - y))
            else:
                height = min(height, max(1, ay - gap - top))
                y = ay - gap - height
        else:
            y = ay + (ah - height) / 2
            if edge == 'left':
                x = max(left, ax + aw + gap)
                width = min(width, max(1, right - x))
            else:
                width = min(width, max(1, ax - gap - left))
                x = ax - gap - width
    x = min(max(left, x), right - width)
    y = min(max(top, y), bottom - height)
    return PopupRect(round(x), round(y), round(width), round(height))
