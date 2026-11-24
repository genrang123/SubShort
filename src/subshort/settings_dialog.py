"""Typed settings editor; credential management stays in its separate dialog."""

import copy
from dataclasses import fields

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

from .credentials import CredentialError
from .models import ASR_MODELS
from .ui import page_header


# group, field, Vietnamese label, value type, allowed choices (when fixed)
FIELD_REGISTRY = (
    ("Chung", "mode", "Chế độ", str, ("subtitle_dub", "subtitle", "dub")),
    ("Chung", "source_language", "Ngôn ngữ nguồn (trống: tự nhận diện)", str, ()),
    ("Chung", "target_language", "Ngôn ngữ đích", str, ()),
    ("Chung", "slow_speed", "Tốc độ hình để canh lời và phụ đề", float, ()),
    ("Chung", "final_speed", "Hệ số tốc độ khi xuất (hình, tiếng, phụ đề)", float, ()),
    ("Nhận dạng", "asr_provider", "Model", str, tuple(ASR_MODELS)),
    ("Nhận dạng", "asr_device", "Thiết bị ASR", str, ("cpu", "cuda")),
    ("Người nói", "multi_speaker", "Nhận diện nhiều người nói", bool, ()),
    ("Người nói", "diarization_model", "Thư mục model diarization", str, ()),
    ("Người nói", "diarization_python", "Python diarization", str, ()),
    ("Người nói", "diarization_hf_cache", "Cache HuggingFace diarization", str, ()),
    ("Người nói", "diarization_device", "Thiết bị diarization", str, ("cpu", "cuda")),
    ("Dịch", "translation_provider", "Dịch vụ nhận văn bản ASR", str, ("openai", "gemini", "openai_compatible")),
    ("Dịch", "translation_model", "Model dịch (trống: mặc định)", str, ()),
    ("Dịch", "translation_base_url", "Base URL OpenAI-compatible (gồm /v1 nếu cần)", str, ()),
    ("Giọng đọc", "tts_provider", "Bộ tạo giọng (Edge nhận bản dịch qua mạng)", str, ("edge", "vieneu", "omnivoice", "none")),
    ("Giọng đọc", "tts_model", "Thư mục model TTS", str, ()),
    ("Giọng đọc", "tts_python", "Python TTS", str, ()),
    ("Giọng đọc", "tts_hf_cache", "Cache HuggingFace TTS", str, ()),
    ("Giọng đọc", "tts_device", "Thiết bị TTS", str, ("cpu", "cuda")),
    ("Giọng đọc", "tts_custom_voice", "Voice tùy biến (UUID)", str, ()),
    ("Giọng đọc", "tts_voice", "Voice Edge / preset VieNeu", str, ()),
    ("Giọng đọc", "consent_omnivoice_license", "Chấp nhận giấy phép OmniVoice", bool, ()),
    ("Âm thanh / Đầu ra", "keep_original_audio", "Giữ âm thanh gốc", bool, ()),
    ("Âm thanh / Đầu ra", "separate_background", "Tách âm nền", bool, ()),
    ("Âm thanh / Đầu ra", "background_volume", "Âm lượng nền (0–1)", float, ()),
    ("Âm thanh / Đầu ra", "separator_model_dir", "Thư mục model tách nền", str, ()),
    ("Âm thanh / Đầu ra", "separator_python", "Python tách nền", str, ()),
    ("Âm thanh / Đầu ra", "ffmpeg", "Đường dẫn FFmpeg", str, ()),
    ("Âm thanh / Đầu ra", "ffprobe", "Đường dẫn FFprobe", str, ()),
    ("Âm thanh / Đầu ra", "output_dir", "Thư mục đầu ra", str, ()),
    ("Đồng bộ", "drive_folder_id", "Google Drive folder ID", str, ()),
    ("Video", "output_layout", "Khung hình đầu ra", str, ("original", "portrait", "landscape")),
    ("Video", "video_encoder", "Bộ mã hóa video", str, ("cpu", "auto", "nvenc")),
    ("Video", "video_bitrate_mbps", "Bitrate video (Mbps, 0.1–100)", float, ()),
    ("Phụ đề cũ", "old_subtitle_mode", "Xóa phụ đề (auto gửi 3 frame tới vision)", str, ("off", "manual", "auto")),
    ("Phụ đề cũ", "old_subtitle_region", "Vùng nguồn JSON [x,y,w,h] (0–1)", str, ()),
    ("Phụ đề cũ", "vision_provider", "Provider vision", str, ("openai", "gemini")),
    ("Phụ đề cũ", "vision_model", "Model vision (phải hỗ trợ hình ảnh)", str, ()),
    ("Hook trích văn bản", "hook_enabled", "Bật hook trích từ văn bản nguồn (không dùng AI)", bool, ()),
    ("Hook trích văn bản", "hook_theme", "Kiểu hiển thị hook", str, ("auto", "bold", "minimal")),
    ("Hook trích văn bản", "hook_seed", "Seed chọn câu nguồn", int, ()),
    ("Hook trích văn bản", "hook_duration", "Thời lượng hook (giây, 1–8)", float, ()),
)

CHOICE_LABELS = {
    "asr_provider": ASR_MODELS,
    "mode": {"subtitle_dub": "Phụ đề + Lồng tiếng", "subtitle": "Chỉ phụ đề", "dub": "Chỉ lồng tiếng"},
}
ASR_HIDDEN_FIELDS = ("asr_model", "asr_aligner", "asr_vad_model", "firered_type")

