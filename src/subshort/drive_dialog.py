"""Drive import is not consent: each upload requires an unchecked confirmation."""
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .drive import DriveError, import_service_account
from .ui import BG


class DriveDialog(simpledialog.Dialog):
    def __init__(self, parent, manager, root, job_id=None):
        self.manager, self.root, self.job_id = manager, Path(root), job_id
        super().__init__(parent, "Tải video lên Google Drive")

    def body(self, master):
        self.configure(background=BG)
        self.minsize(640, 360)
        master.configure(background=BG)
        master.pack_configure(fill="both", expand=True, padx=20, pady=16)
        ttk.Label(master, text="Xuất bản lên Google Drive", style="Heading.TLabel").pack(
            anchor="w", pady=(0, 12))
        self.folder = tk.StringVar(master=self, value=getattr(self.manager.settings, "drive_folder_id", ""))
        self.consent = tk.BooleanVar(master=self, value=False)
        ttk.Label(master, text="Service account cần quyền ghi vào thư mục đích.\nCredential lưu bằng Windows DPAPI; không kèm trong bundle.",
                  style="Muted.TLabel", wraplength=580).pack(anchor="w", pady=(0, 12))
        ttk.Button(master, text="Nhập JSON service account…", command=self.import_account).pack(anchor="w", pady=(0, 18))
        ttk.Label(master, text="Drive folder ID").pack(anchor="w")
        entry = ttk.Entry(master, textvariable=self.folder, width=55)
        entry.pack(fill="x", pady=6)
        ttk.Checkbutton(master, text="Tôi đồng ý gửi video đã chọn tới Google Drive", variable=self.consent).pack(anchor="w", pady=6)
        ttk.Label(master, text="Chỉ tải video đã hoàn tất. Video trên máy luôn được giữ nguyên.",
                  style="Muted.TLabel", wraplength=580).pack(anchor="w", pady=(8, 0))
        return entry

    def buttonbox(self):
        box = ttk.Frame(self, padding=(20, 8, 20, 20))
        ttk.Button(box, text="Tải video đã chọn", command=self.ok, style="Primary.TButton").pack(side="right")
        ttk.Button(box, text="Hủy tải đang chạy", command=self.manager.cancel).pack(side="left")
        ttk.Button(box, text="Đóng", command=self.cancel).pack(side="right", padx=8)
        box.pack(fill="x")
        self.bind("<Escape>", self.cancel)

    def import_account(self):
        path = filedialog.askopenfilename(parent=self, title="Chọn JSON service account", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            import_service_account(self.root, Path(path))
        except DriveError:
            messagebox.showerror("Drive", "Không nhập được service account hợp lệ; credential cũ được giữ nguyên.", parent=self)
            return
        messagebox.showinfo("Drive", "Đã lưu credential được bảo vệ. Chưa có video nào được gửi.", parent=self)

    def validate(self):
        if not self.consent.get():
            messagebox.showerror("Drive", "Cần đồng ý rõ ràng trước khi gửi video.", parent=self)
            return False
        if not self.job_id:
            messagebox.showerror("Drive", "Hãy chọn video đã render xong.", parent=self)
            return False
        folder = self.folder.get().strip()
        try:
            import re
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
                messagebox.showerror("Drive", "Đang có một video được tải lên.", parent=self)
                return False
        except Exception:
            messagebox.showerror("Drive", "Không bắt đầu được tải lên. Kiểm tra thư mục và video đã chọn.", parent=self)
            return False
        return True
