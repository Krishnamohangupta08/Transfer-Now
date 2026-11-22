"""Duotone icon engine + app logo.

Icons are 24-grid stroke paths — round caps/joins, generous corner radii — with a
soft *duotone* layer: the main silhouette of each icon is filled with a translucent
tint of the same colour under the crisp stroke (the look of Linear / Raycast /
Phosphor-duotone).  They render from inline SVG via QSvgRenderer, tinted per theme.
Pixmaps are cached per (name, color, size, stroke, duo, dpr); the cache flushes on
theme change.
"""
from __future__ import annotations

import re
from typing import Optional

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

from .theme import theme

# ------------------------------------------------------------- icon bodies
ICONS: dict[str, str] = {
    # navigation
    # bento dashboard: one tall + one short tile per column
    "grid": ('<rect width="7.5" height="9.5" x="3.5" y="3.5" rx="2.5"/>'
             '<rect width="7.5" height="5" x="13" y="3.5" rx="2.5"/>'
             '<rect width="7.5" height="9.5" x="13" y="11" rx="2.5"/>'
             '<rect width="7.5" height="5" x="3.5" y="15.5" rx="2.5"/>'),
    "history": ('<path d="M3.5 12a8.5 8.5 0 1 0 2.8-6.3"/><path d="M3 4v4h4"/>'
                '<path d="M12 7.5V12l3 2"/>'),
    "queue": ('<rect width="18" height="6" x="3" y="4" rx="2.5"/>'
              '<rect width="18" height="6" x="3" y="14" rx="2.5"/>'
              '<path d="M7 7h.01"/><path d="M7 17h.01"/><path d="M11 7h6"/><path d="M11 17h6"/>'),
    "cloud-upload": ('<path d="M4.2 14.7A6.8 6.8 0 1 1 15.4 8.3h1.5a4.3 4.3 0 0 1 2.4 7.9"/>'
                     '<path d="M12 12v8.5"/><path d="m8.5 16 3.5-3.5 3.5 3.5"/>'),
    "cloud-download": ('<path d="M4.2 14.7A6.8 6.8 0 1 1 15.4 8.3h1.5a4.3 4.3 0 0 1 2.4 7.9"/>'
                       '<path d="M12 12v8.5"/><path d="m8.5 17 3.5 3.5 3.5-3.5"/>'),
    "gear": ('<path d="M12.2 2.5h-.4a2 2 0 0 0-2 2v.2a2 2 0 0 1-2.9 1.8l-.2-.1'
             'a2 2 0 0 0-2.7.7l-.2.4a2 2 0 0 0 .7 2.7l.2.1a2 2 0 0 1 0 3.4l-.2.1'
             'a2 2 0 0 0-.7 2.7l.2.4a2 2 0 0 0 2.7.7l.2-.1a2 2 0 0 1 2.9 1.8v.2a2 2 0 0 0 2 2h.4'
             'a2 2 0 0 0 2-2v-.2a2 2 0 0 1 2.9-1.8l.2.1a2 2 0 0 0 2.7-.7l.2-.4a2 2 0 0 0-.7-2.7'
             'l-.2-.1a2 2 0 0 1 0-3.4l.2-.1a2 2 0 0 0 .7-2.7l-.2-.4a2 2 0 0 0-2.7-.7l-.2.1'
             'a2 2 0 0 1-2.9-1.8v-.2a2 2 0 0 0-2-2z"/>'
             '<circle cx="12" cy="12" r="3.2"/>'),
    "panel-left": ('<rect width="18" height="16" x="3" y="4" rx="3"/><path d="M9.5 4v16"/>'),
    "cap-min": '<path d="M5.5 12h13"/>',
    "cap-max": '<rect width="10" height="10" x="7" y="7" rx="1"/>',
    "cap-restore": ('<rect width="9" height="9" x="5" y="10" rx="1"/>'
                    '<path d="M9 6.5h7A1.5 1.5 0 0 1 17.5 8v7"/>'
                    '<path d="M8 6.5h1"/>'),
    "cap-close": '<path d="m6.4 6.4 11.2 11.2"/><path d="M17.6 6.4 6.4 17.6"/>',
    # basics
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "minus": '<path d="M5 12h14"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "arrow-left": '<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>',
    "arrow-up": '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>',
    "arrow-down": '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    "external": ('<path d="M15 3h6v6"/><path d="M10 14 21 3"/>'
                 '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'),
    "refresh": ('<path d="M3 12a9 9 0 0 1 15.3-6.4L21 8"/><path d="M21 3v5h-5"/>'
                '<path d="M21 12a9 9 0 0 1-15.3 6.4L3 16"/><path d="M3 21v-5h5"/>'),
    "search": '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    "sun": ('<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/>'
            '<path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/>'
            '<path d="M2 12h2"/><path d="M20 12h2"/>'
            '<path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>'),
    "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    "monitor": ('<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M9 21h6"/>'
                '<path d="M12 17v4"/>'),
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    "mail": ('<rect width="20" height="16" x="2" y="4" rx="2"/>'
             '<path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>'),
    "link": ('<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>'
             '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>'),
    "trash": ('<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>'
              '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'),
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "globe": ('<circle cx="12" cy="12" r="9"/><path d="M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18"/>'
              '<path d="M3 12h18"/>'),
    "alert": ('<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>'
              '<path d="M12 9v4"/><path d="M12 17h.01"/>'),
    "alert-circle": '<circle cx="12" cy="12" r="9"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
    "x-circle": '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6"/><path d="m15 9-6 6"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8.5 12.2 2.3 2.3 4.7-4.9"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "help": ('<circle cx="12" cy="12" r="9"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-2.9 2.4-2.9 4"/>'
             '<path d="M12 17h.01"/>'),
    "loader": '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
    "shield": ('<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1 1 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>'
               '<path d="m9 12 2 2 4-4"/>'),
    "send": ('<path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z"/>'
             '<path d="m21.854 2.147-10.94 10.939"/>'),
    "zap": '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
    "hard-drive": ('<line x1="22" x2="2" y1="12" y2="12"/>'
                   '<path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>'
                   '<line x1="6" x2="6.01" y1="16" y2="16"/><line x1="10" x2="10.01" y1="16" y2="16"/>'),
    "calendar": ('<path d="M8 2v4"/><path d="M16 2v4"/>'
                 '<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/>'),
    "inbox": ('<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/>'
              '<path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>'),
    "sparkles": ('<path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z"/>'
                 '<path d="M5 3v4"/><path d="M19 17v4"/><path d="M3 5h4"/><path d="M17 19h4"/>'),
    "image": ('<rect width="18" height="18" x="3" y="3" rx="2"/>'
              '<circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>'),
    "package": ('<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/>'
                '<path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>'),
    "file": ('<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
             '<path d="M14 2v4a2 2 0 0 0 2 2h4"/>'),
    "file-text": ('<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
                  '<path d="M14 2v4a2 2 0 0 0 2 2h4"/>'
                  '<path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>'),
    "film": ('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7.5 4v16"/>'
             '<path d="M16.5 4v16"/><path d="M3 9h4.5"/><path d="M3 15h4.5"/>'
             '<path d="M16.5 9H21"/><path d="M16.5 15H21"/>'),
    "music": ('<circle cx="7" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>'
              '<path d="M10 18V5l10-2v13"/>'),
    "code": '<path d="m8 6-5 6 5 6"/><path d="m16 6 5 6-5 6"/>',
    "copy": ('<rect width="13" height="13" x="9" y="9" rx="2"/>'
             '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'),
    "paste": ('<rect x="8" y="2" width="8" height="4" rx="1"/>'
              '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>'),
    "more": ('<path stroke-width="3.4" d="M5 12h.01"/><path stroke-width="3.4" d="M12 12h.01"/>'
             '<path stroke-width="3.4" d="M19 12h.01"/>'),
    "qr": ('<rect x="4" y="4" width="6.5" height="6.5" rx="1"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1"/>'
           '<rect x="4" y="13.5" width="6.5" height="6.5" rx="1"/>'
           '<path d="M13.5 13.5h3v3h-3z"/><path d="M17 13.5h3"/><path d="M20 17v3h-3.5"/>'
           '<path d="M13.5 17v3"/><path d="M17 20h.01"/><path d="M20 13.5h.01"/>'),
    "keyboard": ('<rect x="3" y="6.5" width="18" height="11" rx="2"/>'
                 '<path d="M6.5 10h.01"/><path d="M9.5 10h.01"/><path d="M12.5 10h.01"/>'
                 '<path d="M15.5 10h.01"/><path d="M18 10h.01"/>'
                 '<path d="M7.5 14h9"/><path d="M6.5 14h.01"/><path d="M17.5 14h.01"/>'),
    "pause": ('<rect x="6.5" y="5" width="3.6" height="14" rx="1"/>'
              '<rect x="13.9" y="5" width="3.6" height="14" rx="1"/>'),
    "play": '<path d="M8 5.4v13.2L19 12z"/>',
    "user-x": ('<circle cx="9" cy="8" r="3.6"/><path d="M3.2 20.4c.4-3.1 2.8-5 5.8-5 1.2 0 2.3.3 3.2.9"/>'
               '<path d="m16 16.8 4.6 4.6"/><path d="m20.6 16.8-4.6 4.6"/>'),
    "filter": '<path d="M4 5h16l-6.3 7.4V19l-3.4 2v-8.6z"/>',
    "corner-down-left": ('<polyline points="9 10 4 15 9 20"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/>'),
    "sliders": ('<line x1="21" x2="14" y1="4" y2="4"/><line x1="10" x2="3" y1="4" y2="4"/>'
                '<line x1="21" x2="12" y1="12" y2="12"/><line x1="8" x2="3" y1="12" y2="12"/>'
                '<line x1="21" x2="16" y1="20" y2="20"/><line x1="12" x2="3" y1="20" y2="20"/>'
                '<line x1="14" x2="14" y1="2" y2="6"/><line x1="8" x2="8" y1="10" y2="14"/>'
                '<line x1="16" x2="16" y1="18" y2="22"/>'),
    "database": ('<ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v13c0 1.66 3.58 3 8 3s8-1.34 8-3v-13"/>'
                 '<path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3"/>'),
    "download-arrow": '<path d="M12 4v11"/><path d="m7 11 5 5 5-5"/><path d="M5 20h14"/>',
    "upload-arrow": '<path d="M12 19V8"/><path d="m7 12 5-5 5 5"/><path d="M5 20h14"/>',
    # advanced features — scheduler, stats, link checker
    "timer": ('<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2"/><path d="M9 2h6"/>'),
    "calendar-clock": ('<path d="M8 2v4"/><path d="M16 2v4"/>'
                       '<rect width="17" height="16" x="3" y="4" rx="2"/><path d="M3 9h17"/>'
                       '<circle cx="16.5" cy="15.5" r="4.2"/><path d="M16.5 14v1.7l1.2 1"/>'),
    "activity": '<path d="M22 12h-4l-3 8L9 4l-3 8H2"/>',
    "bar-chart": ('<path d="M5 20V10"/><path d="M12 20V4"/><path d="M19 20v-7"/>'),
    "pie": ('<path d="M21.2 12A9.2 9.2 0 1 1 12 2.8"/><path d="M12 2.8V12h9.2A9.2 9.2 0 0 0 12 2.8Z"/>'),
    "gauge": ('<path d="M12 15l3.5-5.5"/><path d="M20.5 15.5a9 9 0 1 0-17 0"/>'),
    "rocket": ('<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/>'
               '<path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/>'
               '<path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>'),
    "languages": ('<path d="m5 8 6 6"/><path d="m4 14 6-6 2-3"/><path d="M2 5h12"/>'
                  '<path d="M7 2h1"/><path d="m22 22-5-10-5 10"/><path d="M14 18h6"/>'),
    "shield-check": ('<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1 1 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>'
                     '<path d="m9 12 2 2 4-4"/>'),
    "check-list": ('<path d="m3 5 1.5 1.5L7 4"/><path d="m3 11 1.5 1.5L7 10"/><path d="m3 17 1.5 1.5L7 16"/>'
                   '<path d="M11 5h10"/><path d="M11 12h10"/><path d="M11 19h10"/>'),
    "flame": ('<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>'),
    "trending": ('<path d="M22 7 13.5 15.5l-4-4L2 19"/><path d="M16 7h6v6"/>'),
    # 1.0.0.0 additions — tray, notifications, clipboard watcher, copy-all
    "power": ('<path d="M12 2.5v9"/><path d="M18.4 6.6a9 9 0 1 1-12.8 0"/>'),
    "bell": ('<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>'
             '<path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>'),
    "clipboard-check": ('<rect width="8" height="4" x="8" y="2" rx="1"/>'
                        '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>'
                        '<path d="m9 14.5 2 2 4-4.5"/>'),
    "tray": ('<path d="M3.5 5A1.5 1.5 0 0 1 5 3.5h14A1.5 1.5 0 0 1 20.5 5v9a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 14z"/>'
             '<path d="M12 6.5v5"/><path d="m9.8 9.7 2.2 2.2 2.2-2.2"/><path d="M3 20.5h18"/>'),
    "copy-check": ('<rect width="11" height="11" x="3" y="9" rx="2"/>'
                   '<path d="M5 7V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-2"/>'
                   '<path d="m8.3 14.6 1.8 1.8 3.4-3.6"/>'),
}

