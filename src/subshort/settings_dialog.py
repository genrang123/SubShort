"""Typed settings editor with shared LLM credential management."""

import copy
from dataclasses import fields
from queue import Empty, SimpleQueue
from threading import Thread

from PySide6.QtCore import QRect, QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

from .asr import QWEN_LANGUAGES
from .credentials import CREDENTIAL_FIELDS, CredentialError
from .execution import Cancelled, Execution
from .models import ASR_MODELS
from .providers import ProviderError, list_llm_models
from .ui import page_header
from .voices import VoiceError, VoiceRegistry
from .voices_dialog import _load_presets, _preset_runtime, _voice_work, preset_key


API_TAB = "API & dịch vụ"
LLM_PROVIDERS = {"openai": "OpenAI", "gemini": "Gemini", "openai_compatible": "OpenAI-compatible"}
LLM_FIELDS = {"translation_provider", "translation_model", "translation_base_url", "vision_provider", "vision_model"}
MODEL_FIELDS = {"translation_model", "vision_model"}
LANGUAGE_FIELDS = {"source_language", "target_language"}
VOICE_FIELDS = {"tts_voice", "tts_custom_voice"}
CATALOG_FIELDS = MODEL_FIELDS | LANGUAGE_FIELDS | VOICE_FIELDS
LANGUAGE_LABELS = {
    "vi": "Tiếng Việt", "zh": "Tiếng Trung (Quan thoại)", "yue": "Tiếng Quảng Đông",
    "en": "Tiếng Anh", "ja": "Tiếng Nhật", "ko": "Tiếng Hàn", "th": "Tiếng Thái",
    "fr": "Tiếng Pháp", "de": "Tiếng Đức", "es": "Tiếng Tây Ban Nha", "pt": "Tiếng Bồ Đào Nha",
    "ru": "Tiếng Nga", "ar": "Tiếng Ả Rập", "hi": "Tiếng Hindi", "id": "Tiếng Indonesia",
    "it": "Tiếng Ý", "tr": "Tiếng Thổ Nhĩ Kỳ", "ms": "Tiếng Mã Lai", "nl": "Tiếng Hà Lan",
    "sv": "Tiếng Thụy Điển", "da": "Tiếng Đan Mạch", "fi": "Tiếng Phần Lan", "pl": "Tiếng Ba Lan",
    "cs": "Tiếng Séc", "tl": "Tiếng Filipino", "fa": "Tiếng Ba Tư", "el": "Tiếng Hy Lạp",
    "ro": "Tiếng Romania", "hu": "Tiếng Hungary", "mk": "Tiếng Macedonia", "uk": "Tiếng Ukraina",
    "he": "Tiếng Hebrew", "bn": "Tiếng Bengal", "ta": "Tiếng Tamil", "km": "Tiếng Khmer",
    "lo": "Tiếng Lào", "my": "Tiếng Myanmar",
}


