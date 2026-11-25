"""Qt presentation for the queue; the persisted Job remains the source of truth."""
from datetime import datetime
from html import escape
from math import isfinite
from pathlib import PureWindowsPath
import re
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSplitter, QStackedWidget,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTabBar, QTabWidget, QTableWidget, QVBoxLayout, QWidget,
)

from .sources import youtube_video_id
from .ui import ELEVATED, ERROR, FOCUS, MUTED, SUCCESS, TEXT, WARNING, page_header

STATUS = {'queued': 'Đang chờ', 'running': 'Đang xử lý', 'done': 'Hoàn tất',
          'failed': 'Lỗi', 'cancelled': 'Đã hủy'}
STAGE = {'download': 'Đang tải nguồn', 'probe': 'Kiểm tra nguồn', 'asr': 'Nhận dạng lời nói',
         'diarization': 'Phân biệt người nói', 'translate': 'Dịch văn bản', 'tts': 'Tạo giọng đọc',
         'caption_asr': 'Nhận dạng phụ đề', 'mix': 'Trộn âm thanh', 'mix_background': 'Trộn âm nền',
         'prepare_final_audio': 'Chuẩn bị âm thanh', 'separate_background': 'Tách âm nền',
         'subtitle_region': 'Xử lý vùng phụ đề', 'render': 'Xuất video', 'validate_output': 'Kiểm tra kết quả',
         'interrupted': 'Bị gián đoạn', 'no_audio': 'Giữ nguồn: không có âm thanh', 'done': 'Hoàn tất'}
UPLOAD = {'uploading': 'Đang tải lên', 'done': 'Đã tải lên', 'failed': 'Tải lên lỗi', 'cancelled': 'Đã hủy tải'}
COMPLETED_AT_COLUMN = 5
JOB_COLUMNS = (
    ('Video', 180),
    ('Bước xử lý', 164),
    ('Mốc pipeline', 120),
    ('Trạng thái', 122),
    ('Drive', 130),
    ('Thời gian hoàn tất', None),
)


def button(layout, text, action, *, variant='', tooltip=''):
    widget = QPushButton(text)
    if variant:
        widget.setProperty('variant', variant)
    if tooltip:
        widget.setToolTip(tooltip)
    widget.clicked.connect(action)
    layout.addWidget(widget)
    return widget


def label(layout, text='', *, name='', wrap=False):
    widget = QLabel(text, objectName=name)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    layout.addWidget(widget)
    return widget


def video_name(source: str) -> str:
    if '://' in source or source[:3].lower() == 'yt:':
        video_id = youtube_video_id(source)
        if video_id:
            return f'YouTube · {video_id}'
    if '://' in source:
        try:
            url = urlsplit(source[source.index('https://'):] if ':https://' in source else source)
            leaf = unquote(url.path.rstrip('/').rsplit('/', 1)[-1])
            return ' · '.join(filter(None, (url.hostname, leaf if leaf not in ('watch', 'view') else ''))) or source
        except ValueError:
            return source
    return PureWindowsPath(source).name or source


def stage_name(stage: str) -> str:
    return STAGE.get(stage, stage.replace('_', ' '))


def failure_summary(job) -> tuple[str, bool]:
    if job.stage == 'translate':
        if job.error in ('Thiếu OPENAI_API_KEY', 'Thiếu GEMINI_API_KEY', 'Thiếu OPENAI_COMPATIBLE_API_KEY'):
            return 'Thiếu API key cho dịch vụ dịch văn bản.', True
        if re.fullmatch(r'(OpenAI|Gemini|OpenAI-compatible) từ chối xác thực hoặc hạn mức \(HTTP [0-9]{3}\)', job.error):
            return 'Dịch vụ dịch từ chối xác thực hoặc đã chạm hạn mức.', True
        if job.error in ('HTTP 401 Unauthorized', 'HTTP 403 Forbidden'):
            return 'Dịch vụ dịch từ chối xác thực hoặc quyền truy cập.', True
    return ' '.join(job.error.split()) or 'Chưa có thông báo lỗi.', False


def upload_text(metadata: dict) -> tuple[str, dict]:
    data = metadata.get('upload')
    if not isinstance(data, dict):
        return '', {}
    state = data.get('status')
    text = UPLOAD.get(state, '') if isinstance(state, str) else ''
    size, sent = data.get('size'), data.get('bytes')
    if state == 'uploading' and type(size) is int and size > 0 and type(sent) is int:
        text += f' {max(0, min(sent, size)) / size:.0%}'
    return text, data


