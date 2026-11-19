"""Small, non-blocking UI for consented local voices and provider presets."""
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .media import MediaAdapter
from .tts import TtsSession
from .voices import VoiceError, VoiceRegistry
from .ui import ACCENT, BG, FG, SURFACE


class VoicesDialog(tk.Toplevel):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.parent, self.settings = parent, settings
        self.title("Giọng nói")
        self.transient(parent)
        self.configure(background=BG)
        self.minsize(680, 410)
        self.registry = VoiceRegistry(settings.root, MediaAdapter(settings.ffmpeg, settings.ffprobe))
        self.records = []
        self.presets = []
        self._busy = False
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        ttk.Label(self, text="Thư viện giọng nói", style="Heading.TLabel").pack(
            anchor="w", padx=20, pady=(20, 4))
        ttk.Label(self, text="Giọng thu của bạn và các preset từ bộ tạo giọng đã chọn.",
                  style="Muted.TLabel").pack(anchor="w", padx=20, pady=(0, 12))
        library = ttk.Frame(self)
        library.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        self.listbox = tk.Listbox(
            library, width=72, height=12, exportselection=False, background=SURFACE,
            foreground=FG, selectbackground=ACCENT, selectforeground="white",
            borderwidth=0, highlightthickness=1, highlightbackground="#30394e",
            highlightcolor=ACCENT, activestyle="none", font=("Segoe UI", 11))
        scroll = ttk.Scrollbar(library, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.status = tk.StringVar(master=self, value="Đang tải giọng preset…")
        ttk.Label(self, textvariable=self.status, wraplength=640,
                  style="Muted.TLabel").pack(fill="x", padx=20)
        buttons = ttk.Frame(self); buttons.pack(fill="x", padx=20, pady=16)
        ttk.Button(buttons, text="Thêm giọng thu…", command=self.add).pack(side="left")
        ttk.Button(buttons, text="Chọn", command=self.select, style="Primary.TButton").pack(side="right")
        ttk.Button(buttons, text="Xóa giọng đã chọn", command=self.remove,
                   style="Danger.TButton").pack(side="left", padx=8)
        self._discover()

    def _discover(self):
        self._busy = True
        threading.Thread(target=self._discover_worker, daemon=True).start()

    def _session(self):
        provider = getattr(self.settings, "tts_provider", "vieneu")
        if provider not in ("vieneu", "omnivoice"):
            provider = "vieneu"
        return TtsSession(getattr(self.settings, "tts_python", ""), provider=provider,
                          model_path=getattr(self.settings, "tts_model", ""),
                          hf_cache=getattr(self.settings, "tts_hf_cache", ""),
                          device=getattr(self.settings, "tts_device", "cpu"),
                          license_accepted=getattr(self.settings, "consent_omnivoice_license", False))

    def _discover_worker(self):
        try:
            with self._session() as session:
                presets = session.voices()
        except Exception as exc:
            self.after(0, self._discovered, [], f"Không tải được preset: {exc}")
            return
        self.after(0, self._discovered, presets if isinstance(presets, list) else [], "")

    def _discovered(self, presets, error):
        self._busy = False
        self.presets = [item for item in presets if isinstance(item, dict) and isinstance(item.get("id"), str)]
        try:
            self.records = self.registry.list()
        except VoiceError as exc:
            self.records = []
            error = error or str(exc)
        self._render()
        self.status.set(error or "Chọn giọng để dùng cho các lần render sau.")

    def _render(self):
        self.listbox.delete(0, "end")
        for record in self.records:
            self.listbox.insert("end", f"{record['name']}  [{record['provider']}]  {record['id']}")
        for preset in self.presets:
            self.listbox.insert("end", f"Preset: {preset.get('label', preset['id'])}  [{preset['id']}]")


    def add(self):
        if self._busy:
            return
        audio = filedialog.askopenfilename(parent=self, title="Chọn đoạn thu âm",
                                           filetypes=[("Audio", "*.wav *.mp3 *.m4a *.flac"), ("All", "*.*")])
        if not audio:
            return
        name = simpledialog.askstring("Tên giọng", "Tên giọng:", parent=self)
        if not name:
            return
        provider = getattr(self.settings, "tts_provider", "vieneu")
        if provider not in ("vieneu", "omnivoice"):
            provider = "vieneu"
        ref_text = simpledialog.askstring("Văn bản tham chiếu", "Văn bản đúng của đoạn thu (bắt buộc với OmniVoice):",
                                          parent=self) or ""
        consent = tk.BooleanVar(master=self, value=False)
        if not self._consent_dialog(consent):
            return
        self._busy = True
        self.status.set("Đang chuẩn hóa giọng…")
        threading.Thread(target=self._add_worker,
                         args=(name, audio, provider, ref_text, consent.get()), daemon=True).start()

    def _consent_dialog(self, value):
        dialog = tk.Toplevel(self)
        dialog.title("Xác nhận quyền sử dụng")
        dialog.configure(background=BG)
        dialog.transient(self); dialog.grab_set()
        ttk.Label(dialog, text="Quyền sử dụng giọng nói", style="Heading.TLabel").pack(
            anchor="w", padx=20, pady=(20, 8))
        ttk.Label(dialog, text="Chỉ thêm bản ghi khi bạn là chủ giọng hoặc đã được chủ giọng cho phép.",
                  wraplength=480).pack(anchor="w", padx=20, pady=8)
        ttk.Checkbutton(dialog, text="Tôi là chủ giọng hoặc có sự đồng ý của chủ giọng", variable=value).pack(
            anchor="w", padx=20, pady=8)
        result = [False]
        def accept():
            result[0] = bool(value.get()); dialog.destroy()
        ttk.Button(dialog, text="Tiếp tục", command=accept, style="Primary.TButton").pack(side="right", padx=20, pady=16)
        ttk.Button(dialog, text="Hủy", command=dialog.destroy).pack(side="right", pady=16)
        self.wait_window(dialog)
        return result[0]

    def _add_worker(self, name, audio, provider, ref_text, consent):
        try:
            record = self.registry.add(name, Path(audio), provider, ref_text, consent)
        except Exception as exc:
            self.after(0, self._finished, None, str(exc))
            return
        self.after(0, self._finished, record, "")

    def _finished(self, record, error):
        self._busy = False
        if error:
            messagebox.showerror("Giọng nói", error, parent=self)
            self.status.set(error)
            return
        self.records.append(record)
        self._render()
        self.status.set("Đã thêm giọng. Chọn mục rồi bấm Chọn để lưu.")

    def _selected_record(self):
        selection = self.listbox.curselection()
        if not selection or selection[0] >= len(self.records):
            return None
        return self.records[selection[0]]

    def select(self):
        selection = self.listbox.curselection()
        if not selection:
            return
        if selection[0] < len(self.records):
            voice_id = self.records[selection[0]]["id"]
            self.settings.tts_custom_voice = voice_id
        else:
            self.settings.tts_voice = self.presets[selection[0] - len(self.records)]["id"]
        try:
            self.settings.save()
        except Exception as exc:
            messagebox.showerror("Giọng nói", str(exc), parent=self)
            return
        self.status.set("Đã lưu giọng được chọn.")

    def remove(self):
        record = self._selected_record()
        if record is None:
            return
        if not messagebox.askyesno("Xóa giọng", f"Xóa {record['name']}?", parent=self):
            return
        try:
            self.registry.remove(record["id"])
        except (VoiceError, KeyError) as exc:
            messagebox.showerror("Giọng nói", str(exc), parent=self)
            return
        if getattr(self.settings, "tts_custom_voice", "") == record["id"]:
            self.settings.tts_custom_voice = ""
            try:
                self.settings.save()
            except Exception:
                pass
        self.records = [item for item in self.records if item["id"] != record["id"]]
        self._render()