# ---------------------------------------------------------------- duotone
# name -> indices (in drawing order) of the elements that get the translucent fill.
DUO: dict[str, tuple[int, ...]] = {
    "grid": (0, 2), "queue": (0, 1), "cloud-upload": (0,), "cloud-download": (0,),
    "gear": (0,), "panel-left": (0,), "monitor": (0,), "clock": (0,), "mail": (0,),
    "folder": (0,), "globe": (0,), "alert": (0,), "alert-circle": (0,), "x-circle": (0,),
    "check-circle": (0,), "info": (0,), "help": (0,), "shield": (0,), "shield-check": (0,),
    "send": (0,), "zap": (0,), "hard-drive": (1,), "calendar": (2,), "inbox": (1,),
    "sparkles": (0,), "image": (0,), "package": (0,), "file": (0,), "file-text": (0,),
    "film": (0,), "music": (0, 1), "copy": (0,), "paste": (1,), "keyboard": (0,),
    "pause": (0, 1), "play": (0,), "filter": (0,), "database": (0,), "timer": (0,),
    "calendar-clock": (2,), "pie": (1,), "rocket": (0,), "flame": (0,), "bell": (0,),
    "clipboard-check": (1,), "tray": (0,), "copy-check": (0,), "moon": (0,),
    "search": (0,), "sun": (0,), "trash": (1,), "user-x": (0,), "cap-max": (0,),
}
DUO_ALPHA = 0.17          # strength of the tinted fill
DUO_MIN_SIZE = 14         # below this the fill only muddies the glyph
_ELEMENT = re.compile(r"<(rect|circle|ellipse|path|polyline|line)\b([^>]*?)/>")