def tip(text):
    return f'<p>{escape(str(text))}</p>' if text else ''


def completed_at_text(metadata) -> tuple[str, str]:
    if not isinstance(metadata, dict):
        return '—', ''
    raw = metadata.get('completed_at')
    if not isinstance(raw, str) or not raw.strip():
        return '—', ''
    text = raw.strip()
    try:
        local = datetime.fromisoformat(text).astimezone()
    except (ValueError, OverflowError, OSError):
        return '—', ''
    return local.strftime('%d/%m/%Y %H:%M:%S'), tip(text)


def job_cells(job):
    upload, data = upload_text(job.metadata)
    stage = stage_name(job.stage) or ('Chuẩn bị' if job.status == 'running' else '—')
    value = job.progress
    progress = max(0, min(100, round(value * 100))) if 0 <= value <= 1 and isfinite(value) else None
    if job.status == 'queued':
        progress = None
    return (
        (video_name(job.source), tip(job.source), job.id),
        (stage, tip(stage), None),
        (f'{progress}%' if progress is not None else '—', tip('Mốc toàn pipeline'), progress),
        (STATUS.get(job.status, job.status), tip(job.error or job.output), job.status),
        (upload or '—', tip(data.get('error') or data.get('webViewLink') or upload), None),
        (*completed_at_text(job.metadata), None) if job.status == 'done' else ('—', '', None),
    )


def video_secondary(stage: str, upload: str) -> str:
    return ' · '.join(part for part in (stage if stage != '—' else '',
                                       f'Drive: {upload}' if upload and upload != '—' else '') if part)