FIELD_TOOLTIPS = {
    "slow_speed": (
        "Đổi tốc độ hình để canh thời điểm lồng tiếng và phụ đề. "
        "Ví dụ: 0.7 cho hình chạy ở 70% tốc độ gốc, tạo thêm thời gian cho lời đọc. "
        "Mục này không đổi tốc độ giọng lồng tiếng."
    ),
    "final_speed": (
        "Tăng hoặc giảm tốc độ của cả hình, tiếng và phụ đề khi xuất. "
        "1.0 giữ nguyên tốc độ đã dựng; 1.25 nhanh hơn 25%. "
        "Ví dụ: tốc độ hình 0.8 × hệ số xuất 1.25 = 1.0 (tốc độ hình gốc)."
    ),
}


class SettingsDialog(QDialog):
    """Modal editor; on_saved() runs only after validation and persistence succeed."""

    def __init__(self, parent, settings, on_saved=None):
        super().__init__(parent)
        self.settings = settings
        self.on_saved = on_saved
        self.setWindowTitle("Cài đặt")
        self.setMinimumSize(520, 290)
        self.resize(760, 520)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        page_header(
            layout, "Cài đặt",
            "Giá trị chỉ được ghi vào settings.json sau khi bấm Lưu; Hủy giữ nguyên cấu hình đang chạy.")
        tabs = QTabWidget()
        tabs.setUsesScrollButtons(True)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Form chỉ cuộn dọc; ô nhập co giãn theo bề rộng thay vì đẩy thanh ngang.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(tabs)
        layout.addWidget(scroll, 1)

        forms = {}
        self.editors = {}
        existing = {field.name for field in fields(settings)}
        for group, name, label, value_type, choices in FIELD_REGISTRY:
            if name not in existing:
                continue
            if group not in forms:
                page = QWidget()
                form = QFormLayout(page)
                form.setContentsMargins(12, 12, 12, 12)
                form.setHorizontalSpacing(16)
                form.setVerticalSpacing(10)
                form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                forms[group] = form
                tabs.addTab(page, group)

            value = getattr(settings, name)
            if value_type is bool:
                editor = QCheckBox()
                editor.setChecked(value)
            elif choices:
                editor = QComboBox()
                labels = CHOICE_LABELS.get(name, {})
                for choice in choices:
                    editor.addItem(labels.get(choice, choice), choice)
                index = editor.findData(value)
                if index < 0:
                    editor.setPlaceholderText(str(value))
                editor.setCurrentIndex(index)
            else:
                editor = QLineEdit(str(value))
            editor.setObjectName(name)
            self.editors[name] = editor
            caption = QLabel(label)
            caption.setWordWrap(True)
            caption.setBuddy(editor)
            if name in FIELD_TOOLTIPS:
                editor.setToolTip(FIELD_TOOLTIPS[name])
                caption.setToolTip(FIELD_TOOLTIPS[name])
            forms[group].addRow(caption, editor)

        self.dirty = QLabel("Chưa có thay đổi", objectName="muted")
        self._saved_state = self._form_state()
        for editor in self.editors.values():
            if isinstance(editor, QCheckBox):
                editor.toggled.connect(self._refresh_dirty)
            elif isinstance(editor, QComboBox):
                editor.currentIndexChanged.connect(self._refresh_dirty)
            else:
                editor.textChanged.connect(self._refresh_dirty)

        footer = QHBoxLayout()
        footer.addWidget(self.dirty)
        footer.addStretch()
        buttons = QDialogButtonBox()
        save = buttons.addButton("Lưu cài đặt", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setProperty("variant", "primary")
        save.setDefault(True)
        buttons.addButton("Hủy", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)

    def _form_state(self):
        values = {}
        for name, editor in self.editors.items():
            if isinstance(editor, QCheckBox):
                values[name] = editor.isChecked()
            elif isinstance(editor, QComboBox):
                values[name] = editor.currentIndex()
            else:
                values[name] = editor.text()
        return values

    def _refresh_dirty(self, *_):
        changed = self._form_state() != self._saved_state
        self.dirty.setText("Có thay đổi chưa lưu" if changed else "Chưa có thay đổi")

    def accept(self):
        candidate = copy.deepcopy(self.settings)
        try:
            for _, name, label, value_type, choices in FIELD_REGISTRY:
                if name not in self.editors:
                    continue
                editor = self.editors[name]
                try:
                    if value_type is bool:
                        value = editor.isChecked()
                    elif choices:
                        value = editor.currentData() if editor.currentIndex() >= 0 else editor.placeholderText()
                    else:
                        value = value_type(editor.text())
                except ValueError:
                    raise ValueError(f"{label}: giá trị không hợp lệ") from None
                setattr(candidate, name, value)
            if candidate.asr_provider != self.settings.asr_provider or (
                candidate.asr_provider == "firered" and candidate.firered_type != "aed"
            ):
                candidate.asr_model = candidate.asr_aligner = candidate.asr_vad_model = ""
                candidate.firered_type = "aed"
            candidate.validate()
            candidate.save()
        except CredentialError:
            self._show_error("Không lưu được cấu hình an toàn. Cài đặt hiện tại được giữ nguyên.")
            return
        except (ValueError, OSError) as exc:
            self._show_error(str(exc))
            return

        for name in (*self.editors, *ASR_HIDDEN_FIELDS):
            setattr(self.settings, name, getattr(candidate, name))
        if self.on_saved is not None:
            self.on_saved()
        super().accept()

    def _show_error(self, text):
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Critical)
        message.setWindowTitle("Không lưu được cài đặt")
        message.setTextFormat(Qt.TextFormat.PlainText)
        message.setText(text)
        message.exec()
