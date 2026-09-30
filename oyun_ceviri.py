"""
Oyun Ekran Çevirici - HAFİF SÜRÜM (İngilizce -> Türkçe)
Windows'un yerleşik OCR motorunu kullanır (PyTorch yok).

F8 : Ekranı çevir / çeviriyi kapat
F9 : Programdan çık
"""

import ctypes
import os
import queue
import sys
import time
import traceback
import threading
import tkinter as tk
import tkinter.font as tkfont

import keyboard
import mss
import winocr
from PIL import Image
from deep_translator import GoogleTranslator
from deep_translator.exceptions import TooManyRequests

HOTKEY = "f8"
QUIT_KEY = "f9"
KEY_COLOR = "#ff00ff"  # Overlay'de şeffaf olacak renk

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

events = queue.Queue()
results = queue.Queue()
state = {"visible": False, "busy": False, "overlay": None, "note": None}
translator = GoogleTranslator(source="en", target="tr")


def _g(obj, key):
    """winocr sürümüne göre sonuç dict veya nesne olabilir."""
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


def grab_screen():
    with mss.mss() as sct:
        monitor = dict(sct.monitors[1])  # Ana monitör
        shot = sct.grab(monitor)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    return img.convert("RGBA"), monitor


def read_lines(img):
    """Windows OCR ile satırları oku -> [x1, y1, x2, y2, metin]"""
    res = winocr.recognize_pil_sync(img, "en")
    lines = []
    for ln in _g(res, "lines"):
        text = _g(ln, "text").strip()
        rects = [_g(w, "bounding_rect") for w in _g(ln, "words")]
        if not rects or len(text) < 2 or not any(c.isalpha() for c in text):
            continue
        x1 = min(_g(r, "x") for r in rects)
        y1 = min(_g(r, "y") for r in rects)
        x2 = max(_g(r, "x") + _g(r, "width") for r in rects)
        y2 = max(_g(r, "y") + _g(r, "height") for r in rects)
        lines.append([x1, y1, x2, y2, text])
    return lines


def merge_paragraphs(lines):
    """Alt alta gelen yakın satırları tek cümle olarak birleştir."""
    lines.sort(key=lambda l: (l[1], l[0]))
    paras = []
    for l in lines:
        h = max(l[3] - l[1], 1)
        merged = False
        for p in paras[-6:]:
            gap = l[1] - p[3]
            overlap = min(l[2], p[2]) - max(l[0], p[0])
            if -h * 0.3 <= gap <= h * 0.6 and overlap > 0:
                p[0], p[1] = min(p[0], l[0]), min(p[1], l[1])
                p[2], p[3] = max(p[2], l[2]), max(p[3], l[3])
                p[4] += " " + l[4]
                merged = True
                break
        if not merged:
            paras.append(list(l))
    return paras


def translate_one(text):
    """Tek bir metni çevir; hız sınırına takılırsa bekleyip tekrar dene."""
    for attempt in range(4):
        try:
            return translator.translate(text) or text
        except TooManyRequests:
            time.sleep(1.0 * (attempt + 1))
    return text


def translate_all(texts):
    """Metinleri az sayıda istekle çevir (Google hız sınırına takılmamak için)."""
    chunks, cur, cur_len = [], [], 0
    for t in texts:
        if cur and cur_len + len(t) + 1 > 4000:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(t)
        cur_len += len(t) + 1
    if cur:
        chunks.append(cur)

    out = []
    for ch in chunks:
        parts = None
        try:
            r = translator.translate("\n".join(ch))
            cand = r.split("\n") if r else []
            if len(cand) == len(ch):
                parts = cand
        except Exception:
            parts = None
        if parts is None:  # Toplu çeviri tutmadıysa tek tek, yavaşça çevir
            parts = []
            for t in ch:
                parts.append(translate_one(t))
                time.sleep(0.25)
        out.extend(parts)
    return out


def log_error(stage):
    """Hatayı exe'nin yanındaki hata_log.txt dosyasına yaz."""
    try:
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base, "hata_log.txt"), "a", encoding="utf-8") as f:
            f.write(f"[{stage}]\n{traceback.format_exc()}\n\n")
    except Exception:
        pass


