"""Voice library for provider presets and consented recordings."""
import asyncio
import threading
import unicodedata
from functools import partial
from pathlib import Path
from queue import Empty, SimpleQueue

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QTabBar, QVBoxLayout, QWidget,
)

from .execution import Execution
from .media import MediaAdapter
from .tts import TtsSession
from .ui import ask_confirmation, page_header, show_message
from .voices import VoiceError, VoiceRegistry
from .voice_preview import play_preview, render_preview, stop_preview

PROVIDERS = {'edge': 'Edge', 'vieneu': 'VieNeu', 'omnivoice': 'OmniVoice'}
FILTERS = {'gender': 'Giới tính', 'region': 'Vùng', 'style': 'Phong cách'}
FAVORITES = 'voice_library_favorites'


def _search_text(text):
    text = unicodedata.normalize('NFD', text.casefold().replace('đ', 'd'))
    return ''.join(char for char in text if not unicodedata.combining(char))


def _plain_label(text='', **kwargs):
    return QLabel(text, textFormat=Qt.TextFormat.PlainText, **kwargs)


def _voice_work(work, results):
    try:
        result = work()
    except Exception as exc:
        results.put((None, str(exc) or type(exc).__name__))
    else:
        results.put((result, ''))


def _close_session(session):
    def close():
        try:
            session.close()
        except Exception:
            pass

    threading.Thread(target=close, daemon=True).start()