class VideoCell(QStyledItemDelegate):
    compact = False

    def paint(self, painter, option, index):
        secondary = video_secondary(index.siblingAtColumn(1).data() or '',
                                    index.siblingAtColumn(4).data() or '') if self.compact else ''
        if not secondary:
            super().paint(painter, option, index)
            return
        cell = QStyleOptionViewItem(option)
        self.initStyleOption(cell, index)
        name, cell.text = cell.text, ''
        painter.save()
        painter.setClipRect(cell.rect)
        cell.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, cell, painter, cell.widget)
        metrics = cell.fontMetrics
        height = metrics.height()
        line = cell.rect.adjusted(8, (cell.rect.height() - 2 * height) // 2, -8, 0)
        line.setHeight(height)
        painter.setFont(cell.font)
        selected = cell.state & QStyle.StateFlag.State_Selected
        foreground = cell.palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        for text, color in ((name, foreground), (secondary, foreground if selected else QColor(MUTED))):
            painter.setPen(color)
            painter.drawText(line, Qt.AlignmentFlag.AlignVCenter,
                             metrics.elidedText(text, Qt.TextElideMode.ElideRight, line.width()))
            line.translate(0, height)
        painter.restore()


class ProgressCell(QStyledItemDelegate):
    def paint(self, painter, option, index):
        value = index.data(Qt.ItemDataRole.UserRole)
        if value is None:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setClipRect(option.rect)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        track = option.rect.adjusted(10, 17, -45, -17)
        painter.fillRect(track, QColor(ELEVATED))
        track.setWidth(round(track.width() * value / 100))
        painter.fillRect(track, QColor(FOCUS))
        painter.setPen(QColor(TEXT))
        painter.drawText(option.rect.adjusted(0, 0, -8, 0),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f'{value}%')
        painter.restore()


class StatusCell(QStyledItemDelegate):
    def paint(self, painter, option, index):
        color = {'running': FOCUS, 'done': SUCCESS, 'failed': ERROR, 'cancelled': WARNING}.get(
            index.data(Qt.ItemDataRole.UserRole), MUTED)
        painter.save()
        painter.setClipRect(option.rect)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        text = index.data()
        badge = option.rect.adjusted(7, 8, -7, -8)
        badge.setWidth(min(badge.width(), option.fontMetrics.horizontalAdvance(text) + 16))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ELEVATED))
        painter.drawRoundedRect(badge, 5, 5)
        painter.setPen(QColor(color))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class JobDetails(QWidget):
    def __init__(self, app):
        super().__init__(objectName='jobDetails')
        self.cache = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 5)
        layout.setSpacing(3)
        self.header = QWidget()
        header = QHBoxLayout(self.header)
        header.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.header)
        self.heading = label(header, name='section')
        self.heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.setStretch(0, 1)
        button(header, 'Thu gọn', app.toggle_details)
        self.failure = QWidget()
        failure = QHBoxLayout(self.failure)
        failure.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.failure)
        self.failure_text = label(failure)
        self.failure_text.setStyleSheet(f'color: {ERROR};')
        self.failure_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        failure.setStretch(0, 1)
        self.failure_action = button(failure, '', lambda: self.recover_failure(app))
        self.failure_api = False
        self.failure_text.installEventFilter(self)
        self.failure.hide()
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.views = []
        for title in ('Tổng quan', 'Pipeline', 'Log phiên', 'Kết quả'):
            page = QWidget()
            body = QVBoxLayout(page)
            body.setContentsMargins(4, 4, 4, 4)
            body.setSpacing(4)
            actions = QHBoxLayout()
            if title == 'Pipeline':
                self.activity = QProgressBar(textVisible=False)
                self.activity.setFixedHeight(7)
                self.activity.setRange(0, 0)
                self.activity.setAccessibleName('Bước đang chạy, chưa có số đo riêng')
                body.addWidget(self.activity)
            if title == 'Tổng quan':
                self.review = button(actions, 'Duyệt transcript / speaker', app.review_current)
            elif title == 'Kết quả':
                self.file = button(actions, 'Mở file', app.open_current_file)
                self.folder = button(actions, 'Hiện trong Explorer', app.open_current_folder)
            if actions.count():
                actions.addStretch()
                body.addLayout(actions)
            view = QPlainTextEdit(readOnly=True)
            view.setAccessibleName(title)
            view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
            body.addWidget(view, 1)
            self.tabs.addTab(page, title)
            self.views.append(view)
        self.tabs.currentChanged.connect(self.update_minimum_height)

    def update_minimum_height(self):
        layout = self.layout()
        margins = layout.contentsMargins()
        chrome = sum(widget.sizeHint().height() + layout.spacing()
                     for widget in (self.header, self.failure) if not widget.isHidden())
        view = self.views[self.tabs.currentIndex()]
        page = self.tabs.currentWidget()
        text_height = (2 * view.fontMetrics().lineSpacing() + 2 * view.frameWidth()
                       + round(2 * view.document().documentMargin()))
        height = (margins.top() + margins.bottom() + chrome + self.tabs.tabBar().sizeHint().height()
                  + 2 * self.tabs.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth)
                  + page.layout().minimumSize().height() + text_height)
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)

    def eventFilter(self, watched, event):
        if watched is self.failure_text and event.type() == QEvent.Type.Resize:
            self.elide_failure()
        return super().eventFilter(watched, event)

    def elide_failure(self):
        text = self.failure_text
        text.setText(text.fontMetrics().elidedText(text.accessibleName(), Qt.TextElideMode.ElideRight,
                                                  text.contentsRect().width()))

    def recover_failure(self, app):
        if self.failure_api:
            app.api()
        else:
            self.tabs.setCurrentWidget(self.views[2].parentWidget())

    def render(self, job, logs, stages, can_review, voice):
        name = video_name(job.source)
        state = STATUS.get(job.status, job.status)
        current = stage_name(job.stage) or ('Chuẩn bị' if job.status == 'running' else '—')
        upload, data = upload_text(job.metadata)
        timing = job.metadata.get('timing')
        duration = timing.get('final_duration_seconds') if isinstance(timing, dict) else None
        dimensions = job.metadata.get('output_dimensions')
        resolution = ' × '.join(map(str, dimensions)) if isinstance(dimensions, (list, tuple)) and len(dimensions) == 2 else 'Chưa có'
        done = job.metadata.get('stages')
        done = [stage for stage in done if isinstance(stage, str)] if isinstance(done, list) else []
        observed = list(dict.fromkeys([*stages, *done, *([job.stage] if job.stage else [])]))
        pipeline = [f'Bước gần nhất: {current}']
        if job.status == 'running':
            pipeline.append('Bước hiện tại chưa có số đo phần trăm riêng.')
        pipeline.extend(f'{"✓" if step in done else "•"} {stage_name(step)}' for step in observed)
        if upload:
            pipeline.append(f'Drive: {upload}')
        overview = [f'Nguồn: {job.source}', f'Trạng thái: {state}', f'Bước: {current}']
        overview.append(f'Giọng theo cấu hình video: {voice}')
        if job.error:
            overview.append(f'Lỗi: {job.error}')
        output_info = [f'File: {job.output or "Chưa có"}',
                       f'Thư mục: {PureWindowsPath(job.output).parent if job.output else "Chưa có"}',
                       f'Độ phân giải đầu ra: {resolution}',
                       'Thời lượng đầu ra: ' + (f'{duration:.2f} giây' if type(duration) in (int, float) else 'Chưa có')]
        if upload:
            output_info.append(f'Drive: {upload}')
        output_info.extend(str(data[key]) for key in ('error', 'webViewLink') if data.get(key))
        failed = job.status == 'failed'
        log = '\n'.join(logs)
        if failed and job.error:
            log = f'Lỗi: {job.error}\n\n{log}'
        texts = ('\n'.join(overview), '\n'.join(pipeline),
                 log or 'Chưa có log cho video này trong phiên hiện tại.', '\n'.join(output_info))
        if failed:
            summary, self.failure_api = failure_summary(job)
            line = ' · '.join(filter(None, ('Lỗi', stage_name(job.stage), summary)))
            self.failure_text.setAccessibleName(line)
            self.failure_text.setToolTip(tip(line))
            self.failure_action.setText('Mở cài đặt API' if self.failure_api else 'Xem log video')
            self.failure_action.setToolTip(
                'Kiểm tra API key, quyền và hạn mức của dịch vụ đã dùng cho video. '
                'Thay đổi dịch vụ mặc định không đổi cấu hình của video đã thêm.' if self.failure_api else '')
            self.elide_failure()
        self.failure.setVisible(failed)
        self.heading.setText(name)
        self.heading.setToolTip(tip(job.source))
        for view, text, old in zip(self.views, texts, self.cache if self.cache and self.cache[0] == job.id else (None,) * 4):
            if text != old:
                view.setPlainText(text)
        self.cache = (job.id, *texts)
        self.activity.setVisible(job.status == 'running' or (data.get('status') == 'uploading' and not data.get('size')))
        has_review = bool(job.metadata.get('review_transcript'))
        self.review.setVisible(has_review)
        self.review.setEnabled(can_review and has_review)
        self.file.setEnabled(bool(job.output))
        self.folder.setEnabled(bool(job.output))
        self.update_minimum_height()


