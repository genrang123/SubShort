"""Typed settings editor; credential management stays in its separate dialog."""

import copy
from dataclasses import fields
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .credentials import CredentialError
from .ui import ACCENT, BG, FG, MUTED, SURFACE


# group, field, Vietnamese label, value type, allowed choices (when fixed)
FIELD_REGISTRY = (
    ("Chung", "mode", "Chế độ", str, ("subtitle_dub", "subtitle", "dub")),
    ("Chung", "source_language", "Ngôn ngữ nguồn (trống: tự nhận diện)", str, ()),
    ("Chung", "target_language", "Ngôn ngữ đích", str, ()),
    ("Chung", "slow_speed", "Tốc độ video trước bước xuất cuối", float, ()),
    ("Chung", "final_speed", "Tốc độ video cuối", float, ()),
    ("Nhận dạng", "asr_provider", "Bộ nhận dạng", str, ("local", "qwen", "firered")),
    ("Nhận dạng", "asr_model", "Thư mục model ASR", str, ()),
    ("Nhận dạng", "asr_device", "Thiết bị ASR", str, ("cpu", "cuda")),
    ("Nhận dạng", "asr_aligner", "Thư mục model aligner", str, ()),
    ("Nhận dạng", "asr_vad_model", "Tệp model VAD", str, ()),
    ("Nhận dạng", "firered_type", "Loại FireRed", str, ("aed", "llm")),
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


class SettingsDialog(simpledialog.Dialog):
    """Modal editor; on_saved() runs only after validation and persistence succeed."""

    def __init__(self, parent, settings, on_saved=None):
        self.settings = settings
        self.on_saved = on_saved
        super().__init__(parent, "Cài đặt")

    def body(self, master):
        self.variables = {}
        self.editable_fields = []
        existing = {field.name for field in fields(self.settings)}
        self.configure(background=BG)
        self.minsize(960, 580)
        master.configure(background=BG)
        master.pack_configure(fill="both", expand=True)
        master.columnconfigure(1, weight=1)
        master.rowconfigure(1, weight=1)
        ttk.Label(master, text="Cài đặt studio", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 20))
        self.sections = tk.Listbox(
            master, width=23, height=11, exportselection=False, activestyle="none",
            background=SURFACE, foreground=MUTED, selectbackground=ACCENT,
            selectforeground=FG, borderwidth=0, highlightthickness=0,
            font=("Segoe UI", 11), selectborderwidth=0)
        self.sections.grid(row=1, column=0, sticky="ns", padx=(12, 20), pady=(0, 12))
        content = ttk.Frame(master, padding=(4, 0, 16, 16))
        content.grid(row=1, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        groups = {}
        self.section_frames = []
        first = None
        for group, name, label, value_type, choices in FIELD_REGISTRY:
            if name not in existing:
                continue
            if group not in groups:
                frame = ttk.Frame(content)
                frame.columnconfigure(1, weight=1, minsize=280)
                frame.grid(row=0, column=0, sticky="nsew")
                ttk.Label(frame, text=group, style="Heading.TLabel").grid(
                    row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
                ttk.Separator(frame).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
                groups[group] = frame
                self.sections.insert("end", group)
                self.section_frames.append(frame)
            frame = groups[group]
            row = 2 + (len(frame.grid_slaves()) - 2) // 2
            ttk.Label(frame, text=label, wraplength=265).grid(
                row=row, column=0, sticky="w", padx=(0, 18), pady=6)
            variable = (tk.BooleanVar if value_type is bool else tk.StringVar)(
                master=self, value=getattr(self.settings, name))
            if value_type is bool:
                widget = ttk.Checkbutton(frame, variable=variable)
            elif choices:
                widget = ttk.Combobox(frame, textvariable=variable, values=choices, state="readonly", width=38)
            else:
                widget = ttk.Entry(frame, textvariable=variable, width=40)
            widget.grid(row=row, column=1, sticky="ew", pady=6)
            self.variables[name] = variable
            self.editable_fields.append((name, label, value_type))
            if first is None:
                first = widget
        self.sections.bind("<<ListboxSelect>>", self._show_section)
        if self.section_frames:
            self.sections.selection_set(0)
            self._show_section()
        return first

    def _show_section(self, event=None):
        selection = self.sections.curselection()
        if selection:
            for frame in self.section_frames:
                frame.grid_remove()
            self.section_frames[selection[0]].grid()

    def buttonbox(self):
        box = ttk.Frame(self, padding=(16, 8, 16, 16))
        ttk.Button(box, text="Lưu cài đặt", command=self.ok, style="Primary.TButton").pack(side="right")
        ttk.Button(box, text="Hủy", command=self.cancel).pack(side="right", padx=(0, 8))
        box.pack(fill="x")
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

    def validate(self):
        candidate = copy.deepcopy(self.settings)
        try:
            for name, label, value_type in self.editable_fields:
                try:
                    value = value_type(self.variables[name].get())
                except (ValueError, tk.TclError):
                    raise ValueError(f"{label}: giá trị không hợp lệ") from None
                setattr(candidate, name, value)
            candidate.validate()
            candidate.save()
        except CredentialError:
            messagebox.showerror("Không lưu được cài đặt", "Không lưu được cấu hình an toàn. Cài đặt hiện tại được giữ nguyên.", parent=self)
            return False
        except (ValueError, OSError) as exc:
            messagebox.showerror("Không lưu được cài đặt", str(exc), parent=self)
            return False
        for name, _, _ in self.editable_fields:
            setattr(self.settings, name, getattr(candidate, name))
        return True

    def apply(self):
        self.result = True
        if self.on_saved is not None:
            self.on_saved()