def _duo_layer(name: str, body: str, color: str) -> str:
    keep = DUO.get(name)
    if not keep:
        return ""
    out = []
    for i, (tag, attrs) in enumerate(_ELEMENT.findall(body)):
        if i in keep:
            out.append(f'<{tag}{attrs} fill="{color}" fill-opacity="{DUO_ALPHA}" stroke="none"/>')
    return "".join(out)


# ------------------------------------------------------------------ helpers
_cache: dict[tuple, QPixmap] = {}


def _dpr() -> float:
    app = QApplication.instance()
    if app is None:
        return 1.0
    try:
        screens = app.screens()
        return max(float(sc.devicePixelRatio()) for sc in screens) if screens else 1.0
    except Exception:  # noqa: BLE001
        return 1.0


def icon_svg(name: str, color: str, size: int = 20, stroke: float = 1.5,
             duo: bool = True) -> str:
    # High-quality rendering at small sizes: a 1.5px stroke on the 24-grid
    # becomes a sub-pixel hairline when the icon is drawn at 12-16px, which
    # reads as blurry.  Bump the stroke proportionally (capped) so every
    # glyph stays crisp at any size.
    boost = min(1.38, (24.0 / max(8, size)) ** 0.42)
    sw = round(stroke * boost, 2)
    name = name if name in ICONS else "info"
    body = ICONS[name]
    if duo and size >= DUO_MIN_SIZE:
        body = _duo_layer(name, body, color) + body       # tint first, crisp stroke on top
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="{sw}" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


