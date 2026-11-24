"""Drive import is not consent: each upload requires an unchecked confirmation."""
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .drive import DriveError, import_service_account
from .ui import page_header, show_message


class DriveDialog(QDialog):
    def __init__(self, parent, manager, root, job_id=None):
        super().__init__(parent)
        self.manager, self.root, self.job_id = manager, Path(root), job_id
        self.setWindowTitle("Tải video lên Google Drive")
        self.setMinimumSize(500, 290)
        self.resize(640, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        page_header(
            layout, "Xuất bản lên Google Drive",
            "Service account cần quyền ghi vào thư mục đích.\n"
            "Credential lưu bằng Windows DPAPI; không kèm trong bundle.")

        form = QWidget()
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(10)
        self.import_button = QPushButton("Nhập JSON service account…")
        self.import_button.clicked.connect(self.import_account)
        form_layout.addWidget(self.import_button)

        self.folder = QLineEdit(getattr(manager.settings, "drive_folder_id", ""))
        label = QLabel("Drive folder ID")
        label.setBuddy(self.folder)
        form_layout.addWidget(label)
        form_layout.addWidget(self.folder)
        self.consent = QCheckBox("Tôi đồng ý gửi video đã chọn tới Google Drive")
        form_layout.addWidget(self.consent)
        info = QLabel("Chỉ tải video đã hoàn tất. Video trên máy luôn được giữ nguyên.")
        info.setWordWrap(True)
        form_layout.addWidget(info)
        self.hint = QLabel(objectName="muted")
        self.hint.setWordWrap(True)
        form_layout.addWidget(self.hint)
        form_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Chỉ cuộn dọc để cấu hình vẫn đọc được ở viewport thấp; hàng nút nằm ngoài.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(form)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        self.cancel_upload_button = QPushButton("Hủy tải đang chạy")
        self.cancel_upload_button.setProperty("variant", "danger")
        self.cancel_upload_button.clicked.connect(manager.cancel)
        buttons.addWidget(self.cancel_upload_button)
        buttons.addStretch()
        close_button = QPushButton("Đóng")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        self.upload_button = QPushButton("Tải video đã chọn")
        self.upload_button.setProperty("variant", "primary")
        self.upload_button.clicked.connect(self.accept)
        buttons.addWidget(self.upload_button)
        layout.addLayout(buttons)
        self.upload_button.setDefault(True)
        self.folder.setFocus()

        # The upload is owned by the manager and can outlive this dialog.
        self._upload_poll = QTimer(self, interval=200)
        self._upload_poll.timeout.connect(self._sync_controls)
        self._sync_controls()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_controls()
        self._upload_poll.start()

    def hideEvent(self, event):
        self._upload_poll.stop()
        super().hideEvent(event)

    def _sync_controls(self):
        running = self.manager.running
        for control in (self.folder, self.consent, self.import_button):
            control.setEnabled(not running)
        # Gửi video cần job đã render; cấu hình service account/folder vẫn sửa được khi chưa chọn.
        self.upload_button.setEnabled(not running and bool(self.job_id))
        self.cancel_upload_button.setEnabled(running)
        if running:
            self.hint.setText("Đang tải lên; cấu hình Drive tạm khóa cho tới khi xong.")
        elif not self.job_id:
            self.hint.setText("Chưa chọn video đã render xong. Bạn vẫn có thể nhập service account và folder ID trước.")
        else:
            self.hint.setText("")

    def import_account(self):
        if self.manager.running:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Chọn JSON service account", "", "JSON (*.json)")
        if not path or self.manager.running:
            return
        try:
            import_service_account(self.root, Path(path))
        except DriveError:
            show_message(self, "Drive", "Không nhập được service account hợp lệ; credential cũ được giữ nguyên.")
            return
        show_message(self, "Drive", "Đã lưu credential được bảo vệ. Chưa có video nào được gửi.",
                     QMessageBox.Icon.Information)

    def accept(self):
        if not self.consent.isChecked():
            show_message(self, "Drive", "Cần đồng ý rõ ràng trước khi gửi video.")
            return
        if not self.job_id:
            show_message(self, "Drive", "Hãy chọn video đã render xong.")
            return
        if self.manager.running:
            show_message(self, "Drive", "Đang có một video được tải lên.")
            return
        folder = self.folder.text().strip()
        try:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", folder):
                raise ValueError("Drive folder ID không hợp lệ")
            settings = self.manager.settings
            previous = settings.drive_folder_id
            settings.drive_folder_id = folder
            try:
                settings.save()
            except Exception:
                settings.drive_folder_id = previous
                raise
            if not self.manager.start(self.job_id, folder):
                show_message(self, "Drive", "Đang có một video được tải lên.")
                return
        except Exception:
            show_message(self, "Drive", "Không bắt đầu được tải lên. Kiểm tra thư mục và video đã chọn.")
            return
        self.consent.setChecked(False)
        super().accept()
