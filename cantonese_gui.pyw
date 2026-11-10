# -*- coding: utf-8 -*-
"""
cantonese_gui.pyw - 粵語讀音 (中大 粵語審音配詞字庫) 視窗版

雙擊即可開啟 (Windows 會用 pythonw.exe, 唔會彈出黑色 console 視窗).
需要同一個資料夾內有 cantonese.py (核心功能).
只用 Python 標準庫 (tkinter), 唔需要 pip 安裝任何嘢.
"""
import os
import queue
import sys
import threading
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tkinter as tk
from tkinter import ttk, messagebox

try:
    import cantonese as core
except Exception:  # show the error in a window, since pythonw has no console
    r = tk.Tk(); r.withdraw()
    messagebox.showerror("粵語讀音", "載入 cantonese.py 失敗 (要放喺同一個資料夾):\n\n"
                         + traceback.format_exc())
    sys.exit(1)

if os.name == "nt":
    try:  # crisp text on high-DPI Windows screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

FONT_FAMILY = "Microsoft JhengHei" if os.name == "nt" else "Noto Sans CJK TC"


class App:
    def __init__(self, root, open_browser=True):
        self.root = root
        self.open_browser = open_browser
        self.q = queue.Queue()
        self.busy = False
        self.net = core.Net()
        self.lex = core.Lexicon(self.net)
        core.set_logger(lambda m: self.q.put(("log", m)))

        root.title("粵語讀音 — 中大粵語審音配詞字庫")
        root.geometry("760x520")
        root.minsize(520, 360)

        big = (FONT_FAMILY, 20)
        mid = (FONT_FAMILY, 13)
        top = ttk.Frame(root, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="輸入繁體中文詞語:", font=mid).pack(anchor="w")
        row = ttk.Frame(top)
        row.pack(fill="x", pady=6)
        self.entry = tk.Entry(row, font=big)
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.btn = tk.Button(row, text="查詢讀音", font=(FONT_FAMILY, 16),
                             command=self.start, padx=14)
        self.btn.pack(side="left", padx=(8, 0))
        self.entry.bind("<Return>", lambda e: self.start())
        self.entry.bind("<KP_Enter>", lambda e: self.start())
        ttk.Label(top, text="多音字可以指定讀音, 例如: 銀行[hong4]", font=(FONT_FAMILY, 10),
                  foreground="#777").pack(anchor="w")

        opts = ttk.Frame(top)
        opts.pack(fill="x", pady=(8, 0))
        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="循環播放", variable=self.loop_var).pack(side="left")
        self.smooth_on = False
        self.smooth_btn = tk.Button(opts, text="更順／音量正常", font=(FONT_FAMILY, 11),
                                    command=self.toggle_smooth, padx=10)
        self.smooth_btn.pack(side="left", padx=(12, 0))
        ttk.Label(opts, text="（字與字交叉淡化，音量拉齊）", font=(FONT_FAMILY, 9),
                  foreground="#777").pack(side="left", padx=(8, 0))

        self.result = tk.Label(root, text="", font=(FONT_FAMILY, 22), fg="#b22222",
                               wraplength=720, justify="center")
        self.result.pack(fill="x", padx=12, pady=(4, 6))

        self.status = tk.Label(root, text="準備好", font=mid, anchor="w")
        self.status.pack(fill="x", padx=12)

        logf = ttk.Frame(root, padding=(12, 4, 12, 12))
        logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, font=(FONT_FAMILY, 11), height=8, wrap="word",
                           state="disabled", bg="#fafafa")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        self.entry.focus_set()
        root.after(100, self.poll)

    # -- helpers ---------------------------------------------------------
    def append_log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def toggle_smooth(self):
        self.smooth_on = not self.smooth_on
        if self.smooth_on:
            self.smooth_btn.config(relief="sunken", bg="#cfe8cf")
        else:
            self.smooth_btn.config(relief="raised", bg="SystemButtonFace")
        if self.entry.get().strip() and not self.busy:
            self.start()

    def start(self):
        if self.busy:
            return
        word = self.entry.get().strip()
        if not word:
            self.status.config(text="請先輸入詞語", fg="#c00")
            return
        self.busy = True
        self.btn.config(state="disabled")
        self.status.config(text="查詢中… (第一次查某個字要上網下載, 請稍等)", fg="#000")
        self.result.config(text="")
        self.append_log("【%s】" % word)
        threading.Thread(target=self.worker, args=(word,), daemon=True).start()

    def worker(self, word):
        try:
            res = core.process(word, self.lex, self.net, open_browser=self.open_browser,
                               loop=self.loop_var.get(), smooth=self.smooth_on)
            self.q.put(("done", res))
        except Exception as e:
            self.q.put(("error", "%s: %s" % (type(e).__name__, e)))

    def poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self.append_log(data)
                elif kind == "done":
                    self.finish(data)
                elif kind == "error":
                    self.status.config(text="出錯: " + data, fg="#c00")
                    self.append_log("  [!] " + data)
                    self.reset()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def finish(self, res):
        if res:
            wav, html_path, items, dur = res
            self.result.config(text="   ".join("%s %s" % (c, j or "?") for c, j, _, _ in items))
            self.status.config(text="完成 ✓  %s(%s, %.2f 秒)" % ("已開啟瀏覽器播放  " if self.open_browser else "", wav.name, dur),
                               fg="#060")
        else:
            self.status.config(text="冇結果 (睇下面訊息)", fg="#c00")
        self.reset()

    def reset(self):
        self.busy = False
        self.btn.config(state="normal")
        self.entry.focus_set()
        self.entry.select_range(0, "end")


def main():
    root = tk.Tk()
    App(root, open_browser="--no-browser" not in sys.argv)
    root.mainloop()


if __name__ == "__main__":
    main()