class _WarmSession:
    """Exclusive handoff of one loaded offline TTS session.

    The holder owns the idle session; a preview worker takes it for its one
    request and always gives it back, or closes it when the dialog already
    retired the holder. Closing never runs on the GUI thread.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._session = None
        self._key = None
        self._closed = False

    def offer(self, key, session):
        with self._lock:
            if self._closed or self._session is not None:
                return False
            self._key, self._session = key, session
            return True

    def take(self, key):
        with self._lock:
            if self._closed or self._key != key:
                return None
            session, self._session = self._session, None
            return session

    def restore(self, key, session):
        if getattr(session, 'process', True) is None:  # a failed request force-closed it
            return False
        with self._lock:
            if self._closed or self._session is not None:
                return False
            self._key, self._session = key, session
            return True

    def close_async(self):
        with self._lock:
            self._closed = True
            session, self._session = self._session, None
        if session is not None:
            _close_session(session)


def _provider(settings):
    provider = getattr(settings, "tts_provider", "vieneu")
    return provider if provider in ("vieneu", "omnivoice") else "vieneu"


def _preset_runtime(settings):
    return getattr(settings, 'tts_python', ''), dict(
        provider=getattr(settings, 'tts_provider', 'vieneu'), model_path=getattr(settings, 'tts_model', ''),
        hf_cache=getattr(settings, 'tts_hf_cache', ''), device=getattr(settings, 'tts_device', 'cpu'),
        license_accepted=getattr(settings, 'consent_omnivoice_license', False))


def preset_key(settings):
    python, options = _preset_runtime(settings)
    return settings.root, python, tuple(options.items())


def voice_description(settings, names, presets=(), *, speaker_map=None):
    provider = getattr(settings, 'tts_provider', 'vieneu')
    if getattr(settings, 'mode', '') == 'subtitle' or provider == 'none':
        return 'Không lồng tiếng'
    title = PROVIDERS.get(provider, provider)
    labels = {preset['id']: preset.get('label') or preset['id'] for preset in presets}
    voice = getattr(settings, 'tts_voice', '')
    custom = getattr(settings, 'tts_custom_voice', '')
    if getattr(settings, 'multi_speaker', False):
        assignments = ', '.join(f'{speaker}: {names.get((provider, value), labels.get(value, value))}'
                                for speaker, value in (speaker_map or {}).items()
                                if isinstance(value, str) and value.strip())
        return f'{title} · {assignments or "Chưa có ánh xạ giọng theo người nói"}'
    if custom and provider != 'edge':
        return f'{title} · {names.get((provider, custom), custom + " (không có trong thư viện của provider)")}'
    if provider == 'omnivoice':
        return f'{title} · Chưa chọn giọng thu'
    return f'{title} · {labels.get(voice, voice) or "Mặc định của bộ tạo giọng"}'


# Returns (presets, session); the caller owns the session and closes it off-GUI.
def _load_presets(execution, python, options):
    if options['provider'] == 'edge':
        from edge_tts import list_voices

        async def catalog():
            task = asyncio.create_task(list_voices())
            while not task.done():
                execution.check()
                await asyncio.wait({task}, timeout=0.1)
            return task.result()

        return [{'id': item['ShortName'], 'label': item.get('FriendlyName') or item['ShortName'],
                 'gender': {'Male': 'Nam', 'Female': 'Nữ'}.get(item.get('Gender'), ''),
                 'style': item.get('VoiceTag', {}).get('VoicePersonalities', []),
                 'locale': item['Locale']}
                for item in asyncio.run(catalog()) if item.get('Locale') == 'vi-VN'], None
    with execution.activate():
        session = TtsSession(python, **options)
        try:
            presets = [item for item in session.voices()
                       if isinstance(item, dict) and isinstance(item.get("id"), str)]
        except BaseException:
            session.close()
            raise
    return presets, session


class VoicesDialog(QDialog):
    def __init__(self, parent, settings, *, preset_cache=None, deletion_blocker=None):
        super().__init__(parent)
        self.settings = settings
        self.provider = getattr(settings, 'tts_provider', 'vieneu')
        self.deletion_blocker = deletion_blocker
        self.setWindowTitle('Thư viện giọng nói')
        area = self.screen().availableGeometry()
        self.setMinimumSize(390, 300)
        self.resize(min(880, area.width() - 24), min(630, area.height() - 42))
        self.media = MediaAdapter(settings.ffmpeg, settings.ffprobe)
        self.registry = VoiceRegistry(settings.root, self.media)
        self.records = []
        self.preset_cache = preset_cache if preset_cache is not None else {}
        self._preset_key = preset_key(settings)
        self.presets = self.preset_cache.get(self._preset_key, [])
        self._busy = False
        self._workers = []
        self._worker_timer = QTimer(self, interval=30)
        self._worker_timer.timeout.connect(self._collect_workers)
        self._preset_execution = None
        self._warm = _WarmSession()
        self._loading_key = None
        self.pending = None
        self.cards = {}
        self.entries = {}
        self.visible_keys = []
        self._columns = 0
        self._preview_execution = None
        self._preview_cache = {}
        self._playing = False
        extra = getattr(settings, 'extra', {})
        saved = extra.get(FAVORITES, [])
        self.favorites = {tuple(key) for key in saved if isinstance(key, list) and len(key) == 3
                          and all(isinstance(value, str) for value in key)} if isinstance(saved, list) else set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        page_header(layout, 'Thư viện giọng nói')
        self.current_voice = _plain_label(wordWrap=True, objectName='muted')
        layout.addWidget(self.current_voice)

        toolbar = QHBoxLayout()
        self.search = QLineEdit(placeholderText='Tìm giọng, bộ tạo giọng…', clearButtonEnabled=True)
        self.search.setAccessibleName('Tìm giọng')
        toolbar.addWidget(self.search, 1)
        self.add_button = QPushButton('Thêm giọng thu…')
        self.add_button.clicked.connect(self.add)
        toolbar.addWidget(self.add_button)
        layout.addLayout(toolbar)

        self.tabs = QTabBar(expanding=False, usesScrollButtons=True)
        for title in ('Giọng có sẵn', 'Giọng của tôi', 'Yêu thích'):
            self.tabs.addTab(title)
        if getattr(settings, 'tts_custom_voice', '') or self.provider in ('omnivoice', 'none'):
            self.tabs.setCurrentIndex(1)
        layout.addWidget(self.tabs)

        self.filter_bar = QWidget()
        self.filter_layout = QGridLayout(self.filter_bar)
        self.filter_layout.setContentsMargins(0, 0, 0, 0)
        self.filters = {field: QComboBox() for field in FILTERS}
        for field, combo in self.filters.items():
            combo.setAccessibleName(FILTERS[field])
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.currentIndexChanged.connect(self._filter)
        layout.addWidget(self.filter_bar)

        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.grid = QGridLayout(content)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.grid.setContentsMargins(8, 8, 8, 8)
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        self.empty = _plain_label(wordWrap=True, alignment=Qt.AlignmentFlag.AlignCenter)

        self.preset_status = _plain_label(wordWrap=True, objectName='muted')
        self.status = _plain_label(wordWrap=True, objectName='muted')
        self.result_count = _plain_label(objectName='muted')
        for label in (self.preset_status, self.status, self.result_count):
            layout.addWidget(label)
        actions = QHBoxLayout()
        self.load_presets_button = QPushButton('Tải danh mục (dùng mạng)' if self.provider == 'edge' else 'Tải giọng có sẵn')
        self.load_presets_button.clicked.connect(self._load_presets)
        actions.addWidget(self.load_presets_button)
        self.clear_button = QPushButton('Xóa bộ lọc')
        self.clear_button.clicked.connect(self._clear_filters)
        actions.addWidget(self.clear_button)
        self.stop_button = QPushButton('Dừng nghe')
        self.stop_button.clicked.connect(self._stop_preview)
        self.stop_button.hide()
        actions.addWidget(self.stop_button)
        actions.addStretch()
        self.select_button = QPushButton('Sử dụng giọng này', default=True)
        self.select_button.setProperty('variant', 'primary')
        self.select_button.clicked.connect(self.select)
        self.close_button = QPushButton('Hủy')
        self.close_button.clicked.connect(self.reject)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(self.close_button)
        footer.addWidget(self.select_button)
        layout.addLayout(actions)
        layout.addLayout(footer)
        for button in (self.add_button, self.load_presets_button, self.clear_button, self.stop_button, self.close_button):
            button.setAutoDefault(False)

        try:
            self.records = self.registry.list()
        except VoiceError as exc:
            self.status.setText(str(exc))
        self.tabs.currentChanged.connect(self._refresh_filters)
        self.search.textChanged.connect(self._filter)
        self._render()
        if self._preset_key in self.preset_cache:
            self._show_presets_loaded()

    def _load_presets(self):
        if self._busy or self._preset_execution is not None or self._preset_key in self.preset_cache:
            return
        python, options = _preset_runtime(self.settings)
        execution = self._preset_execution = Execution()
        warm, warm_key = self._warm, self._preview_key()
        self.load_presets_button.setEnabled(False)
        self.preset_status.setText("Đang khởi động bộ tạo giọng để tải preset… Bạn vẫn có thể dùng giọng thu hoặc đóng cửa sổ.")
        self.preset_status.show()

        def work():
            presets, session = _load_presets(execution, python, options)
            # Handing the session over inside the worker keeps it from leaking
            # when this dialog is gone and its completion callback is dropped.
            if session is not None and not warm.offer(warm_key, session):
                _close_session(session)
            return presets

        self._run_worker(work, self._presets_loaded)

    def _set_busy(self, busy):
        self._busy = busy
        self.scroll.setEnabled(not busy)
        self.add_button.setEnabled(not busy)
        self.close_button.setEnabled(not busy)
        self.load_presets_button.setEnabled(not busy and self._preset_execution is None)
        self._sync_selection()

    def _start(self, work, completion, status):
        self._set_busy(True)
        self.status.setText(status)
        self._completion = completion
        self._run_worker(work, self._work_finished)

    def _run_worker(self, work, finished):
        results = SimpleQueue()
        try:
            threading.Thread(target=_voice_work, args=(work, results), daemon=True).start()
        except RuntimeError as exc:
            finished(None, str(exc))
            return
        self._workers.append((results, finished))
        self._worker_timer.start()

    def _collect_workers(self):
        # Workers only publish Python data; the dialog owns all completion callbacks.
        for entry in self._workers[:]:
            results, finished = entry
            try:
                result, error = results.get_nowait()
            except Empty:
                continue
            self._workers.remove(entry)
            finished(result, error)
        if not self._workers:
            self._worker_timer.stop()

    def _work_finished(self, result, error):
        completion = self._completion
        self._completion = None
        try:
            completion(result, error)
        finally:
            self._set_busy(False)

    def done(self, result):
        if not self._busy:
            self._stop_preview()
            self._preview_cache.clear()
            if self._preset_execution is not None:
                self._preset_execution.cancel()
            self._warm.close_async()
            super().done(result)

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
        else:
            super().closeEvent(event)

    def _presets_loaded(self, presets, error):
        execution, self._preset_execution = self._preset_execution, None
        if execution.cancelled:
            return
        if error:
            self.preset_status.setText(f"Không tải được preset: {error}")
            self.preset_status.setVisible(self.tabs.currentIndex() == 0)
            self.load_presets_button.setEnabled(not self._busy)
            return
        self.presets = self.preset_cache[self._preset_key] = presets
        self._render()
        self._show_presets_loaded()

    def _show_presets_loaded(self):
        self.load_presets_button.hide()
        self.preset_status.setText('Đã tải giọng preset.' if self.presets else 'Không có giọng preset.')
        self.preset_status.setVisible(self.tabs.currentIndex() == 0)

    def _preview_key(self):
        return (self._preset_key, self.provider)

    def _show_current_voice(self):
        names = {(item['provider'], item['id']): item['name'] for item in self.records}
        self.current_voice.setText('Giọng mặc định: ' + voice_description(self.settings, names, self.presets))

    def _default_key(self):
        custom = getattr(self.settings, 'tts_custom_voice', '')
        if custom and self.provider in ('vieneu', 'omnivoice'):
            return self.provider, 'recording', custom
        return self.provider, 'preset', getattr(self.settings, 'tts_voice', '')

    def _render(self):
        self.entries = {(rec['provider'], 'recording', rec['id']): rec for rec in self.records}
        self.entries.update({(self.provider, 'preset', item['id']): item for item in self.presets})
        for key in self.cards.keys() - self.entries.keys():
            card = self.cards.pop(key)
            self.grid.removeWidget(card)
            card.hide()
            card.setParent(None)
            card.deleteLater()
        for key, entry in self.entries.items():
            if key not in self.cards:
                self.cards[key] = self._card(key, entry)
        self._show_current_voice()
        self._refresh_filters()

    def _card(self, key, entry):
        provider, kind, voice = key
        name = entry.get('name') or entry.get('label') or voice
        card = QFrame(objectName='voiceCard')
        box = QVBoxLayout(card)
        box.setContentsMargins(10, 8, 10, 8)
        title = QPushButton(name, checkable=True, autoDefault=False, objectName='voiceTitle')
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        title.setToolTip(f'{name}\n{voice}')
        title.clicked.connect(lambda: self._choose(key))
        box.addWidget(title)
        metadata = [value for field in FILTERS for value in self._values(entry, field)]
        description = ' · '.join([PROVIDERS.get(provider, provider), *metadata])
        detail = _plain_label(description, wordWrap=True, objectName='muted')
        detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        box.addWidget(detail)
        badge = _plain_label(objectName='voiceBadge')
        box.addWidget(badge)
        row = QHBoxLayout()
        star = QPushButton('☆', checkable=True, autoDefault=False)
        star.clicked.connect(lambda: self._favorite(key))
        row.addWidget(star)
        if kind == 'recording':
            self._card_action(row, 'Bản thu', 'Nghe bản thu gốc', lambda: self._preview(key, True))
        if provider == self.provider:
            label = 'Nghe thử · mạng' if provider == 'edge' else 'Nghe thử'
            self._card_action(row, label, 'Đọc câu mẫu bằng giọng này', lambda: self._preview(key))
        if kind == 'recording':
            delete = self._card_action(row, 'Xóa', 'Xóa bản thu', lambda: self.remove(key))
            delete.setProperty('variant', 'danger')
        row.addStretch()
        box.addLayout(row)
        card.controls = title, badge, star
        return card

    @staticmethod
    def _card_action(row, text, tooltip, action):
        control = QPushButton(text, autoDefault=False)
        control.setToolTip(tooltip)
        control.clicked.connect(action)
        row.addWidget(control)
        return control

    @staticmethod
    def _values(entry, field):
        value = entry.get(field)
        return [value] if isinstance(value, str) and value else value if isinstance(value, list) else []

    def _tab_keys(self):
        tab = self.tabs.currentIndex()
        return [key for key in self.entries if (key[1] == 'preset' if tab == 0 else
                key[1] == 'recording' if tab == 1 else key in self.favorites)]

    def _refresh_filters(self, *_):
        keys = self._tab_keys()
        for field, combo in self.filters.items():
            previous = combo.currentData()
            values = sorted({value for key in keys for value in self._values(self.entries[key], field)})
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(FILTERS[field] + ': tất cả')
            for value in values:
                combo.addItem(value, value)
            combo.setCurrentIndex(max(0, combo.findData(previous)))
            combo.blockSignals(False)
            combo.setVisible(bool(values))
        self.filter_bar.setVisible(any(not combo.isHidden() for combo in self.filters.values()))
        on_catalog = self.tabs.currentIndex() == 0
        self.load_presets_button.setVisible(on_catalog and self.provider in ('edge', 'vieneu')
                                           and self._preset_key not in self.preset_cache)
        self.preset_status.setVisible(on_catalog and bool(self.preset_status.text()))
        self._filter()

    def _clear_filters(self):
        self.search.clear()
        for combo in self.filters.values():
            combo.setCurrentIndex(0)

    def _filter(self, *_):
        query = _search_text(self.search.text()).split()
        keys = self._tab_keys()
        self.visible_keys = []
        for key in keys:
            entry = self.entries[key]
            haystack = _search_text(' '.join([*key, PROVIDERS.get(key[0], key[0]),
                *(entry.get(field, '') for field in ('label', 'name', 'locale')),
                *(value for field in FILTERS for value in self._values(entry, field))]))
            if all(word in haystack for word in query) and all(
                    not combo.currentData() or combo.currentData() in self._values(entry, field)
                    for field, combo in self.filters.items()):
                self.visible_keys.append(key)
        filtered = bool(query or any(combo.currentData() for combo in self.filters.values()))
        self.clear_button.setVisible(filtered)
        self.result_count.setText(f'{len(self.visible_keys)} / {len(keys)} giọng'
                                  + (' · Chưa có bộ lọc metadata cho mục này' if keys and self.filter_bar.isHidden() else ''))
        tab = self.tabs.currentIndex()
        self.empty.setText('Không tìm thấy giọng phù hợp.' if filtered else (
            'Chưa có giọng yêu thích.' if tab == 2 else 'Chưa có bản thu. Bấm Thêm giọng thu để bắt đầu.' if tab == 1 else
            'OmniVoice không có preset. Chọn hoặc thêm bản thu.' if self.provider == 'omnivoice' else
            'Chế độ hiện tại không lồng tiếng.' if self.provider == 'none' else
            'Tải danh mục để xem giọng có sẵn.' if self._preset_key not in self.preset_cache else
            'Danh mục này không có giọng phù hợp.'))
        self._relayout()
        self._sync_selection()

    def _relayout(self):
        self._columns = 2 if self.scroll.width() >= 740 else 1
        for card in self.cards.values():
            self.grid.removeWidget(card)
            card.hide()
        self.grid.removeWidget(self.empty)
        self.empty.setVisible(not self.visible_keys)
        if not self.visible_keys:
            self.grid.addWidget(self.empty, 0, 0, 1, self._columns)
        for index, key in enumerate(self.visible_keys):
            card = self.cards[key]
            self.grid.addWidget(card, *divmod(index, self._columns))
            card.show()
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, int(self._columns == 2))
        for index, combo in enumerate(self.filters.values()):
            self.filter_layout.addWidget(combo, *divmod(index, 3 if self._columns == 2 else 2))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'scroll') and self._columns != (2 if self.scroll.width() >= 740 else 1):
            self._relayout()

    def _choose(self, key):
        if key[0] != self.provider:
            self.status.setText(f'Chọn {PROVIDERS.get(key[0], key[0])} trong Cài đặt để dùng giọng này.')
        else:
            self.pending = key
            name = self.entries[key].get('name') or self.entries[key].get('label') or key[2]
            self.status.setText(f'Đang chọn: {name}. Bấm Sử dụng giọng này để lưu.')
        self._sync_selection()

    def _sync_selection(self):
        default = self._default_key()
        self.select_button.setEnabled(not self._busy and self.pending in self.entries and self.pending != default)
        for key, card in self.cards.items():
            title, badge, star = card.controls
            title.setChecked(key == self.pending)
            badge.setText('Đang tải…' if key == self._loading_key else
                          'Mặc định' if key == default else 'Đang chọn' if key == self.pending else '')
            badge.setVisible(bool(badge.text()))
            favored = key in self.favorites
            star.setChecked(favored)
            star.setText('★' if favored else '☆')
            action = 'Bỏ yêu thích' if favored else 'Thêm vào yêu thích'
            star.setToolTip(action)
            star.setAccessibleName(f'{action}: {title.text()}')

    def _favorite(self, key):
        updated = self.favorites ^ {key}
        extra = self.settings.extra
        previous = extra.get(FAVORITES)
        extra[FAVORITES] = [list(item) for item in sorted(updated)]
        try:
            self.settings.save()
        except Exception as exc:
            if previous is None:
                extra.pop(FAVORITES, None)
            else:
                extra[FAVORITES] = previous
            self.status.setText(f'Không lưu được yêu thích: {exc}')
        else:
            self.favorites = updated
            self.status.setText('Đã lưu thay đổi yêu thích.')
        if self.tabs.currentIndex() == 2:
            self._refresh_filters()
        else:
            self._sync_selection()

    def add(self):
        if self._busy:
            return
        self._set_busy(True)
        try:
            work = self._pick_voice()
        finally:
            self._set_busy(False)
        if work is not None:
            self._start(work, self._added, "Đang chuẩn hóa giọng…")

    def _pick_voice(self):
        audio, _ = QFileDialog.getOpenFileName(self, "Chọn đoạn thu âm", "",
                                              "Audio (*.wav *.mp3 *.m4a *.flac);;All (*)")
        if not audio:
            return
        name, accepted = QInputDialog.getText(self, "Tên giọng", "Tên giọng:")
        if not accepted or not name:
            return
        provider = _provider(self.settings)
        ref_text, accepted = QInputDialog.getText(
            self, "Văn bản tham chiếu", "Văn bản đúng của đoạn thu (bắt buộc với OmniVoice):")
        if not self._consent_dialog():
            return
        return partial(self.registry.add, name, Path(audio), provider, ref_text if accepted else "", True)

    def _consent_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Xác nhận quyền sử dụng")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Quyền sử dụng giọng nói"))
        info = QLabel("Chỉ thêm bản ghi khi bạn là chủ giọng hoặc đã được chủ giọng cho phép.")
        info.setWordWrap(True)
        layout.addWidget(info)
        consent = QCheckBox("Tôi là chủ giọng hoặc có sự đồng ý của chủ giọng")
        layout.addWidget(consent)
        buttons = QDialogButtonBox()
        proceed = buttons.addButton("Tiếp tục", QDialogButtonBox.ButtonRole.AcceptRole)
        proceed.setEnabled(False)
        buttons.addButton("Hủy", QDialogButtonBox.ButtonRole.RejectRole)
        consent.toggled.connect(proceed.setEnabled)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted and consent.isChecked()
        dialog.deleteLater()
        return accepted

    def _added(self, record, error):
        if error:
            show_message(self, "Giọng nói", error)
            self.status.setText(error)
            return
        self.records.append(record)
        self._render()
        self.tabs.setCurrentIndex(1)
        self.status.setText('Đã thêm bản thu. Chọn giọng rồi bấm Sử dụng giọng này để lưu.')

    def select(self):
        if not self.select_button.isEnabled():
            return
        _, kind, voice = self.pending
        previous = {key: getattr(self.settings, key, '') for key in ('tts_voice', 'tts_custom_voice')}
        self.settings.tts_custom_voice = voice if kind == 'recording' else ''
        if kind == 'preset':
            self.settings.tts_voice = voice
        try:
            self.settings.save()
        except Exception as exc:
            for key, value in previous.items():
                setattr(self.settings, key, value)
            self.status.setText(f'Không lưu được giọng mặc định: {exc}')
            return
        self._show_current_voice()
        self.accept()

    def remove(self, key):
        if self._busy:
            return
        record = self.entries[key]
        if getattr(self.settings, 'tts_custom_voice', '') == record['id']:
            self.status.setText('Hãy áp dụng một giọng tương thích khác trước khi xóa giọng mặc định.')
            return
        reason = self.deletion_blocker(record) if self.deletion_blocker else ''
        if reason:
            self.status.setText(reason)
            return
        if not ask_confirmation(self, 'Xóa giọng', f"Xóa {record['name']}?"):
            return
        self._stop_preview()
        try:
            self.registry.remove(record['id'])
        except (VoiceError, KeyError, OSError) as exc:
            self.status.setText(f'Không xóa được bản thu: {exc}')
            return
        if key in self.favorites:
            self._favorite(key)
        if self.pending == key:
            self.pending = None
        self.records = [item for item in self.records if item['id'] != record['id']]
        self._render()
        self.status.setText('Đã xóa bản thu.')

    def _preview(self, key, recording=False):
        self._stop_preview()
        provider, kind, voice = key
        name = self.entries[key].get('name') or self.entries[key].get('label') or voice
        registry, media, warm = self.registry, self.media, self._warm
        warm_key = self._preview_key()
        python, options = _preset_runtime(self.settings)
        execution = self._preview_execution = Execution()
        self.stop_button.show()
        self.status.setText(f'Đang chuẩn bị âm thanh: {name}…')
        cache_key = key, recording
        cached = self._preview_cache.get(cache_key)
        self._loading_key = None if cached is not None else key
        self._sync_selection()

        def work():
            session = None
            try:
                with execution.activate():
                    execution.check()
                    reference = registry.resolve(voice) if kind == 'recording' else None
                    if cached is not None:
                        wav = cached
                    else:
                        session = (warm.take(warm_key)
                                   if provider != 'edge' and not recording else None)
                        wav = render_preview(provider, voice, reference, python, options, media,
                                             recording=recording, session=session)
                return execution, cache_key, name, wav, ''
            except Exception as exc:
                return execution, cache_key, name, None, str(exc) or type(exc).__name__
            finally:
                # A retired holder (dialog closed) refuses it: close it here.
                if session is not None and not warm.restore(warm_key, session):
                    _close_session(session)

        self._run_worker(work, self._preview_finished)

    def _preview_finished(self, result, error):
        if result is not None:
            execution, cache_key, name, wav, error = result
            if execution is not self._preview_execution:
                return
        self._preview_execution = None
        self._loading_key = None
        self._sync_selection()
        if error:
            self.status.setText(f'Không nghe được: {error}')
            self.stop_button.hide()
            return
        try:
            play_preview(wav)
        except (RuntimeError, OSError) as exc:
            self.status.setText(f'Không phát được âm thanh: {exc}')
            self.stop_button.hide()
            return
        if cache_key not in self._preview_cache and len(self._preview_cache) >= 8:
            del self._preview_cache[next(iter(self._preview_cache))]
        self._preview_cache[cache_key] = wav
        self._playing = True
        self.status.setText(f'Đã phát mẫu: {name}.')

    def _stop_preview(self):
        if self._preview_execution is not None:
            self._preview_execution.cancel()
            self._preview_execution = None
        if self._loading_key is not None:
            self._loading_key = None
            self._sync_selection()
        if self._playing:
            stop_preview()
            self._playing = False
        self.stop_button.hide()
