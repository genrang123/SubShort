from html import escape
from pathlib import Path
from uuid import uuid4
from PySide6.QtCore import QItemSelectionModel, QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QScrollArea, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)
from .models import Settings, Job
from .queue import CLAIMABLE_STATUSES, JobQueue, PENDING_STATUSES, RETRYABLE_STATUSES, QueueError
from .diarization import review_fingerprint, validate_turns
import json
import os
import subprocess
from .providers import ProviderError, Segment
from .batch import Batch
import queue as messages
import time
from collections import deque
from .credentials import CredentialError
from .pipeline import Pipeline
from .ui import apply_theme, ask_confirmation, page_header, show_message
from .queue_view import build_workspace, button, job_cells, label, tip
from .voices import VoiceError, VoiceRegistry
from .voices_dialog import VoicesDialog, preset_key, voice_description


def run_dialog(dialog):
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.exec()


class App(QMainWindow):
    def __init__(self, root: Path):
        super().__init__()
        self.setWindowTitle("SubShort · Video Studio")
        area = self.screen().availableGeometry().size()
        self.resize(min(1240, area.width() - 32), min(780, area.height() - 48))
        self.setMinimumSize(550, 300)
        self.settings = Settings.load(root)
        self.preset_cache = {}
        self.load_voice_names()
        self.queue = JobQueue(root / "queue_history.json")
        self.events = messages.SimpleQueue()
        self.job_events = deque(maxlen=1000)
        self.log_lines = deque(maxlen=1000)
        self.log_last = ""
        self.snapshot = {}
        self.worker_state = (False, False)
        self.stopping = False
        self.latest_message = ''
        self.batch = Batch(self.settings, self.queue, Pipeline, self.notify)
        from .upload import UploadManager
        self.upload = UploadManager(self.settings, self.queue, self.notify)
        self.closing = False
        build_workspace(self)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start(100)
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'adapt_workspace'):
            self.adapt_workspace()

    def job_id_at(self, row):
        item = self.jobs.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def current_job(self):
        selected = self.selected_jobs()
        current = self.job_id_at(self.jobs.currentRow())
        return self.snapshot.get(current if current in selected else (selected[0] if selected else None))

    def can_mutate(self):
        return not (self.batch.running or self.upload.running or self.closing or self.queue.load_error)

    def sync_actions(self, *_):
        writable = self.can_mutate()
        selected = [self.snapshot[job_id] for job_id in self.selected_jobs() if job_id in self.snapshot]
        job = self.current_job()
        for control in (self.add_button, self.urls_button, self.empty_add, self.empty_urls):
            control.setEnabled(writable)
        self.start_button.setEnabled(writable and any(j.status in PENDING_STATUSES for j in self.snapshot.values()))
        self.cancel_button.setEnabled(not self.stopping and not self.closing and any(self.worker_state))
        self.cancel_button.setText('Đang dừng…' if self.stopping else 'Dừng xử lý / tải lên')
        self.retry_button.setEnabled(writable and any(j.status in RETRYABLE_STATUSES for j in selected))
        self.remove_button.setEnabled(writable and bool(selected) and all(j.status != 'running' for j in selected))
        self.upload_button.setEnabled(not self.closing and not self.queue.load_error)
        self.open_button.setEnabled(bool(job and job.output) and not self.closing)
        self.details_button.setEnabled(job is not None)
        if job is None:
            self.details.hide()
        elif not self.details.isHidden():
            events = [(stage, line) for jid, stage, line in self.job_events if jid == job.id]
            self.details.render(job, [line for _, line in events], [stage for stage, _ in events if stage],
                                writable, self.describe_voice(job))
        self.details_button.setText('Chi tiết' if self.details.isHidden() else 'Ẩn chi tiết')

    def toggle_details(self):
        self.details.setVisible(self.details.isHidden() and self.current_job() is not None)
        self.sync_actions()

    def queue_menu(self, point):
        row = self.jobs.indexAt(point).row()
        job = self.snapshot.get(self.job_id_at(row))
        if job is None:
            return
        menu = QMenu(self)
        details = menu.addAction('Chi tiết')
        folder = menu.addAction('Hiện file kết quả trong Explorer')
        folder.setEnabled(bool(job.output))
        retry = menu.addAction('Thử lại video này')
        retry.setEnabled(self.can_mutate() and job.status in RETRYABLE_STATUSES)
        review = menu.addAction('Duyệt transcript / speaker')
        review.setEnabled(self.can_mutate() and bool(job.metadata.get('review_transcript')))
        chosen = menu.exec(self.jobs.viewport().mapToGlobal(point))
        menu.deleteLater()
        if chosen == details:
            flags = QItemSelectionModel.SelectionFlag
            row = next((i for i in range(self.jobs.rowCount()) if self.job_id_at(i) == job.id), -1)
            if row == -1 or self.jobs.isRowHidden(row):
                return
            self.jobs.setCurrentCell(row, 0, flags.Select | flags.Rows)
            self.details.show()
            self.sync_actions()
        elif chosen == folder:
            self.open_output(job.id)
        elif chosen == retry:
            self.retry_jobs([job.id])
        elif chosen == review:
            self.review_speaker(job.id)

    def selected_jobs(self):
        return [self.job_id_at(index.row())
                for index in sorted(self.jobs.selectionModel().selectedRows(), key=lambda index: index.row())
                if not self.jobs.isRowHidden(index.row())]

    def filter_queue(self):
        completed = self.queue_tabs.currentIndex() == 1
        has_rows = False
        flags = QItemSelectionModel.SelectionFlag
        selection = self.jobs.selectionModel()
        with QSignalBlocker(self.jobs), QSignalBlocker(selection):
            for row, job in enumerate(self.snapshot.values()):
                visible = (job.status == 'done') == completed
                if self.jobs.isRowHidden(row) == visible:
                    self.jobs.setRowHidden(row, not visible)
                if visible:
                    has_rows = True
                elif selection.isRowSelected(row):
                    selection.select(self.jobs.model().index(row, 0), flags.Deselect | flags.Rows)
            if self.jobs.currentRow() >= 0 and self.jobs.isRowHidden(self.jobs.currentRow()):
                selection.clearCurrentIndex()
        self.queue_stack.setCurrentWidget(self.jobs if has_rows else self.empty_queue)
        self.empty_add.setVisible(not completed)
        self.empty_urls.setVisible(not completed)
        self.empty_title.setText('Không đọc được hàng đợi' if self.queue.load_error else
                                 'Chưa có video hoàn tất' if completed else 'Chưa có video trong hàng đợi')
        self.empty_hint.setText(self.queue.load_error or ('Video đã xử lý xong sẽ xuất hiện ở đây.' if completed else
                                                         'Thêm video trên máy hoặc dán URL để bắt đầu.'))
        self.sync_actions()

    def refresh(self):
        snapshot = self.queue.snapshot()
        old_ids = list(self.snapshot)
        self.snapshot = {job.id: job for job in snapshot}
        removed_rows = [row for row, job_id in enumerate(old_ids) if job_id not in self.snapshot]
        current = self.job_id_at(self.jobs.currentRow())
        flags = QItemSelectionModel.SelectionFlag
        with QSignalBlocker(self.jobs), QSignalBlocker(self.jobs.selectionModel()):
            for row in reversed(removed_rows):
                self.jobs.removeRow(row)
            self.jobs.setRowCount(len(snapshot))
            for row, job in enumerate(snapshot):
                for column, (text, tooltip, value) in enumerate(job_cells(job)):
                    item = self.jobs.item(row, column)
                    if item is None:
                        item = QTableWidgetItem()
                        self.jobs.setItem(row, column, item)
                    if item.text() != text:
                        item.setText(text)
                    if item.toolTip() != tooltip:
                        item.setToolTip(tooltip)
                    if item.data(Qt.ItemDataRole.UserRole) != value:
                        item.setData(Qt.ItemDataRole.UserRole, value)
                if current and job.id == current and self.jobs.currentRow() != row:
                    self.jobs.setCurrentCell(row, 0, flags.NoUpdate)
        voice = 'Giọng mặc định: ' + self.describe_voice()
        self.default_voice.setText(voice)
        self.default_voice.setToolTip(tip(voice))
        counts = {key: 0 for key in self.counts}
        counts['total'] = len(snapshot)
        for job in snapshot:
            if job.status in counts and job.status != 'total':
                counts[job.status] += 1
        for key, (widget, title) in self.counts.items():
            widget.setText(f'{title}  {counts[key]}')
        self.queue_tabs.setTabText(0, f'Hàng đợi ({len(snapshot) - counts["done"]})')
        self.queue_tabs.setTabText(1, f'Đã hoàn tất ({counts["done"]})')
        self.worker_state = (self.batch.running, self.upload.running)
        if not any(self.worker_state):
            self.stopping = False
        status = ('Đang dừng…' if self.stopping or self.closing else
                  f'Đang xử lý: {counts["running"]}' if self.worker_state[0] else
                  'Đang tải lên Drive' if self.worker_state[1] else 'Sẵn sàng')
        message = self.queue.load_error or self.batch.error or self.upload.error or self.latest_message
        text = ' · '.join(part for part in (status, message) if part)
        self.status_text.setText(text)
        self.status_text.setToolTip('<p>' + escape(text) + '</p>')
        self.filter_queue()

    def open_current_file(self):
        self.open_output(open_file=True)

    def open_current_folder(self):
        self.open_output()

    def open_output(self, job_id=None, *, open_file=False):
        job = self.snapshot.get(job_id) if job_id else self.current_job()
        if job is None:
            return
        target = Path(job.output) if job and job.output else None
        if target is None or not target.exists():
            show_message(self, "Chưa có kết quả", "Job chưa có output hoặc file đã bị xóa.", QMessageBox.Icon.Information)
            return
        try:
            if open_file or target.is_dir():
                os.startfile(target)
            else:
                subprocess.Popen(['explorer.exe', '/select,', str(target.absolute())])
        except OSError as exc:
            show_message(self, 'Không mở được kết quả', str(exc))

    def configure_pipeline(self):
        from .settings_dialog import SettingsDialog
        run_dialog(SettingsDialog(self, self.settings, on_saved=self.refresh))

    def drive(self):
        from .drive_dialog import DriveDialog
        job = self.current_job()
        run_dialog(DriveDialog(self, self.upload, self.settings.root, job.id if job else None))

    def load_voice_names(self):
        try:
            records = VoiceRegistry(self.settings.root, None).list()
        except VoiceError:
            records = []
        self.voice_names = {(record['provider'], record['id']): record['name'] for record in records}

    def describe_voice(self, job=None):
        settings = self.settings if job is None else self.settings.for_job(job)
        speaker_map = None
        if job and settings.multi_speaker:
            review = job.metadata.get('speaker_review') or job.config.get('speaker_review')
            if isinstance(review, dict) and isinstance(review.get('voice_map'), dict):
                speaker_map = review['voice_map']
        return voice_description(settings, self.voice_names, self.preset_cache.get(preset_key(settings), ()),
                                 speaker_map=speaker_map)

    def voice_deletion_blocker(self, record):
        if not self.can_mutate():
            return 'Không thể xóa bản thu khi hàng đợi đang hoạt động hoặc không đọc được.'
        voice_id = record['id']
        for job in self.queue.snapshot():
            if job.status not in CLAIMABLE_STATUSES | {'running'}:
                continue
            settings = self.settings.for_job(job)
            if settings.mode == 'subtitle' or settings.tts_provider != record['provider']:
                continue
            review = job.config.get('speaker_review')
            voice_map = review.get('voice_map') if isinstance(review, dict) else None
            if settings.tts_custom_voice == voice_id or (settings.multi_speaker and
                    isinstance(voice_map, dict) and voice_id in voice_map.values()):
                return f'Không thể xóa: video {job.source} vẫn tham chiếu bản thu này.'
        return ''

    def voices(self):
        run_dialog(VoicesDialog(self, self.settings, preset_cache=self.preset_cache,
                                deletion_blocker=self.voice_deletion_blocker))
        self.load_voice_names()
        self.refresh()

    def add_urls(self):
        if not self.can_mutate():
            return
        text, accepted = QInputDialog.getMultiLineText(
            self, "Thêm nguồn", "URL HTTPS hoặc yt:ID / douyin:ID / drive:ID; mỗi dòng một nguồn")
        if not accepted or not text:
            return
        from .sources import classify_source

        try:
            self.settings.validate()
            sources = [line.strip() for line in text.splitlines() if line.strip()]
            for source in sources:
                if classify_source(source) == "local":
                    raise ValueError("Dùng Thêm video cho đường dẫn local")
            for source in sources:
                self.queue.add(Job(uuid4().hex[:12], source, self.settings.target_language,
                                   mode=self.settings.mode, config=self.settings.snapshot()))
        except (OSError, ValueError, QueueError, ProviderError) as exc:
            show_message(self, "Không thêm được nguồn", str(exc))
        self.refresh()

    def review_current(self):
        self.review_speaker()

    def review_speaker(self, job_id=None):
        if not self.can_mutate():
            return
        job = self.snapshot.get(job_id) if job_id else self.current_job()
        if job is None:
            return
        # A review changes persisted state; do not mutate the display snapshot.
        job = next((item for item in self.queue.snapshot() if item.id == job.id), None)
        if job is None:
            return
        data = job.metadata if isinstance(job.metadata, dict) else {}
        turns = data.get("diarization", [])
        if self.batch.running:
            return
        transcript_rows = data.get("review_transcript", [])
        if not isinstance(transcript_rows, list) or not transcript_rows:
            show_message(self, "Chưa có transcript", "Chạy job trước để tạo transcript cần duyệt.", QMessageBox.Icon.Information)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Duyệt transcript, bản dịch và speaker")
        fit_dialog(dialog, 800, 600)
        layout = QVBoxLayout(dialog)
        page_header(layout, 'Duyệt transcript / speaker')
        source = label(layout, 'Chỉnh sửa nội dung đã tạo cho video này.', name='muted')
        source.setToolTip(tip(job.source))
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        def edit_json(tab, hint, value):
            page = QWidget()
            body = QVBoxLayout(page)
            label(body, hint, wrap=True)
            editor = QPlainTextEdit(json.dumps(value, ensure_ascii=False, indent=2))
            editor.setAccessibleName(tab)
            body.addWidget(editor, 1)
            tabs.addTab(page, tab)
            return editor

        turns_text = edit_json('Lượt nói', 'Giữ start/end/speaker_id; status=resolved.', turns)
        transcript_text = edit_json('Transcript', 'Giữ start/end/source_id; sửa text để dịch lại.', transcript_rows)
        translation_text = edit_json('Bản dịch', 'Giữ start/end/source_id/speaker_id; sửa text để TTS chạy lại.',
                                     data.get('review_translation', []))
        voices_text = edit_json('Giọng nói', 'Voice map JSON, ví dụ {"speaker-1": "<voice-id>"}:',
                                data.get('speaker_review', {}).get('voice_map', {}))
        samples = data.get('speaker_samples', {})
        if samples:
            row = QHBoxLayout()
            layout.addLayout(row)
            choices = QComboBox()
            choices.setAccessibleName('Mẫu người nói')
            for speaker, sample in samples.items():
                choices.addItem(speaker, sample)
            row.addWidget(choices, 1)
            button(row, 'Nghe mẫu', lambda: self.play_sample(choices.currentData(), dialog))

        def save_review():
            try:
                edited_turns = validate_turns(json.loads(turns_text.toPlainText()), None)
                voice_map = json.loads(voices_text.toPlainText())
                transcript_rows = json.loads(transcript_text.toPlainText())
                original_rows = data.get("review_transcript", [])
                if not isinstance(transcript_rows, list) or len(transcript_rows) != len(original_rows):
                    raise ValueError("Transcript phải giữ nguyên số cue")
                edited_text = {}
                for original, row in zip(original_rows, transcript_rows):
                    if any(row.get(key) != original.get(key) for key in ("start", "end", "source_id")) or not isinstance(row.get("text"), str) or not row["text"].strip():
                        raise ValueError("Chỉ được sửa text; start/end/source_id phải giữ nguyên")
                    edited_text[str(original["source_id"])] = row["text"]
                translation_rows = json.loads(translation_text.toPlainText())
                original_translations = data.get("review_translation", [])
                if not isinstance(translation_rows, list) or len(translation_rows) != len(original_translations):
                    raise ValueError("Bản dịch phải giữ nguyên số cue")
                edited_translation = {}
                for original, row in zip(original_translations, translation_rows):
                    if any(row.get(key) != original.get(key) for key in ("start", "end", "source_id", "speaker_id")) or not isinstance(row.get("text"), str) or not row["text"].strip():
                        raise ValueError("Chỉ được sửa text bản dịch; thời điểm/ID phải giữ nguyên")
                    if row["text"] != original["text"]:
                        edited_translation[str(original.get("source_id", ""))] = row["text"]
                if any(turn.status != "resolved" for turn in edited_turns) or not isinstance(voice_map, dict):
                    raise ValueError("Mọi lượt nói phải resolved và voice map phải là object")
                review_segments = [Segment(float(row["start"]), float(row["end"]), edited_text[str(row["source_id"])], str(row["source_id"]), str(row.get("speaker_id", ""))) for row in original_rows]
                review_key = review_fingerprint(data.get("review_source_hash", ""), review_segments, edited_turns, [row.text for row in review_segments])
                config = dict(job.config)
                if edited_text != config.get("review_transcript", {}):
                    config["review_translation"] = edited_translation
                else:
                    config["review_translation"] = {**config.get("review_translation", {}), **edited_translation}
                config["review_transcript"] = edited_text
                config["speaker_review"] = {"approved": True, "turns": [turn.__dict__ for turn in edited_turns], "voice_map": voice_map, "fingerprint": review_key}
                job.config = config
                job.status, job.error = "queued", ""
                self.queue.update(job)
                dialog.accept()
                self.refresh()
            except (OSError, ValueError, TypeError, KeyError, AttributeError, QueueError, ProviderError) as exc:
                show_message(dialog, "Review không hợp lệ", str(exc))

        actions = QHBoxLayout()
        actions.addStretch()
        button(actions, 'Hủy', dialog.reject)
        button(actions, 'Lưu để chạy lại', save_review, variant='primary')
        layout.addLayout(actions)
        run_dialog(dialog)

    @staticmethod
    def play_sample(path, dialog):
        try:
            if Path(path).is_file():
                os.startfile(path)
        except OSError as exc:
            show_message(dialog, "Không mở được mẫu giọng", str(exc))

    def add(self):
        if not self.can_mutate():
            return
        try:
            self.settings.validate()
        except ValueError as exc:
            show_message(self, "Cấu hình không hợp lệ", str(exc))
            return
        try:
            paths, _ = QFileDialog.getOpenFileNames(self, "Thêm video", "", "Video (*.mp4 *.mkv *.mov *.avi);;Tất cả (*)")
            for source in paths:
                self.queue.add(Job(uuid4().hex[:12], source, self.settings.target_language,
                                   mode=self.settings.mode, config=self.settings.snapshot()))
        except (OSError, ValueError, QueueError) as exc:
            show_message(self, "Không lưu được queue", str(exc))
        self.refresh()

    def run(self):
        if not self.can_mutate():
            return
        try:
            self.batch.start()
        except (OSError, ValueError, QueueError) as exc:
            show_message(self, "Không chạy được queue", str(exc))
        self.refresh()

    def notify(self, job, message=""):
        timestamp = time.strftime('%H:%M:%S')
        if job is not None:
            detail = message or ' · '.join(part for part in (job.stage, getattr(job, 'error', '')) if part) or job.status
            self.events.put((job.id, job.stage, message, f'{timestamp}  {job.id[:6]}  {job.status:<9} {detail}'))
        else:
            self.events.put((None, '', message, f'{timestamp}  {message}' if message else ''))

    def retry(self):
        self.retry_jobs(self.selected_jobs())

    def retry_jobs(self, job_ids):
        if not self.can_mutate():
            return
        try:
            for job_id in job_ids:
                if self.queue.retry(job_id):
                    self.job_events = deque((e for e in self.job_events if e[0] != job_id), maxlen=1000)
        except (OSError, ValueError, QueueError) as exc:
            show_message(self, "Không thử lại được", str(exc))
        self.refresh()

    def remove_jobs(self):
        if self.batch.running or self.upload.running or self.closing or self.queue.load_error:
            return
        selected = self.selected_jobs()
        if not selected:
            return
        if not ask_confirmation(self, "Xóa job", f"Xóa {len(selected)} job đã chọn khỏi hàng đợi?\n"
                                "Video nguồn và thư mục kết quả vẫn được giữ nguyên."):
            return
        try:
            self.queue.remove(selected)
        except (OSError, ValueError, QueueError) as exc:
            show_message(self, "Không xóa được job", str(exc))
        else:
            self.latest_message = f'Đã gỡ {len(selected)} video khỏi hàng đợi.'
        self.refresh()

    def poll(self):
        changed = False
        lines = []
        for _ in range(500):
            try:
                job_id, stage, message, line = self.events.get_nowait()
            except messages.Empty:
                break
            changed = True
            if line:
                lines.append(line)
                self.job_events.append((job_id, stage, line))
            if message:
                self.latest_message = message
        if lines:
            self.log_lines.extend(lines)
            if self.log_toggle.isChecked():
                self.show_log_lines(lines)
        workers = self.batch.running, self.upload.running
        if changed or workers != self.worker_state:
            self.refresh()
        if self.closing and not any(workers):
            self.close()

    def show_log_lines(self, lines):
        fresh = []
        for line in lines:
            if line != self.log_last:
                fresh.append(line)
                self.log_last = line
        if not fresh:
            return
        self.log_text.appendPlainText("\n".join(fresh))

    def toggle_log(self, checked):
        self.log_frame.setVisible(checked)
        if checked:
            self.log_last = ""
            self.log_text.clear()
            self.show_log_lines(list(self.log_lines))

    def closeEvent(self, event):
        if not self.closing:
            self.closing = True
            self.cancel_workers()
        if self.batch.running or self.upload.running:
            self.status_text.setText("Đang hủy và chờ worker dừng…")
            self.centralWidget().setEnabled(False)
            event.ignore()
            return
        self.batch.join()
        self.upload.join()
        self.poll_timer.stop()
        super().closeEvent(event)

    def request_stop(self):
        if not self.stopping:
            self.stopping = True
            self.latest_message = ''
            self.cancel_workers()
            self.refresh()

    def cancel_workers(self):
        self.batch.cancel()
        self.upload.cancel()

    def api(self):
        run_dialog(CredentialDialog(self, self.settings))