def work(img, monitor):
    stage = "OCR"
    try:
        blocks = merge_paragraphs(read_lines(img))
        stage = "Çeviri"
        if blocks:
            translated = translate_all([b[4] for b in blocks])
            for b, t in zip(blocks, translated):
                b[4] = t or b[4]
        results.put((monitor, blocks, None))
    except Exception as e:
        log_error(stage)
        short = f"{type(e).__name__}: {e}"[:200]
        results.put((monitor, [], f"{stage} hatası -> {short}"))


# ---------------- Arayüz ----------------

def kill(win):
    try:
        if win is not None and win.winfo_exists():
            win.destroy()
    except Exception:
        pass
    if state["note"] is win:
        state["note"] = None


def notify(root, text, ms=None):
    kill(state["note"])
    t = tk.Toplevel(root)
    t.overrideredirect(True)
    t.attributes("-topmost", True)
    tk.Label(
        t, text=text, bg="#111111", fg="white",
        font=("Segoe UI", 12, "bold"), padx=16, pady=9, wraplength=900,
    ).pack()
    t.update_idletasks()
    x = (t.winfo_screenwidth() - t.winfo_reqwidth()) // 2
    t.geometry(f"+{x}+20")
    state["note"] = t
    if ms:
        root.after(ms, lambda: kill(t))


def pick_font(text, width, height):
    width = max(width, 80)
    f = None
    for size in range(22, 9, -1):
        f = tkfont.Font(family="Segoe UI", size=-size, weight="bold")
        lines, line = 1, ""
        for word in text.split():
            trial = (line + " " + word).strip()
            if f.measure(trial) <= width:
                line = trial
            else:
                lines += 1
                line = word
        if lines * f.metrics("linespace") <= height * 1.3:
            break
    return f, width


def show_overlay(root, monitor, blocks):
    top = tk.Toplevel(root)
    top.overrideredirect(True)
    top.attributes("-topmost", True)
    top.attributes("-transparentcolor", KEY_COLOR)
    top.geometry(f"{monitor['width']}x{monitor['height']}+{monitor['left']}+{monitor['top']}")
    canvas = tk.Canvas(top, bg=KEY_COLOR, highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    for x1, y1, x2, y2, text in blocks:
        font, w = pick_font(text, x2 - x1, y2 - y1)
        item = canvas.create_text(x1 + 6, y1 + 3, text=text, anchor="nw",
                                  fill="white", font=font, width=w)
        bx1, by1, bx2, by2 = canvas.bbox(item)
        rect = canvas.create_rectangle(bx1 - 6, by1 - 3, bx2 + 6, by2 + 3,
                                       fill="#111111", outline="#666666")
        canvas.tag_lower(rect, item)

    state["overlay"] = top
    state["visible"] = True


def hide_overlay():
    kill(state["overlay"])
    state["overlay"] = None
    state["visible"] = False


def main():
    root = tk.Tk()
    root.withdraw()
    keyboard.add_hotkey(HOTKEY, lambda: events.put("toggle"))
    keyboard.add_hotkey(QUIT_KEY, lambda: events.put("quit"))
    notify(root, f"Oyun Çevirici hazır  •  {HOTKEY.upper()}: çevir / kapat  •  {QUIT_KEY.upper()}: çıkış", 4000)

    def poll():
        try:
            while True:
                ev = events.get_nowait()
                if ev == "quit":
                    keyboard.unhook_all()
                    root.destroy()
                    return
                if ev == "toggle":
                    if state["visible"]:
                        hide_overlay()
                    elif not state["busy"]:
                        img, monitor = grab_screen()  # bildirimden ÖNCE yakala
                        state["busy"] = True
                        notify(root, "Çevriliyor...")
                        threading.Thread(target=work, args=(img, monitor), daemon=True).start()
        except queue.Empty:
            pass

        try:
            monitor, blocks, err = results.get_nowait()
            state["busy"] = False
            kill(state["note"])
            if err:
                notify(root, err, 10000)
            elif blocks:
                show_overlay(root, monitor, blocks)
            else:
                notify(root, "Çevrilecek yazı bulunamadı", 2000)
        except queue.Empty:
            pass

        root.after(50, poll)

    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
