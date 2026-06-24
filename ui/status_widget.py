"""
ui/status_widget.py - Lightweight git status widget for Maya main UI

A floating tool window showing:
  - Current version number
  - Uncommitted changes count
  - Last commit time
"""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer

from core.vc_engine import get_scenes_dir, get_repo_status


class StatusWidget(QWidget):
    """Floating status bar for MayaVC."""

    _instance = None

    @classmethod
    def show(cls):
        if cls._instance is None:
            cls._instance = cls()
        cls._instance._refresh()
        cls._instance.show()
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MayaVC Status")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setFixedHeight(28)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(12)

        self.version_label = QLabel("v---")
        layout.addWidget(self.version_label)

        self.changes_label = QLabel("Changes: ---")
        layout.addWidget(self.changes_label)

        self.last_commit_label = QLabel("Last commit: ---")
        layout.addWidget(self.last_commit_label)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(15000)

    def _refresh(self):
        scenes = get_scenes_dir()
        s = get_repo_status(scenes)
        if not s["is_repo"]:
            self.version_label.setText("(not a git repo)")
            self.changes_label.setText("")
            self.last_commit_label.setText("")
            return

        if s["current_version"]:
            self.version_label.setText(f"v{s['current_version']:03d}")
        else:
            self.version_label.setText("v---")

        if s["uncommitted_changes"]:
            n = s["uncommitted_changes"]
            color = "#e74c3c" if n > 0 else "#27ae60"
            self.changes_label.setText(f"Uncommitted: {n}")
            self.changes_label.setStyleSheet(f"color: {color};")
        else:
            self.changes_label.setText("Clean")
            self.changes_label.setStyleSheet("color: #27ae60;")

        self.last_commit_label.setText(
            f"Last commit: {s['last_commit_time']}"
            if s["last_commit_time"] else "No commits"
        )
