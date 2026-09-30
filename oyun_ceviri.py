"""
Oyun Ekran Çevirici - HAFİF SÜRÜM (İngilizce -> Türkçe)
Windows'un yerleşik OCR motorunu kullanır (PyTorch yok).

F8 : Ekranı çevir / çeviriyi kapat
F9 : Programdan çık
"""

import ctypes
import json
import os
import queue
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import threading
import tkinter as tk
import tkinter.font as tkfont

import keyboard
import mss
import winocr
from PIL import Image

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
state = {"visible": False, "busy": False, "overlay": None, "note": None, "job": 0, "started": 0.0, "stage": ""}


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


class RateLimited(Exception):
    pass


def gtranslate(text, timeout=6):
    """Google'ın ücretsiz çeviri ucuna zaman sınırlı istek at."""
    url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=tr&dt=t"
    data = urllib.parse.urlencode({"q": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            raise RateLimited(f"Google hız sınırı uyguladı (HTTP {e.code}). Birkaç dakika bekle.")
        raise
    try:
        parsed = json.loads(body)
    except ValueError:
        raise RateLimited("Google beklenmeyen yanıt verdi (geçici engel olabilir). Birkaç dakika bekle.")
    return "".join(seg[0] for seg in parsed[0] if seg and seg[0])


def translate_one(text, deadline):
    """Tek metni çevir; hız sınırında kısa bekleyip dener, süre dolunca None döner."""
    while time.time() < deadline:
        try:
            return gtranslate(text)
        except RateLimited:
            time.sleep(1.5)
        except Exception:
            return None
    return None


def translate_all(texts, deadline):
    """Metinleri az istekle çevir. Çevrilemeyenler None olarak kalır."""
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
        parts, limited = None, False
        for _ in range(2):
            if time.time() >= deadline:
                break
            try:
                cand = gtranslate("\n".join(ch)).split("\n")
                if len(cand) == len(ch):
                    parts = cand
                limited = False
                break
            except RateLimited:
                limited = True
                time.sleep(2)
            except Exception:
                break
        if limited and parts is None:
            raise RateLimited("Google hız sınırı uyguladı. Birkaç dakika bekleyip tekrar dene.")
        if parts is None:  # Toplu çeviri satırları tutturamadıysa tek tek çevir
            parts = []
            for t in ch:
                parts.append(translate_one(t, deadline) if time.time() < deadline else None)
                time.sleep(0.25)
        out.extend(parts)
    return out


def norm(x):
    return "".join(c for c in x.lower() if c.isalnum())


def _log_path():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "hata_log.txt")


def log(msg):
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S") + "  " + msg + "\n")
    except Exception:
        pass


def log_error(stage):
    log(f"HATA [{stage}]\n{traceback.format_exc()}")


def work(job, img, monitor):
    stage = "OCR"
    state["stage"] = stage
    try:
        t0 = time.time()
        log("OCR basladi")
        lines = read_lines(img)
        blocks = merge_paragraphs(lines)
        log(f"OCR bitti: {len(lines)} satir, {len(blocks)} blok, {time.time() - t0:.1f} sn")

        stage = "çeviri"
        state["stage"] = stage
        kept = []
        if blocks:
            t1 = time.time()
            log("Ceviri basladi")
            translated = translate_all([b[4] for b in blocks], time.time() + 18)
            log(f"Ceviri bitti: {sum(1 for t in translated if t)}/{len(translated)} blok, {time.time() - t1:.1f} sn")
            if not any(translated):
                raise RuntimeError("Çeviri sunucusundan yanıt alınamadı (internet veya Google engeli)")
            for b, t in zip(blocks, translated):
                if not t or norm(t) == norm(b[4]):
                    continue  # çevrilemedi veya zaten Türkçe -> dokunma
                b[4] = t
                kept.append(b)
        results.put((job, monitor, kept, None))
    except Exception as e:
        log_error(stage)
        short = f"{type(e).__name__}: {e}"[:200]
        results.put((job, monitor, [], f"{stage} hatası -> {short}"))


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
                        state["job"] += 1
                        state["started"] = time.time()
                        notify(root, "Çevriliyor...")
                        threading.Thread(
                            target=work, args=(state["job"], img, monitor), daemon=True
                        ).start()
        except queue.Empty:
            pass

        try:
            job, monitor, blocks, err = results.get_nowait()
            if job == state["job"]:  # eski/iptal edilmiş işlerin sonucunu yok say
                state["busy"] = False
                kill(state["note"])
                if err:
                    notify(root, err, 10000)
                elif blocks:
                    show_overlay(root, monitor, blocks)
                else:
                    notify(root, "Çevrilecek İngilizce yazı bulunamadı", 2500)
        except queue.Empty:
            pass

        # Bekçi: 30 saniyeden uzun sürerse pes et, takılı kalma
        if state["busy"] and time.time() - state["started"] > 30:
            state["busy"] = False
            state["job"] += 1
            notify(root, f"Zaman aşımı ({state['stage']} aşamasında takıldı). Ayrıntı: hata_log.txt", 6000)

        root.after(50, poll)

    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