def pixmap(name: str, color: Optional[str] = None, size: int = 20,
           stroke: float = 1.5, duo: bool = True) -> QPixmap:
    """A tinted icon pixmap, rendered at the screen device ratio."""
    if color is None:
        color = theme.pal["text2"]
    dpr = max(1.0, _dpr())
    key = (name, color, size, stroke, duo, dpr)
    if key in _cache:
        return _cache[key]
    px = int(size * dpr)
    renderer = QSvgRenderer(QByteArray(icon_svg(name, color, size, stroke, duo).encode("utf-8")))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing
                           | QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter, QRectF(0, 0, px, px))
    painter.end()
    pm.setDevicePixelRatio(dpr)
    if len(_cache) > 512:
        _cache.clear()
    _cache[key] = pm
    return pm


_renderers: dict[tuple, QSvgRenderer] = {}


def paint(painter: QPainter, name: str, color: str, rect: QRectF,
          stroke: float = 1.6, duo: bool = True) -> None:
    """Draw an icon as *vector* straight into ``painter`` — resolution
    independent, so it is razor sharp on any display scale (no pixmap, no
    resampling).  Use this from paintEvent()/delegates for HD status icons."""
    size = max(8, int(round(min(rect.width(), rect.height()))))
    key = (name, color, size, stroke, duo)
    r = _renderers.get(key)
    if r is None:
        if len(_renderers) > 256:
            _renderers.clear()
        r = QSvgRenderer(QByteArray(icon_svg(name, color, size, stroke, duo).encode("utf-8")))
        _renderers[key] = r
    painter.save()
    painter.setRenderHints(QPainter.RenderHint.Antialiasing
                           | QPainter.RenderHint.SmoothPixmapTransform)
    r.render(painter, rect)
    painter.restore()