def build_workspace(app):
    central = QWidget()
    app.setCentralWidget(central)
    layout = QHBoxLayout(central)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    rail = QWidget(objectName='sidebar')
    layout.addWidget(rail)
    nav = QVBoxLayout(rail)
    nav.setContentsMargins(9, 9, 9, 6)
    nav.setSpacing(3)
    brand = label(nav, 'SubShort', name='brand')
    caption = label(nav, 'VIDEO STUDIO', name='navSection')
    workspace = label(nav, 'KHÔNG GIAN LÀM VIỆC', name='navSection')
    icons = QStyle.StandardPixmap
    items = []

    def nav_item(text, icon, action):
        item = button(nav, text, action, variant='nav', tooltip=text)
        item.setIcon(app.style().standardIcon(icon))
        item.setAccessibleName(text)
        items.append((item, text))
        return item

    def focus_queue():
        queue_nav.setChecked(True)
        app.queue_tabs.setCurrentIndex(0)
        (app.jobs if app.queue_stack.currentWidget() is app.jobs else app.empty_add).setFocus()

    queue_nav = nav_item('Hàng đợi video', icons.SP_FileDialogListView, focus_queue)
    queue_nav.setCheckable(True)
    queue_nav.setChecked(True)
    nav.addSpacing(10)
    configuration = label(nav, 'CẤU HÌNH', name='navSection')
    app.voices_button = nav_item('Giọng nói', icons.SP_MediaVolume, app.voices)
    nav_item('Cài đặt', icons.SP_FileDialogDetailedView, app.configure_pipeline)
    nav.addStretch()

    page = QWidget()
    layout.addWidget(page, 1)
    main = QVBoxLayout(page)
    main.setSpacing(6)
    header = QGridLayout()
    main.addLayout(header)
    title = app.queue_title = QWidget()
    titles = QVBoxLayout(title)
    titles.setContentsMargins(0, 0, 0, 0)
    titles.setSpacing(1)
    page_header(titles, 'Hàng đợi video', 'Thêm nguồn, theo dõi xử lý và mở thành phẩm.')
    app.default_voice = label(titles, name='muted')
    app.default_voice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    sources = app.sources = QWidget()
    source_actions = QHBoxLayout(sources)
    source_actions.setContentsMargins(0, 0, 0, 0)
    app.urls_button = button(source_actions, 'Thêm URL / ID', app.add_urls)
    app.add_button = button(source_actions, '+ Thêm video', app.add, variant='primary')

    summary = app.summary = QWidget(objectName='queueSummary')
    summary_layout = QHBoxLayout(summary)
    summary_layout.setContentsMargins(8, 4, 8, 4)
    app.counts = {}
    for key, text in (('total', 'Tổng'), ('queued', 'Chờ'), ('running', 'Đang xử lý'),
                      ('done', 'Hoàn tất'), ('failed', 'Lỗi')):
        count = label(summary_layout, name='muted')
        count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        app.counts[key] = (count, text)
    main.addWidget(summary)

    toolbar = QGridLayout()
    main.addLayout(toolbar)
    batches, selection = app.batch_actions, app.selection_actions = QWidget(), QWidget()
    batch_actions, selected_actions = QHBoxLayout(batches), QHBoxLayout(selection)
    for actions in (batch_actions, selected_actions):
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(5)
    app.start_button = button(batch_actions, 'Chạy hàng đợi', app.run, variant='primary',
                              tooltip='Chạy các video đang chờ và thử lại các video lỗi.')
    app.cancel_button = button(batch_actions, 'Dừng xử lý / tải lên', app.request_stop,
                               tooltip='Yêu cầu dừng cả xử lý và tải lên nếu đang chạy.')
    app.open_video_button = button(selected_actions, 'Mở video', app.open_current_file, variant='primary')
    app.details_button = button(selected_actions, 'Chi tiết', app.toggle_details)
    app.retry_button = button(selected_actions, 'Thử lại', app.retry, tooltip='Đưa video lỗi hoặc đã hủy về hàng đợi.')
    app.remove_button = button(selected_actions, 'Xóa', app.remove_jobs, variant='danger',
                               tooltip='Gỡ khỏi hàng đợi, giữ nguyên file nguồn và kết quả.')

    app.queue_tabs = QTabBar()
    app.queue_tabs.setExpanding(False)
    app.queue_tabs.addTab('Hàng đợi')
    app.queue_tabs.addTab('Đã hoàn tất')
    main.addWidget(app.queue_tabs)

    app.queue_stack = QStackedWidget()
    app.queue_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
    app.queue_stack.setMinimumHeight(78)
    app.empty_queue = QWidget(objectName='emptyQueue')
    empty = QVBoxLayout(app.empty_queue)
    empty.setContentsMargins(4, 3, 4, 3)
    empty.setSpacing(2)
    empty.addStretch()
    empty_icon = label(empty)
    empty_icon.setPixmap(app.style().standardIcon(icons.SP_FileIcon).pixmap(24, 24))
    app.empty_title = label(empty, name='section')
    app.empty_hint = label(empty, name='muted', wrap=True)
    for item in (empty_icon, app.empty_title, app.empty_hint):
        item.setAlignment(Qt.AlignmentFlag.AlignCenter)
    empty_actions = QHBoxLayout()
    empty.addLayout(empty_actions)
    empty_actions.addStretch()
    app.empty_add = button(empty_actions, 'Thêm video', app.add, variant='primary')
    app.empty_urls = button(empty_actions, 'Thêm URL', app.add_urls)
    empty_actions.addStretch()
    empty.addStretch()
    app.queue_stack.addWidget(app.empty_queue)

    jobs = app.jobs = QTableWidget(0, len(JOB_COLUMNS))
    jobs.setHorizontalHeaderLabels([title for title, _ in JOB_COLUMNS])
    jobs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    jobs.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    jobs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    jobs.setAlternatingRowColors(True)
    jobs.setShowGrid(False)
    jobs.verticalHeader().hide()
    jobs.verticalHeader().setDefaultSectionSize(40)
    header_view = jobs.horizontalHeader()
    completed_width = max(
        144,
        header_view.fontMetrics().horizontalAdvance(JOB_COLUMNS[COMPLETED_AT_COLUMN][0]) + 24,
        jobs.fontMetrics().horizontalAdvance('88/88/8888 88:88:88') + 24,
    )
    for column, (_, width) in enumerate(JOB_COLUMNS):
        jobs.setColumnWidth(column, completed_width if column == COMPLETED_AT_COLUMN else width)
    header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header_view.moveSection(header_view.visualIndex(COMPLETED_AT_COLUMN), 0)
    jobs.horizontalHeaderItem(2).setToolTip('Mốc toàn pipeline, không phải phần trăm của bước hiện tại.')
    video_cell = VideoCell(jobs)
    jobs.setItemDelegateForColumn(0, video_cell)
    jobs.setItemDelegateForColumn(2, ProgressCell(jobs))
    jobs.setItemDelegateForColumn(3, StatusCell(jobs))
    jobs.doubleClicked.connect(lambda index: app.open_output(app.job_id_at(index.row())))
    jobs.itemSelectionChanged.connect(app.sync_actions)
    jobs.currentCellChanged.connect(app.sync_actions)
    jobs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    jobs.customContextMenuRequested.connect(app.queue_menu)
    app.queue_stack.addWidget(jobs)
    app.queue_tabs.currentChanged.connect(app.filter_queue)

    app.splitter = QSplitter(Qt.Orientation.Vertical)
    app.splitter.setChildrenCollapsible(False)
    app.splitter.addWidget(app.queue_stack)
    app.details = JobDetails(app)
    app.splitter.addWidget(app.details)
    app.splitter.setStretchFactor(0, 3)
    app.splitter.setStretchFactor(1, 2)
    app.details.hide()
    main.addWidget(app.splitter, 1)

    app.log_frame = QWidget()
    logs = QVBoxLayout(app.log_frame)
    logs.setContentsMargins(0, 0, 0, 0)
    app.log_text = QPlainTextEdit(readOnly=True, maximumBlockCount=1000)
    app.log_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
    app.log_text.setMaximumHeight(160)
    logs.addWidget(app.log_text)
    app.splitter.addWidget(app.log_frame)
    app.splitter.setStretchFactor(2, 1)
    app.log_frame.hide()
    status = app.statusBar()
    app.status_text = QLabel(textFormat=Qt.TextFormat.PlainText)
    app.status_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    status.addWidget(app.status_text, 1)
    controls = QWidget()
    footer = QHBoxLayout(controls)
    footer.setContentsMargins(0, 0, 0, 0)
    footer.setSpacing(4)
    status.addPermanentWidget(controls)
    app.open_button = button(footer, 'Hiện trong Explorer', app.open_current_folder,
                             tooltip='Hiện đúng file kết quả trong Explorer.')
    app.upload_button = button(footer, 'Drive', app.drive, tooltip='Cấu hình và tải video lên Google Drive.')
    app.log_toggle = QCheckBox('Log')
    app.log_toggle.setToolTip('Hiện log xử lý chung')
    app.log_toggle.toggled.connect(app.toggle_log)
    footer.addWidget(app.log_toggle)

    last = None

    def adapt():
        nonlocal last
        compact, narrow, short = app.width() < 960, app.width() < 620, app.height() < 390
        if last == (compact, narrow, short):
            return
        focus = app.focusWidget()
        compact_changed = last is None or compact != last[0]
        short_changed = last is None or short != last[2]
        last = (compact, narrow, short)
        app.short_workspace = short
        if short_changed:
            app.details.header.setVisible(not short)
            app.details.update_minimum_height()
        title.findChild(QLabel, 'muted').setVisible(not short)
        rail.setFixedWidth(55 if compact else 222)
        brand.setText('S' if compact else 'SubShort')
        for item in (caption, workspace, configuration):
            item.setVisible(not compact)
        for item, text in items:
            item.setText('' if compact else text)
        margin = 6 if narrow or short else 18
        main.setContentsMargins(margin, margin, margin, 5)
        header.addWidget(title, 0, 0)
        header.removeWidget(sources)
        header.addWidget(sources, int(narrow), int(not narrow), Qt.AlignmentFlag.AlignLeft)
        header.setColumnStretch(0, 1)
        toolbar.addWidget(batches, 0, 0, Qt.AlignmentFlag.AlignLeft)
        toolbar.removeWidget(selection)
        toolbar.addWidget(selection, int(narrow), int(not narrow), Qt.AlignmentFlag.AlignLeft)
        toolbar.setColumnStretch(0, 1)
        if compact_changed:
            for column in (1, 4):
                jobs.setColumnHidden(column, compact)
            video_cell.compact = compact
            jobs.verticalHeader().setDefaultSectionSize(max(48, 2 * jobs.fontMetrics().height() + 12) if compact else 40)
            jobs.viewport().update()
        if hasattr(app, 'poll_timer'):
            app.sync_actions(previous_focus=focus)

    app.adapt_workspace = adapt
    adapt()
