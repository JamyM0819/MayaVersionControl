"""
ui/commit_dialog.py - Commit message dialog

Usage:
  from ui.commit_dialog import show_commit_dialog
  base, msg, ok = show_commit_dialog("hero", 6, "ma", parent=maya_main_window)
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QPushButton, QLineEdit,
)
from PySide6.QtCore import Qt, Signal

from core.vc_engine import detect_next_version, get_plugin_repo_hash


class CommitDialog(QDialog):
    """Collect commit message and optional base-name from user."""

    submitted = Signal(str)

    def __init__(self, base_name: str, next_ver: int, ext: str,
                 parent=None, scenes_dir: str = ""):
        super().__init__(parent)
        self._base_name = base_name
        self._ext = ext
        self._scenes_dir = scenes_dir

        # Get plugin repo hash for the title (not Maya project's)
        repo_hash = get_plugin_repo_hash() or ""

        title = "Incremental Save" if not repo_hash else f"Incremental Save  [{repo_hash}]"
        self.setWindowTitle(title)
        self.setMinimumSize(500, 260)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Base name row with live version preview
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("File name:"))
        self.name_edit = QLineEdit(base_name)
        self.name_edit.setPlaceholderText("e.g. hero, prop_sword, character_rig")
        name_layout.addWidget(self.name_edit, stretch=1)
        self.name_suffix = QLabel(f"_v{next_ver:03d}.{ext}")
        name_layout.addWidget(self.name_suffix)
        layout.addLayout(name_layout)

        def on_name_changed(text):
            base = text.strip() or self._base_name
            if self._scenes_dir:
                _, _, v = detect_next_version(self._scenes_dir, base)
            else:
                v = 1
            self.name_suffix.setText(f"_v{v:03d}.{self._ext}")

        self.name_edit.textChanged.connect(on_name_changed)

        layout.addWidget(QLabel("Description (required):"))

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText(
            "e.g. Adjusted skin weights, added IK/FK switch controller"
        )
        self.text_edit.setMaximumHeight(80)
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(80)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        submit_btn = QPushButton("Commit (Ctrl+Enter)")
        submit_btn.setDefault(True)
        submit_btn.setFixedWidth(140)
        submit_btn.clicked.connect(self._on_submit)
        btn_layout.addWidget(submit_btn)

        layout.addLayout(btn_layout)

    def _on_submit(self):
        msg = self.text_edit.toPlainText().strip()
        if not msg:
            self.text_edit.setFocus()
            self.text_edit.setStyleSheet("border: 2px solid #e74c3c;")
            return
        self.submitted.emit(msg)
        self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Return and event.modifiers() == Qt.ControlModifier:
            self._on_submit()
        else:
            super().keyPressEvent(event)

    def get_base_name(self) -> str:
        """Return the (sanitized) user-chosen base name."""
        name = self.name_edit.text().strip()
        return name if name else self._base_name

    def get_next_version(self) -> int:
        """Return the version number shown in the suffix right now."""
        suffix = self.name_suffix.text()  # e.g. "_v003.ma"
        try:
            return int(suffix.split("_v")[1].split(".")[0])
        except Exception:
            return 1

    def get_message(self) -> str:
        return self.text_edit.toPlainText().strip()


def show_commit_dialog(base: str, next_ver: int, ext: str,
                       parent=None, scenes_dir: str = "") -> tuple:
    """Show commit dialog, return (base_name, version, message, ok)."""
    dlg = CommitDialog(base, next_ver, ext, parent=parent, scenes_dir=scenes_dir)
    result = dlg.exec()
    if result != QDialog.Accepted:
        return base, next_ver, "", False
    return dlg.get_base_name(), dlg.get_next_version(), dlg.get_message(), True


# ---------------------------------------------------------------------------
# Amend dialog — multi-line commit message for "Save w/ Commit"
# ---------------------------------------------------------------------------

class AmendDialog(QDialog):
    """Collect a multi-line commit message to append to the current version."""

    def __init__(self, tag: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Save w/ Commit — {tag}")
        self.setMinimumSize(520, 300)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel(
            f"Append commit to <b>{tag}</b>.<br>"
            f"Describe what changed (multi-line OK):"
        ))

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText(
            "e.g.\n- Adjusted skin weights on shoulder\n- Added IK/FK switch controller\n- Cleaned up unused nodes"
        )
        layout.addWidget(self.text_edit, stretch=1)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(80)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        submit_btn = QPushButton("Commit (Ctrl+Enter)")
        submit_btn.setDefault(True)
        submit_btn.setFixedWidth(140)
        submit_btn.clicked.connect(self._on_submit)
        btn_layout.addWidget(submit_btn)

        layout.addLayout(btn_layout)

    def _on_submit(self):
        msg = self.text_edit.toPlainText().strip()
        if not msg:
            self.text_edit.setFocus()
            self.text_edit.setStyleSheet("border: 2px solid #e74c3c;")
            return
        self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Return and event.modifiers() == Qt.ControlModifier:
            self._on_submit()
        else:
            super().keyPressEvent(event)

    def get_message(self) -> str:
        return self.text_edit.toPlainText().strip()


def show_amend_dialog(tag: str, parent=None) -> tuple:
    """Show dialog for amending a commit. Returns (message, ok)."""
    dlg = AmendDialog(tag, parent=parent)
    result = dlg.exec()
    if result != QDialog.Accepted:
        return "", False
    return dlg.get_message(), True