def icon(name: str, color: Optional[str] = None, size: int = 20,
         stroke: float = 1.5) -> QIcon:
    return QIcon(pixmap(name, color, size, stroke))


def flush_cache() -> None:
    _cache.clear()
    _renderers.clear()


# --------------------------------------------------------------------- logo
# The mark is a route: a hollow node, a line, a hollow node ("This PC" to
# "link") on a rounded-square tile.  Ink tile + white glyph on light themes,
# inverted on dark themes.  Designed on a 64-unit grid; tools/make_icon.py
# draws the identical geometry for the .ico / .icns / .png files.
LOGO_INK = "#0F1218"
LOGO_PAPER = "#ECEEF2"
LOGO_RADIUS = 18                      # tile corner radius on the 64 grid (28 %)


def logo_scale(px: float) -> float:
    """Glyph size multiplier: small renders get a bigger, bolder glyph."""
    return 1.5 if px <= 24 else 1.3 if px <= 40 else 1.15 if px <= 64 else 1.0


def logo_svg(px: int = 64, dark: bool = False, logical: float | None = None) -> str:
    """The logo as SVG.  ``dark`` = inverted tile for dark surfaces; ``logical``
    is the on-screen size (device-independent) that picks the glyph boost."""
    tile, ink = (LOGO_PAPER, LOGO_INK) if dark else (LOGO_INK, "#FFFFFF")
    k = logo_scale(logical or px)
    ring_r, ring_w, line_w = 3.5 * k, 2.6 * k, 2.3 * k      # ring centre-line radius, strokes
    cx_l, cx_r = 32 - 12.3 * k, 32 + 12.3 * k
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{px}" height="{px}" '
            f'viewBox="0 0 64 64" fill="none">'
            f'<rect width="64" height="64" rx="{LOGO_RADIUS}" fill="{tile}"/>'
            f'<g stroke="{ink}" stroke-linecap="round">'
            f'<circle cx="{cx_l:.2f}" cy="32" r="{ring_r:.2f}" stroke-width="{ring_w:.2f}"/>'
            f'<circle cx="{cx_r:.2f}" cy="32" r="{ring_r:.2f}" stroke-width="{ring_w:.2f}"/>'
            f'<path d="M{cx_l + ring_r:.2f} 32H{cx_r - ring_r:.2f}" stroke-width="{line_w:.2f}"/>'
            f'</g></svg>')


def logo_pixmap(size: int = 32, glow: bool = False, fixed: bool = False) -> QPixmap:
    """Logo pixmap.  Follows the theme (inverted on dark) unless ``fixed``
    (the OS app icon is always the ink tile).  ``glow`` adds a soft shadow."""
    dpr = max(1.0, _dpr())
    pad = int(size * 0.35) if glow else 0
    total = int((size + pad * 2) * dpr)
    px = int(size * dpr)
    renderer = QSvgRenderer(QByteArray(
        logo_svg(px, dark=(theme.dark and not fixed), logical=size).encode("utf-8")))
    pm = QPixmap(total, total)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if glow:
        from PyQt6.QtGui import QColor, QRadialGradient
        g = QRadialGradient(total / 2, total / 2 + pad * 0.35, total / 2)
        g.setColorAt(0.0, QColor(0, 0, 0, 70))
        g.setColorAt(0.62, QColor(0, 0, 0, 28))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(0, 0, total, total, g)
    off = int(pad * dpr)
    renderer.render(painter, QRectF(off, off, px, px))
    painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def app_icon() -> QIcon:
    """Window / taskbar / tray icon: always the ink tile, tuned per size."""
    ic = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(logo_pixmap(size, fixed=True))
    return ic


def _on_theme_changed(*_a) -> None:
    flush_cache()


theme.changed.connect(_on_theme_changed)
