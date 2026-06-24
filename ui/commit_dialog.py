"""
ui/commit_dialog.py - Commit message dialog

Usage:
  from ui.commit_dialog import show_commit_dialog
  msg, ok = show_commit_dialog("myProject_v004.ma", parent=maya_main_window)
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QPushButton,
)
from PySide6.QtCore import Qt, Signal


class CommitDialog(QDialog):
    """Collect commit message from user after incremental save."""

    submitted = Signal(str)

    def __init__(self, filename: str, parent=None):
        super().__init__(parent)
        self._filename = filename
        self.setWindowTitle("Incremental Save - Commit Message")
        self.setMinimumSize(480, 200)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel(f"Scene:  {self._filename}"))
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

    def get_message(self) -> str:
        return self.text_edit.toPlainText().strip()


def show_commit_dialog(filename: str, parent=None) -> tuple:
    """Show commit dialog, return (message, ok)."""
    dlg = CommitDialog(filename, parent)
    result = dlg.exec()
    return dlg.get_message(), (result == QDialog.Accepted)
