"""Shared studio styling; native file pickers retain platform accessibility."""
from tkinter import ttk, font

BG = "#10141f"
SURFACE = "#191f2e"
FG = "#e8edf7"
MUTED = "#91a0b8"
ACCENT = "#7c6cff"


def apply_theme(root):
    root.configure(background=BG)
    root.option_add("*Background", SURFACE)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
        font.nametofont(name, root=root).configure(family="Segoe UI", size=10)
    root.option_add("*Foreground", FG)
    root.option_add("*insertBackground", FG)
    root.option_add("*selectBackground", ACCENT)
    root.option_add("*selectForeground", "white")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=SURFACE, foreground=FG, bordercolor="#30394e",
                    lightcolor=SURFACE, darkcolor=SURFACE, font=("Segoe UI", 10))
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG)
    style.configure("Card.TLabel", background=SURFACE)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Title.TLabel", font=("Segoe UI Semibold", 24))
    style.configure("Heading.TLabel", font=("Segoe UI Semibold", 13))
    style.configure("Brand.TLabel", foreground=ACCENT, font=("Segoe UI Semibold", 21))
    style.configure("TButton", padding=(14, 10), relief="flat", borderwidth=0)
    style.map("TButton", background=[("active", "#30394e"), ("disabled", "#202638")],
              foreground=[("disabled", "#627087")])
    style.configure("Primary.TButton", background=ACCENT, foreground="white")
    style.map("Primary.TButton", background=[("active", "#9487ff"), ("disabled", "#393451")])
    style.configure("Danger.TButton", foreground="#ff9aab")
    style.configure("TEntry", fieldbackground="#222a3c", padding=8, insertcolor=FG)
    style.configure("TCombobox", fieldbackground="#222a3c", padding=7, arrowcolor=MUTED)
    style.map("TCombobox", fieldbackground=[("readonly", "#222a3c")],
              foreground=[("readonly", FG)])
    style.configure("TCheckbutton", background=BG, padding=5)
    style.map("TCheckbutton", background=[("active", BG)])
    style.configure("TLabelframe", background=BG, padding=14)
    style.configure("TLabelframe.Label", background=BG, foreground=MUTED,
                    font=("Segoe UI Semibold", 10))
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 8, 0, 8))
    style.configure("TNotebook.Tab", padding=(12, 9), background=SURFACE)
    style.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", "white")])
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE,
                    foreground=FG, rowheight=46, borderwidth=0)
    style.configure("Treeview.Heading", background="#222a3c", foreground=MUTED,
                    padding=(10, 12), relief="flat", font=("Segoe UI Semibold", 10))
    style.map("Treeview", background=[("selected", "#38335c")], foreground=[("selected", "white")])
    style.configure("TScrollbar", background="#30394e", troughcolor=BG, borderwidth=0,
                    arrowcolor=MUTED)
    for orientation in ("Vertical", "Horizontal"):
        style.layout(f"{orientation}.TScrollbar", [
            (f"{orientation}.Scrollbar.trough", {"sticky": "nswe", "children": [
                (f"{orientation}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure(f"{orientation}.TScrollbar", background="#30394e", troughcolor=BG,
                        bordercolor=BG, lightcolor="#30394e", darkcolor="#30394e", arrowsize=10)  
    style.configure("TProgressbar", background=ACCENT, troughcolor="#222a3c", borderwidth=0)
    return style