def fit_dialog(dialog, width, height):
    area = dialog.screen().availableGeometry().size()
    dialog.resize(min(width, area.width() - 32), min(height, area.height() - 56))


class CredentialDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Cài đặt API và dịch văn bản")
        fit_dialog(self, 660, 550)
        shell = QVBoxLayout(self)
        scroll = QScrollArea(widgetResizable=True)
        shell.addWidget(scroll, 1)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        translation = QGroupBox("1. Dịch vụ dịch văn bản")
        layout.addWidget(translation)
        form = QFormLayout(translation)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        provider = QComboBox()
        provider.addItems(("openai", "gemini", "openai_compatible"))
        if provider.findText(settings.translation_provider) == -1:
            provider.addItem(settings.translation_provider)
        provider.setCurrentText(settings.translation_provider)
        self.api_variables = {
            "translation_provider": provider,
            "translation_base_url": QLineEdit(settings.translation_base_url),
            "translation_model": QLineEdit(settings.translation_model),
        }
        for caption, widget in zip(("Dịch vụ", "Base URL", "Model ID"), self.api_variables.values()):
            form.addRow(caption, widget)
        keys = QGroupBox("2. API key — nhập vào đúng dịch vụ")
        layout.addWidget(keys)
        form = QFormLayout(keys)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.fields = {}
        for field, caption in (("openai_api_key", "OpenAI API key"), ("gemini_api_key", "Gemini API key"),
                               ("openai_compatible_api_key", "OpenAI-compatible API key")):
            row = QHBoxLayout()
            entry = QLineEdit()
            entry.setEchoMode(QLineEdit.EchoMode.Password)
            delete = QCheckBox("Xóa key")
            row.addWidget(entry, 1)
            row.addWidget(delete)
            saved = "Đã lưu" if getattr(settings, field) else "Chưa có"
            form.addRow(f"{caption} ({saved})", row)
            self.fields[field] = entry, delete
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Save).setText("Lưu cấu hình")
        box.button(QDialogButtonBox.StandardButton.Save).setProperty('variant', 'primary')
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("Hủy")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        shell.addWidget(box)

    def accept(self):
        if self.validate():
            super().accept()

    def validate(self):
        previous = {field: getattr(self.settings, field) for field in self.fields}
        previous.update({name: getattr(self.settings, name) for name in self.api_variables})
        for field, (entry, delete) in self.fields.items():
            value = "" if delete.isChecked() else entry.text().strip() or previous[field]
            setattr(self.settings, field, value)
        for name, widget in self.api_variables.items():
            value = widget.currentText() if isinstance(widget, QComboBox) else widget.text()
            setattr(self.settings, name, value.strip())
        try:
            if self.settings.translation_provider == "openai_compatible":
                from .providers import OpenAICompatibleTranslator
                OpenAICompatibleTranslator(self.settings.openai_compatible_api_key,
                                           self.settings.translation_model, self.settings.translation_base_url)
            self.settings.save()
        except ValueError as exc:
            for field, value in previous.items():
                setattr(self.settings, field, value)
            show_message(self, "Cấu hình API không hợp lệ", str(exc))
            return False
        except (CredentialError, OSError):
            for field, value in previous.items():
                setattr(self.settings, field, value)
            show_message(self, "Không hoàn tất lưu credential", "Không hoàn tất lưu cấu hình an toàn. Bản credential được bảo vệ có thể đã được cập nhật; hãy mở lại ứng dụng để đọc trạng thái đã lưu trước khi thử lại.")
            return False
        return True


def launch(root: Path):
    application = QApplication([])
    apply_theme(application)
    try:
        window = App(root)
    except CredentialError:
        show_message(None, "Không đọc được credential", "Không đọc hoặc migrate được credential an toàn. Dữ liệu hiện có được giữ nguyên; kiểm tra tài khoản Windows và quyền truy cập cấu hình.")
        return
    window.show()
    application.exec()