# group, field, Vietnamese label, value type, allowed choices (when fixed)
FIELD_REGISTRY = (
    ("Chung", "mode", "Chế độ", str, ("subtitle_dub", "subtitle", "dub")),
    ("Chung", "source_language", "Ngôn ngữ nguồn", str, ()),
    ("Chung", "target_language", "Ngôn ngữ đích", str, ()),
    ("Chung", "slow_speed", "Tốc độ hình để canh lời và phụ đề", float, ()),
    ("Chung", "final_speed", "Hệ số tốc độ khi xuất (hình, tiếng, phụ đề)", float, ()),
    (API_TAB, "translation_provider", "Dịch vụ dịch", str, tuple(LLM_PROVIDERS)),
    (API_TAB, "translation_model", "Model ID dịch", str, ()),
    (API_TAB, "translation_base_url", "Base URL", str, ()),
    (API_TAB, "vision_provider", "Dịch vụ nhận diện ảnh", str, ("openai", "gemini")),
    (API_TAB, "vision_model", "Model ID nhận diện ảnh", str, ()),
    ("Nhận dạng", "asr_provider", "Model", str, tuple(ASR_MODELS)),
    ("Nhận dạng", "asr_device", "Thiết bị ASR", str, ("cpu", "cuda")),
    ("Người nói", "multi_speaker", "Nhận diện nhiều người nói", bool, ()),
    ("Người nói", "diarization_model", "Thư mục model diarization", str, ()),
    ("Người nói", "diarization_python", "Python diarization", str, ()),
    ("Người nói", "diarization_hf_cache", "Cache HuggingFace diarization", str, ()),
    ("Người nói", "diarization_device", "Thiết bị diarization", str, ("cpu", "cuda")),
    ("Giọng đọc", "tts_provider", "Bộ tạo giọng (Edge nhận bản dịch qua mạng)", str, ("edge", "vieneu", "omnivoice", "none")),
    ("Giọng đọc", "tts_voice", "Giọng có sẵn", str, ()),
    ("Giọng đọc", "tts_custom_voice", "Giọng từ bản thu", str, ()),
    ("Giọng đọc", "tts_speed", "Tốc độ đọc", float, ()),
    ("Giọng đọc", "tts_model", "Thư mục model TTS", str, ()),
    ("Giọng đọc", "tts_python", "Python TTS", str, ()),
    ("Giọng đọc", "tts_hf_cache", "Cache HuggingFace TTS", str, ()),
    ("Giọng đọc", "tts_device", "Thiết bị TTS", str, ("cpu", "cuda")),
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
    ("Hook trích văn bản", "hook_enabled", "Bật hook trích từ văn bản nguồn (không dùng AI)", bool, ()),
    ("Hook trích văn bản", "hook_theme", "Kiểu hiển thị hook", str, ("auto", "bold", "minimal")),
    ("Hook trích văn bản", "hook_seed", "Seed chọn câu nguồn", int, ()),
    ("Hook trích văn bản", "hook_duration", "Thời lượng hook (giây, 1–8)", float, ()),
)

CHOICE_LABELS = {
    "asr_provider": ASR_MODELS,
    "mode": {"subtitle_dub": "Phụ đề + Lồng tiếng", "subtitle": "Chỉ phụ đề", "dub": "Chỉ lồng tiếng"},
    "translation_provider": LLM_PROVIDERS,
    "vision_provider": LLM_PROVIDERS,
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
    "tts_speed": (
        "Hệ số tốc độ của giọng lồng tiếng và mẫu nghe thử (0.5–2.0). "
        "1.0 giữ nguyên tốc độ đọc. Không đổi tốc độ hình hoặc bản thu gốc."
    ),
    "translation_base_url": "Nhập URL gốc (gồm /v1 nếu dịch vụ yêu cầu), không thêm /chat/completions. "
                            "Không đặt khóa API trong URL. Chỉ dùng HTTP với máy chủ cục bộ hoặc đáng tin cậy.",
    "vision_model": "Chọn model hỗ trợ hình ảnh. Danh sách của dịch vụ có thể chứa model chỉ hỗ trợ văn bản.",
}


def _fetch_models(execution, results, provider, key, base_url):
    with execution.activate():
        try:
            models = list_llm_models(provider, key, base_url)
        except Cancelled:
            return
        except (ProviderError, ValueError) as exc:
            results.put(([], str(exc)))
        else:
            results.put((models, ""))


