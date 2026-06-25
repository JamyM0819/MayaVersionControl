"""
ui/perf_panel.py - Performance monitor panel for MayaVC.
Visualizes timing stats for each step: git commands, save/load, etc.

Usage:
    from core.perf_monitor import show_perf_panel
    show_perf_panel()
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QAbstractItemView, QCheckBox,
)
from PySide6.QtCore import Qt, QTimer

from core.perf_monitor import get_perf


def _get_maya_window():
    try:
        from shiboken6 import wrapInstance
        import maya.OpenMayaUI as omui
        ptr = omui.MQtUtil.mainWindow()
        if ptr:
            return wrapInstance(int(ptr), QWidget)
    except Exception:
        pass
    return None


_COLORS = {
    "fast":  "#27ae60",   # green  (< 10ms)
    "ok":    "#f39c12",   # orange (10-100ms)
    "slow":  "#e74c3c",   # red    (> 100ms)
    "huge":  "#c0392b",   # dark red (> 500ms)
}


def _color_for_ms(ms):
    if ms < 10:
        return _COLORS["fast"]
    elif ms < 100:
        return _COLORS["ok"]
    elif ms < 500:
        return _COLORS["slow"]
    else:
        return _COLORS["huge"]


# ---- table model ----

_COLUMNS = [
    ("Step", 160),
    ("Count", 60),
    ("Last (ms)", 80),
    ("Avg (ms)", 80),
    ("Min (ms)", 80),
    ("Max (ms)", 80),
    ("Total (ms)", 100),
]


def show():
    mw = _get_maya_window()
    if mw is None:
        return

    # Close existing
    if hasattr(show, "_win"):
        try:
            show._win.close()
        except Exception:
            pass
        show._win = None

    win = QWidget(mw, Qt.Window)
    win.setObjectName("MayaVCPerfPanel")
    win.setWindowTitle("MayaVC — Performance Monitor")
    win.resize(820, 400)
    win.setMinimumSize(600, 250)

    lay = QVBoxLayout(win)
    lay.setContentsMargins(6, 6, 6, 6)
    lay.setSpacing(4)

    # -- top bar --
    top = QHBoxLayout()

    enabled_cb = QCheckBox("Enable profiling")
    top.addWidget(enabled_cb)

    # summary label
    summary_label = QLabel("")
    top.addWidget(summary_label, stretch=1)

    auto_cb = QCheckBox("Auto-refresh (1s)")
    auto_cb.setChecked(True)
    top.addWidget(auto_cb)

    clear_btn = QPushButton("Clear")
    top.addWidget(clear_btn)

    refresh_btn = QPushButton("Refresh")
    top.addWidget(refresh_btn)

    lay.addLayout(top)

    # -- table --
    table = QTableWidget(0, len(_COLUMNS))
    table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    hdr = table.horizontalHeader()
    for i, (_, w) in enumerate(_COLUMNS):
        hdr.setSectionResizeMode(i, QHeaderView.Fixed)
        table.setColumnWidth(i, w)
    hdr.setStretchLastSection(True)
    lay.addWidget(table, stretch=1)

    # -- timer --
    timer = QTimer(win)
    timer.setInterval(1000)

    def do_refresh():
        pm = get_perf()
        data = pm.stats()
        table.setRowCount(len(data))

        total_overall = 0.0
        for i, d in enumerate(data):
            c = _color_for_ms(d["avg_ms"])
            items = [
                (d["label"], Qt.AlignLeft | Qt.AlignVCenter),
                (str(d["count"]), Qt.AlignCenter),
                (f"{d['last_ms']:.1f}", Qt.AlignRight | Qt.AlignVCenter),
                (f"{d['avg_ms']:.1f}", Qt.AlignRight | Qt.AlignVCenter),
                (f"{d['min_ms']:.1f}", Qt.AlignRight | Qt.AlignVCenter),
                (f"{d['max_ms']:.1f}", Qt.AlignRight | Qt.AlignVCenter),
                (f"{d['total_ms']:.1f}", Qt.AlignRight | Qt.AlignVCenter),
            ]
            for j, (text, alignment) in enumerate(items):
                item = QTableWidgetItem(text)
                item.setTextAlignment(alignment)
                item.setForeground(Qt.GlobalColor.white)
                item.setBackground(Qt.GlobalColor.darkGray)
                # color the avg column to highlight bottlenecks
                if j == 3:
                    from PySide6.QtGui import QColor
                    item.setBackground(QColor(c))
                table.setItem(i, j, item)
            total_overall += d["total_ms"]

        summary_label.setText(
            f"  {len(data)} steps tracked  |  cumulative: {total_overall:.1f} ms"
        )

        # Update enabled state to match reality
        enabled_cb.setChecked(pm.enabled)

    # -- signals --
    def on_enabled(state):
        pm = get_perf()
        pm.enabled = bool(state)

    def on_auto(state):
        if state:
            timer.start()
        else:
            timer.stop()

    enabled_cb.setChecked(True)
    enabled_cb.stateChanged.connect(on_enabled)
    auto_cb.stateChanged.connect(on_auto)

    clear_btn.clicked.connect(lambda: (get_perf().clear(), do_refresh()))
    refresh_btn.clicked.connect(do_refresh)

    timer.timeout.connect(do_refresh)
    timer.start()

    do_refresh()

    show._win = win
    win.show()

    import maya.cmds as _dbg
    _dbg.warning("MayaVC: Performance panel shown — start using the plugin to collect data.")
