#!/usr/bin/env python3
"""
Plate Cutter — desktop app
"""
import os
import sys
import queue
import threading
import traceback
import subprocess
import re

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except Exception:
    DND_AVAILABLE = False

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import plate_cutter

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")


class QueueWriter:
    def __init__(self, q):
        self.q = q

    def write(self, text):
        if text:
            self.q.put(text)

    def flush(self):
        pass


class PlateCutterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Plate Cutter")
        self.root.geometry("640x560")
        self.root.minsize(560, 480)

        self.files = []
        self.output_dir = os.path.join(os.path.expanduser("~"), "Desktop", "plate_cutter_output")
        self.output_dir_is_default = True
        self.log_queue = queue.Queue()
        self.worker_thread = None

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        title = tk.Label(self.root, text="Plate Cutter", font=("Helvetica", 18, "bold"))
        title.pack(anchor="w", **pad)

        subtitle_text = (
            "Drag plate photos into the box below, or click Browse."
            if DND_AVAILABLE else
            "Click Browse to select plate photos."
        )
        subtitle = tk.Label(self.root, text=subtitle_text, fg="#555555")
        subtitle.pack(anchor="w", padx=10)

        list_frame = tk.Frame(self.root, bd=2, relief="groove")
        list_frame.pack(fill="both", expand=True, **pad)

        self.listbox = tk.Listbox(list_frame, selectmode="extended")
        self.listbox.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        scrollbar = tk.Scrollbar(list_frame, command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)

        if DND_AVAILABLE:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", self._on_drop)

        btn_row = tk.Frame(self.root)
        btn_row.pack(fill="x", **pad)

        tk.Button(btn_row, text="Browse for Photos...", command=self._browse_files).pack(side="left")
        tk.Button(btn_row, text="Remove Selected", command=self._remove_selected).pack(side="left", padx=8)
        tk.Button(btn_row, text="Clear All", command=self._clear_files).pack(side="left")

        out_row = tk.Frame(self.root)
        out_row.pack(fill="x", **pad)
        tk.Label(out_row, text="Save results to:").pack(side="left")
        self.output_label = tk.Label(out_row, text=self.output_dir, fg="#0066cc")
        self.output_label.pack(side="left", padx=6)
        tk.Button(out_row, text="Choose Folder...", command=self._choose_output).pack(side="right")

        out_note = tk.Label(
            self.root,
            text=("All results go into a single \"Results\" folder here, split into "
                  "20/90 subfolders."),
            fg="#777777", wraplength=600, justify="left",
        )
        out_note.pack(anchor="w", padx=10)

        run_row = tk.Frame(self.root)
        run_row.pack(fill="x", **pad)
        self.run_button = tk.Button(
            run_row, text="Run", font=("Helvetica", 12, "bold"),
            bg="#2e7d32", fg="white", command=self._start_run
        )
        self.run_button.pack(side="left")

        self.progress = ttk.Progressbar(run_row, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=10)

        self.open_output_button = tk.Button(
            run_row, text="Open Output Folder", command=self._open_output, state="disabled"
        )
        self.open_output_button.pack(side="right")

        tk.Label(self.root, text="Log:").pack(anchor="w", padx=10)
        self.log_text = tk.Text(self.root, height=10, state="disabled", bg="#111111", fg="#dddddd")
        self.log_text.pack(fill="both", expand=False, padx=10, pady=(0, 10))

    def _browse_files(self):
        paths = filedialog.askopenfilenames(
            title="Select plate photos",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"), ("All files", "*.*")],
        )
        self._add_files(paths)

    def _on_drop(self, event):
        data = event.data
        paths = re.findall(r'\{([^}]+)\}|(\S+)', data)
        cleaned = [p[0] or p[1] for p in paths]
        self._add_files(cleaned)

    def _add_files(self, paths):
        added = 0
        for p in paths:
            if os.path.isdir(p):
                for f in sorted(os.listdir(p)):
                    full = os.path.join(p, f)
                    if full.lower().endswith(IMAGE_EXTS) and full not in self.files:
                        self.files.append(full)
                        self.listbox.insert("end", os.path.basename(full))
                        added += 1
            elif p.lower().endswith(IMAGE_EXTS) and p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", os.path.basename(p))
                added += 1

        if added and self.output_dir_is_default and self.files:
            first_folder = os.path.dirname(self.files[0])
            if first_folder:
                self.output_dir = first_folder
                self.output_label.config(text=self.output_dir)

    def _remove_selected(self):
        for i in reversed(self.listbox.curselection()):
            del self.files[i]
            self.listbox.delete(i)

    def _clear_files(self):
        self.files.clear()
        self.listbox.delete(0, "end")

    def _choose_output(self):
        chosen = filedialog.askdirectory(title="Choose output folder")
        if chosen:
            self.output_dir = chosen
            self.output_dir_is_default = False
            self.output_label.config(text=self.output_dir)

    def _open_output(self):
        path = self.output_dir
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", path])
            elif sys.platform.startswith("win"):
                os.startfile(path)
            else:
                subprocess.run(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Couldn't open folder", str(e))

    def _log(self, text):
        self.log_text.config(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _start_run(self):
        if not self.files:
            messagebox.showwarning("No photos", "Add at least one plate photo first.")
            return
        if self.worker_thread and self.worker_thread.is_alive():
            return

        os.makedirs(self.output_dir, exist_ok=True)
        self.run_button.config(state="disabled")
        self.open_output_button.config(state="disabled")
        self.progress.start(12)
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

        self.worker_thread = threading.Thread(target=self._run_worker, daemon=True)
        self.worker_thread.start()

    def _run_worker(self):
        writer = QueueWriter(self.log_queue)
        old_stdout = sys.stdout
        sys.stdout = writer
        try:
            results_root = plate_cutter.next_results_dir(self.output_dir)
            print(f"Saving results in: {results_root}\n")
            for path in self.files:
                print(f"Processing {os.path.basename(path)} ...\n")
                try:
                    plate_cutter.process_image(path, results_root)
                except Exception as e:
                    print(f"  FAILED: {e}\n")
            print("\nAll done.\n")
        except Exception:
            print("\nUnexpected error:\n")
            print(traceback.format_exc())
        finally:
            sys.stdout = old_stdout
            self.log_queue.put("__DONE__")


def main():
    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    app = PlateCutterApp(root)

    def check_done():
        try:
            while True:
                text = app.log_queue.get_nowait()
                if text == "__DONE__":
                    app.run_button.config(state="normal")
                    app.open_output_button.config(state="normal")
                    app.progress.stop()
                else:
                    app._log(text)
        except queue.Empty:
            pass
        root.after(150, check_done)

    root.after(150, check_done)
    root.mainloop()


if __name__ == "__main__":
    main()