class _CatalogComboBox(QComboBox):
    """Scrollable catalog combo whose popup stays within the enclosing dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Fusion's menu popup ignores maxVisibleItems and overflows compact dialogs.
        self.setStyleSheet("QComboBox { combobox-popup: 0; }")
        self.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def showPopup(self):
        super().showPopup()
        popup = self.view().window()
        dialog = self.window()
        if popup is None or popup is self or dialog is self:
            return
        client = QRect(dialog.mapToGlobal(dialog.rect().topLeft()), dialog.rect().size())
        rect = popup.geometry()
        if client.isEmpty() or rect.isEmpty():
            return
        width = min(rect.width(), client.width())
        height = min(rect.height(), client.height())
        client_left, client_top = client.x(), client.y()
        client_right, client_bottom = client_left + client.width(), client_top + client.height()
        picker_top = self.mapToGlobal(self.rect().topLeft()).y()
        below_y = picker_top + self.height()
        above_y = picker_top - height
        if below_y + height <= client_bottom:
            y = max(client_top, below_y)
        elif above_y >= client_top:
            y = min(above_y, client_bottom - height)
        else:
            y = max(client_top, min(below_y, client_bottom - height))
        x = max(client_left, min(rect.x(), client_right - width))
        popup.setGeometry(x, y, width, height)


class SettingsDialog(QDialog):
    """Modal editor; on_saved() runs only after validation and persistence succeed."""

    def __init__(self, parent, settings, on_saved=None, *, api_provider=None, preset_cache=None):
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
            "Bấm Lưu cài đặt để áp dụng thay đổi, hoặc Hủy để giữ nguyên.")
        tabs = QTabWidget()
        tabs.setUsesScrollButtons(True)
        layout.addWidget(tabs, 1)

        forms = {}
        self.editors = {}
        self._model_pickers = {}
        existing = {field.name for field in fields(settings)}
        for group, name, label, value_type, choices in FIELD_REGISTRY:
            if name not in existing:
                continue
            if group not in forms:
                page = QWidget()
                scroll = QScrollArea(widgetResizable=True)
                scroll.setFrameShape(QFrame.Shape.NoFrame)
                scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                scroll.setWidget(page)
                form = QFormLayout(page)
                form.setContentsMargins(12, 12, 12, 12)
                form.setHorizontalSpacing(16)
                form.setVerticalSpacing(10)
                form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                forms[group] = form
                if group == API_TAB:
                    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                    form.setVerticalSpacing(7)
                    self.api_page = scroll
                    self._section(form, "Dịch văn bản", "Dịch vụ này nhận văn bản nhận dạng từ video.")
                tabs.addTab(scroll, group.replace('&', '&&'))

            if name == "vision_provider":
                self._build_keys(forms[group], settings, api_provider)
                self._section(forms[group], "Nhận diện phụ đề cũ",
                              "Chế độ tự động trong Phụ đề cũ gửi 3 khung hình tới dịch vụ này.")
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
            elif name in CATALOG_FIELDS:
                editor = _CatalogComboBox()
                editor.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                editor.setMinimumContentsLength(10)
                if name in MODEL_FIELDS:
                    editor.setPlaceholderText("Chưa chọn model")
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
            if name in MODEL_FIELDS or name == "tts_voice":
                self._add_fetch_picker(forms[group], caption, name)
            else:
                forms[group].addRow(caption, editor)

        api_form = forms[API_TAB]
        provider = self.editors["translation_provider"]

        def update_translation_fields():
            compatible = provider.currentData() == "openai_compatible"
            api_form.setRowVisible(self.editors["translation_base_url"], compatible)

        provider.currentIndexChanged.connect(update_translation_fields)
        self.editors["translation_base_url"].setPlaceholderText("https://provider.example/v1")
        update_translation_fields()
        for name in ("translation_model", "vision_model"):
            self._connect_model_picker(name)
        self._connect_languages()
        self._connect_voices(preset_cache if preset_cache is not None else {})
        hint = QLabel("Video đã thêm giữ dịch vụ, model và Base URL đã lưu; khóa API được lấy khi chạy.",
                      wordWrap=True, objectName="muted")
        api_form.addRow(hint)
        if api_provider is not None:
            tabs.setCurrentWidget(self.api_page)
            key = self.key_fields.get(f"{api_provider}_api_key")
            if key:
                key[0].setFocus()
                QTimer.singleShot(0, self, lambda: self.api_page.ensureWidgetVisible(key[0]))
        self.tabs = tabs

        self.dirty = QLabel("Chưa có thay đổi", objectName="muted")
        self._saved_state = self._form_state()
        for editor in self.editors.values():
            if isinstance(editor, QCheckBox):
                editor.toggled.connect(self._refresh_dirty)
            elif isinstance(editor, QComboBox):
                editor.currentIndexChanged.connect(self._refresh_dirty)
            else:
                editor.textChanged.connect(self._refresh_dirty)
        for entry, delete in self.key_fields.values():
            entry.textChanged.connect(self._refresh_dirty)
            delete.toggled.connect(self._refresh_dirty)

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

    def _connect_languages(self):
        asr = self.editors["asr_provider"]
        source = self.editors["source_language"]
        target = self.editors["target_language"]

        def populate(combo, labels, selected):
            with QSignalBlocker(combo):
                combo.clear()
                for code, label in labels.items():
                    combo.addItem(label, code)
                if combo.findData(selected) < 0:
                    combo.addItem(selected, selected)
                combo.setCurrentIndex(combo.findData(selected))

        labels = {code: f"{LANGUAGE_LABELS[code]} ({code})"
                  for code in sorted(LANGUAGE_LABELS, key=LANGUAGE_LABELS.get)}

        def update_source(initial=False):
            provider = asr.currentData()
            choices = {"": "Tự nhận diện"}
            choices.update((code, label) for code, label in labels.items()
                           if provider == "local" or (provider == "qwen" and code in QWEN_LANGUAGES))
            selected = self.settings.source_language if initial else source.currentData() or ""
            populate(source, choices, selected if initial or selected in choices else "")
            source.setEnabled(provider != "firered")
            source.setToolTip("FireRed không nhận tùy chọn ngôn ngữ nguồn." if provider == "firered" else "")

        update_source(initial=True)
        asr.currentIndexChanged.connect(lambda _: update_source())
        previous = self.settings.target_language
        populate(target, labels, previous)
        target.addItem("Khác…")

        def choose_target(index):
            nonlocal previous
            selected = target.itemData(index)
            if selected is not None:
                previous = selected
                return
            value, ok = QInputDialog.getText(self, "Ngôn ngữ đích khác", "Nhập mã hoặc tên ngôn ngữ:")
            value = value.strip()
            with QSignalBlocker(target):
                if ok and value:
                    if target.findData(value) < 0:
                        target.insertItem(index, value, value)
                    previous = value
                target.setCurrentIndex(target.findData(previous))

        target.currentIndexChanged.connect(choose_target)

    def _connect_voices(self, cache):
        provider = self.editors["tts_provider"]
        preset = self.editors["tts_voice"]
        recording = self.editors["tts_custom_voice"]
        target = self.editors["target_language"]
        button, status = self._model_pickers["tts_voice"]
        timer = QTimer(self, interval=50)
        pending = None
        try:
            records = VoiceRegistry(self.settings.root, None).list()
        except VoiceError as exc:
            records = []
            recording.setStatusTip(str(exc))

        def draft():
            settings = copy.copy(self.settings)
            settings.tts_provider = provider.currentData()
            settings.tts_device = self.editors["tts_device"].currentData()
            for name in ("tts_model", "tts_python", "tts_hf_cache"):
                setattr(settings, name, self.editors[name].text())
            return settings

        def populate(combo, choices, selected):
            with QSignalBlocker(combo):
                combo.clear()
                for value, label in choices.items():
                    combo.addItem(label, value)
                if selected and combo.findData(selected) < 0:
                    combo.addItem(f"Đã lưu: {selected}" if combo is preset else "Bản thu đã lưu (không tìm thấy)", selected)
                combo.setCurrentIndex(combo.findData(selected))

        def show_presets(selected):
            service = provider.currentData()
            lang = (target.currentData() or "").lower().replace("_", "-")
            presets = cache.get(preset_key(draft()), ())

            def order(item):
                locale = item.get("locale", "").lower()
                matches = locale == lang or locale.startswith(lang + "-") or (
                    lang == "yue" and locale == "zh-hk")
                return not matches, (item.get("label") or item["id"]).casefold()

            choices = {"": "Mặc định VieNeu"} if service == "vieneu" else {}
            if service in ("edge", "vieneu"):
                for item in sorted(presets, key=order):
                    title = item.get("label") or item["id"]
                    choices[item["id"]] = f"{title} · {item['locale']}" if item.get("locale") else title
            populate(preset, choices, selected)
            preset.setPlaceholderText("Chưa chọn giọng" if service in ("edge", "vieneu") else "Không dùng preset")
            preset.setEnabled(service in ("edge", "vieneu"))
            preset.setToolTip("Giọng khớp ngôn ngữ đích đứng đầu danh sách." if service == "edge"
                              else "VieNeu dành cho tiếng Việt." if service == "vieneu" else "")

        def stop(*_):
            nonlocal pending
            if pending is not None:
                pending[0].cancel()
                pending = None
            timer.stop()
            button.setEnabled(provider.currentData() in ("edge", "vieneu"))
            button.setToolTip("Tải danh mục Edge qua mạng." if provider.currentData() == "edge"
                              else "Tải preset tiếng Việt từ model VieNeu đã cấu hình.")

        def update_provider():
            service = provider.currentData()
            saved = service == self.settings.tts_provider
            stop()
            show_presets(self.settings.tts_voice if saved else "")
            choices = {"": "Không dùng bản thu"}
            choices.update((item["id"], item["name"]) for item in records if item["provider"] == service)
            populate(recording, choices, self.settings.tts_custom_voice if saved else "")
            recording.setEnabled(service in ("vieneu", "omnivoice"))
            recording.setToolTip("Bản thu được chọn sẽ dùng thay giọng preset." if service in ("vieneu", "omnivoice")
                                 else "Chỉ dùng bản thu với VieNeu hoặc OmniVoice.")
            status.hide()

        def collect():
            if pending is None:
                return
            _, results, key = pending
            try:
                items, error = results.get_nowait()
            except Empty:
                return
            stop()
            if not error:
                cache[key] = items
                show_presets(preset.currentData() or "")
            status.setText(f"Không tải được giọng: {error}" if error else
                           f"Đã tải {len(items)} giọng." if items else "Không có giọng để chọn.")

        def fetch():
            nonlocal pending
            settings = draft()
            python, options = _preset_runtime(settings)
            execution, results = Execution(), SimpleQueue()

            def work():
                items, session = _load_presets(execution, python, options)
                if session is not None:
                    session.close()
                return items

            status.show()
            try:
                Thread(target=_voice_work, args=(work, results), daemon=True).start()
            except RuntimeError:
                status.setText("Không khởi động được tác vụ tải giọng.")
                return
            pending = execution, results, preset_key(settings)
            button.setEnabled(False)
            status.setText("Đang tải danh sách giọng…")
            timer.start()

        def runtime_changed(*_):
            if provider.currentData() == "vieneu":
                stop()
                show_presets(preset.currentData() or "")
                status.hide()

        update_provider()
        provider.currentIndexChanged.connect(lambda _: update_provider())
        for name in ("tts_model", "tts_python", "tts_hf_cache"):
            self.editors[name].textChanged.connect(runtime_changed)
        self.editors["tts_device"].currentIndexChanged.connect(runtime_changed)
        target.currentIndexChanged.connect(lambda _: show_presets(preset.currentData() or ""))
        button.clicked.connect(fetch)
        timer.timeout.connect(collect)
        self.finished.connect(stop)

    def _add_fetch_picker(self, form, caption, name):
        editor = self.editors[name]
        button = QPushButton("Tải giọng" if name == "tts_voice" else "Tải model", objectName=f"fetch_{name}")
        button.setAutoDefault(False)
        button.setAccessibleName(f"Tải danh sách {caption.text().lower()}")
        row = QHBoxLayout()
        row.addWidget(editor, 1)
        row.addWidget(button)
        form.addRow(caption, row)
        status = QLabel(objectName="muted", wordWrap=True, textFormat=Qt.TextFormat.PlainText)
        form.addRow(status)
        status.hide()
        self._model_pickers[name] = button, status

    def _connect_model_picker(self, name):
        kind = name.removesuffix("_model")
        provider = self.editors[f"{kind}_provider"]
        base = self.editors["translation_base_url"]
        editor = self.editors[name]
        button, status = self._model_pickers[name]
        timer = QTimer(self, interval=50)
        pending = None

        def context():
            service = provider.currentData()
            return service, base.text().strip().rstrip("/") if service == "openai_compatible" else ""

        original = current = context()

        def populate(models, selected):
            with QSignalBlocker(editor):
                editor.clear()
                if kind == "translation" and current[0] in ("openai", "gemini"):
                    editor.addItem("Mặc định của dịch vụ", "")
                if selected and selected not in models:
                    editor.addItem(selected, selected)
                for model in models:
                    editor.addItem(model, model)
                editor.setCurrentIndex(editor.findData(selected))

        def stop():
            nonlocal pending
            if pending is not None:
                pending[0].cancel()
                pending = None
            timer.stop()
            button.setEnabled(provider.currentData() in LLM_PROVIDERS)

        def reset(*_):
            nonlocal current
            updated = context()
            if updated == current:
                return
            stop()
            current = updated
            populate([], getattr(self.settings, name) if current == original else "")
            status.hide()

        def collect():
            if pending is None:
                return
            try:
                models, error = pending[1].get_nowait()
            except Empty:
                return
            stop()
            if models:
                populate(models, editor.currentData() or "")
            status.setText(error or (f"Đã tải {len(models)} model." if models else "Dịch vụ không trả về model nào."))

        def fetch():
            nonlocal pending
            service, url = current
            field = f"{service}_api_key"
            entry, delete = self.key_fields[field]
            key = "" if delete.isChecked() else entry.text().strip() or getattr(self.settings, field)
            execution, results = Execution(), SimpleQueue()
            try:
                Thread(target=_fetch_models, args=(execution, results, service, key, url), daemon=True).start()
            except RuntimeError:
                status.setText("Không khởi động được tác vụ tải model.")
            else:
                pending = execution, results
                button.setEnabled(False)
                status.setText("Đang tải danh sách model…")
                timer.start()
            status.show()

        def key_changed(service):
            if pending is not None and current[0] == service:
                stop()
                status.hide()

        populate([], getattr(self.settings, name))
        stop()
        provider.currentIndexChanged.connect(reset)
        if kind == "translation":
            base.textChanged.connect(reset)
        for field, (entry, delete) in self.key_fields.items():
            service = field.removesuffix("_api_key")
            entry.textChanged.connect(lambda _, service=service: key_changed(service))
            delete.toggled.connect(lambda _, service=service: key_changed(service))
        timer.timeout.connect(collect)
        button.clicked.connect(fetch)
        self.finished.connect(stop)

    def _build_keys(self, form, settings, api_provider):
        detail = "OpenAI và Gemini dùng chung khóa cho dịch và nhận diện ảnh. Khóa đã lưu không hiện lại."
        if api_provider in LLM_PROVIDERS:
            detail = f"Video này dùng {LLM_PROVIDERS[api_provider]}. Kiểm tra khóa và quyền truy cập."
        self._section(form, "Khóa API", detail)
        self.key_fields = {}
        for field in CREDENTIAL_FIELDS:
            name = LLM_PROVIDERS[field.removesuffix("_api_key")]
            saved = bool(getattr(settings, field))
            entry = QLineEdit(objectName=field)
            entry.setEchoMode(QLineEdit.EchoMode.Password)
            entry.setPlaceholderText("Đã lưu · nhập để thay" if saved else "Chưa có · nhập khóa API")
            delete = QCheckBox("Xóa khi lưu", objectName=f"remove_{field}")
            delete.setAccessibleName(f"Xóa khóa API {name} khi lưu")
            delete.setEnabled(saved)
            delete.toggled.connect(entry.setDisabled)
            row = QHBoxLayout()
            row.addWidget(entry, 1)
            row.addWidget(delete)
            caption = QLabel(name)
            caption.setBuddy(entry)
            entry.setAccessibleName(f"Khóa API {name}")
            form.addRow(caption, row)
            self.key_fields[field] = entry, delete

    @staticmethod
    def _section(form, title, detail):
        form.addRow(QLabel(title, objectName="section"))
        form.addRow(QLabel(detail, wordWrap=True, objectName="muted"))

    def _form_state(self):
        values = {}
        for name, editor in self.editors.items():
            if isinstance(editor, QCheckBox):
                values[name] = editor.isChecked()
            elif name in CATALOG_FIELDS:
                values[name] = editor.currentData() or ""
            elif isinstance(editor, QComboBox):
                values[name] = editor.currentIndex()
            else:
                values[name] = editor.text()
        return values

    def _refresh_dirty(self, *_):
        changed = self._form_state() != self._saved_state or any(
            entry.text().strip() or delete.isChecked() for entry, delete in self.key_fields.values())
        self.dirty.setText("Có thay đổi chưa lưu" if changed else "Chưa có thay đổi")

    def accept(self):
        candidate = copy.deepcopy(self.settings)
        changes = {name for name, value in self._form_state().items() if value != self._saved_state[name]}
        for field, (entry, delete) in self.key_fields.items():
            new_key = entry.text().strip()
            if delete.isChecked() or new_key:
                setattr(candidate, field, "" if delete.isChecked() else new_key)
                changes.add(field)
        try:
            for _, name, label, value_type, choices in FIELD_REGISTRY:
                if name not in changes:
                    continue
                editor = self.editors[name]
                try:
                    if value_type is bool:
                        value = editor.isChecked()
                    elif name in CATALOG_FIELDS:
                        value = editor.currentData() or ""
                    elif choices:
                        value = editor.currentData() if editor.currentIndex() >= 0 else editor.placeholderText()
                    else:
                        value = value_type(editor.text())
                except ValueError:
                    raise ValueError(f"{label}: giá trị không hợp lệ") from None
                setattr(candidate, name, value.strip() if name in LLM_FIELDS and isinstance(value, str) else value)
            if not LLM_FIELDS.isdisjoint(changes):
                try:
                    candidate.validate_llm()
                except ValueError as exc:
                    self.tabs.setCurrentWidget(self.api_page)
                    self._show_error(str(exc))
                    return
            # Rotating a key must not require unrelated local models or media tools.
            if not changes or changes.difference(LLM_FIELDS, CREDENTIAL_FIELDS):
                if candidate.asr_provider != self.settings.asr_provider or (
                    candidate.asr_provider == "firered" and candidate.firered_type != "aed"
                ):
                    candidate.asr_model = candidate.asr_aligner = candidate.asr_vad_model = ""
                    candidate.firered_type = "aed"
                candidate.validate()
            candidate.save()
        except CredentialError:
            text = "Không lưu được cấu hình an toàn. Cài đặt hiện tại được giữ nguyên."
            if not changes.isdisjoint(CREDENTIAL_FIELDS):
                text += " Hãy mở lại ứng dụng để kiểm tra trạng thái khóa đã lưu."
            self._show_error(text)
            return
        except (ValueError, OSError) as exc:
            self._show_error(str(exc))
            return

        for name in (*self.editors, *ASR_HIDDEN_FIELDS, *CREDENTIAL_FIELDS):
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
