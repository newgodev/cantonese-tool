#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cantonese.py - 用中大「粵語審音配詞字庫」(Lexi-can) 聽中文詞語的粵語讀音

用法:
    python cantonese.py              # 互動模式: 輸入詞語, 按 Enter
    python cantonese.py 魯迪烏斯      # 只處理一個詞
    python cantonese.py --no-browser 魯迪烏斯

指定讀音 (多音字): 在字後面加 [拼音], 例如  銀行[hong4]

只用 Python 標準庫 (Python 3.8+), 不需要 pip 安裝任何套件, 不需要 ffmpeg.
"""
import argparse
import array
import gzip
import html
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
import webbrowser
import zlib
from pathlib import Path

# ---------------------------------------------------------------- settings
BASE = "https://humanum.arts.cuhk.edu.hk/Lexis/lexi-can/"
WAYBACK = "https://web.archive.org/web/2024id_/"   # fallback if CUHK site unreachable
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 15
GAP_SEC = 0.12          # silence between characters
TRIM = True             # trim leading/trailing silence of each clip

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
SOUND_DIR = CACHE / "sound"
READINGS_JSON = CACHE / "readings.json"
OUTPUT = HERE / "output"

JP_RE = re.compile(r"^[a-z]{1,6}[1-6]$")

# ---------------------------------------------------------------- helpers

def setup_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if os.name == "nt":
        try:
            os.system("")   # enable ANSI on Windows 10+ (harmless)
        except Exception:
            pass


_LOGGER = [None]   # GUI can redirect messages here (see set_logger)


def set_logger(fn):
    """fn(str) receives every status/warning line instead of print()."""
    _LOGGER[0] = fn


def say(msg=""):
    if _LOGGER[0]:
        _LOGGER[0](msg)
    else:
        try:
            print(msg)
        except Exception:      # e.g. pythonw.exe has no console
            pass


def warn(msg):
    say("  [!] " + msg)


class Net:
    """HTTP fetching with UA, retries, gzip, and a Wayback Machine fallback."""

    def __init__(self):
        self.live_ok = True       # set False after the CUHK site fails once
        self.told_fallback = False

    def _get(self, url, retries=2):
        last = None
        for i in range(retries):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": UA,
                    "Accept": "*/*",
                    "Accept-Language": "zh-HK,zh-TW;q=0.9,zh;q=0.8,en;q=0.5",
                    "Referer": BASE,
                })
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    data = r.read()
                    enc = (r.headers.get("Content-Encoding") or "").lower()
                if enc == "gzip" or data[:2] == b"\x1f\x8b":
                    try:
                        data = gzip.decompress(data)
                    except OSError:
                        pass
                elif enc == "deflate":
                    data = zlib.decompress(data)
                return data
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise
                last = e
            except (urllib.error.URLError, socket.timeout, ssl.SSLError,
                    ConnectionError, OSError) as e:
                last = e
            time.sleep(0.6 * (i + 1))
        raise last

    def get(self, path, validate=None):
        """Fetch BASE+path from CUHK; if that fails, try the Wayback Machine.
        validate(data) -> bool decides whether a response is acceptable."""
        errors = []
        if self.live_ok:
            try:
                data = self._get(BASE + path)
                if validate is None or validate(data):
                    return data
                errors.append("CUHK: unexpected content")
            except urllib.error.HTTPError as e:
                errors.append("CUHK: HTTP %s" % e.code)
            except Exception as e:
                errors.append("CUHK: %s" % e)
                self.live_ok = False     # don't wait on timeouts for every char
        if not self.told_fallback:
            warn("連唔到中大網站, 改用 Internet Archive 備份 (web.archive.org)")
            self.told_fallback = True
        try:
            data = self._get(WAYBACK + BASE + path)
            if validate is None or validate(data):
                return data
            errors.append("Wayback: unexpected/mismatched content")
        except Exception as e:
            errors.append("Wayback: %s" % e)
        raise IOError("; ".join(errors))


# ---------------------------------------------------------------- lookup

def is_cjk(ch):
    o = ord(ch)
    return (0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF or
            0xF900 <= o <= 0xFAFF or 0x20000 <= o <= 0x3134F)


def big5_bytes(ch):
    for codec in ("big5", "big5hkscs"):
        try:
            return ch.encode(codec)
        except UnicodeEncodeError:
            continue
    return None


def parse_search_page(text):
    """Return list of readings: [{'jp':..., 'examples':[...], 'note':...}]"""
    readings = []
    rows = re.split(r"<tr>\s*<td nowrap align=center>", text)[1:]
    for r in rows:
        m = re.search(r"sound\.php\?s=([a-z]+[1-6])", r)
        if not m:
            continue
        jp = m.group(1)
        last = r.split("</select></td>")[-1].split("</tr>")[0]
        divs = re.findall(r"<div[^>]*>(.*?)</div>", last, re.S)
        ex_text = " , ".join(re.sub(r"<a .*?</a>", "", d, flags=re.S) for d in divs)
        ex_text = html.unescape(re.sub(r"<[^>]+>", "", ex_text))
        ex_text = re.sub(r"\([^)]*\)|（[^）]*）", "", ex_text)
        examples = [e.strip() for e in re.split(r"[,，]", ex_text) if e.strip()]
        note = re.sub(r"<div.*?</div>", "", last, flags=re.S)
        note = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", note)).split())
        if jp not in [x["jp"] for x in readings]:
            readings.append({"jp": jp, "examples": examples, "note": note})
    # fallback: any sound links at all
    if not readings:
        for jp in re.findall(r"sound\.php\?s=([a-z]+[1-6])", text):
            if jp not in [x["jp"] for x in readings]:
                readings.append({"jp": jp, "examples": [], "note": ""})
    return readings


def choose_reading(readings):
    """Most common reading ~ the one with the most example words (ties: first)."""
    best = max(range(len(readings)), key=lambda i: (len(readings[i]["examples"]), -i))
    return readings[best]["jp"]


class Lexicon:
    def __init__(self, net):
        self.net = net
        self.cache = {}
        if READINGS_JSON.exists():
            try:
                self.cache = json.loads(READINGS_JSON.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}

    def save(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        READINGS_JSON.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1),
                                 encoding="utf-8")

    def lookup(self, ch):
        """Return list of readings ([] if not found). Raises IOError on network failure."""
        if ch in self.cache:
            return self.cache[ch]
        b = big5_bytes(ch)
        if b is None:
            return None
        q = urllib.parse.quote(b, safe="")
        big5hex = b.hex().upper()

        def ok(data):
            t = data.decode("big5hkscs", "replace")
            if "粵語審音配詞字庫" not in t and "sound.php" not in t:
                return False
            # the Wayback Machine ignores letter case in URLs, so it can return the
            # page of a *different* character; check the Big5 code shown on the page
            m = re.search(r"大五碼:</td>\s*<td[^>]*>([0-9A-Fa-f]{4})", t)
            if m and m.group(1).upper() != big5hex:
                return False
            if not m and "sound.php" in t:
                m2 = re.search(r"class=w>([^<]+)<", t)
                if m2 and m2.group(1).strip() != ch:
                    return False
            return True

        data = self.net.get("search.php?q=" + q, validate=ok)
        readings = parse_search_page(data.decode("big5hkscs", "replace"))
        self.cache[ch] = readings
        self.save()
        return readings


# ---------------------------------------------------------------- audio

def is_wav(data):
    return len(data) > 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def get_sound(net, jp):
    """Return Path of cached WAV for jyutping jp (downloads if necessary)."""
    SOUND_DIR.mkdir(parents=True, exist_ok=True)
    p = SOUND_DIR / (jp + ".wav")
    if p.exists() and p.stat().st_size > 44:
        return p
    data = None
    try:
        data = net.get("sound/%s.wav" % jp, validate=is_wav)
    except Exception as e1:
        # sound.php is an HTML page embedding the real file; parse it in case the path changes
        try:
            page = net.get("sound.php?s=" + jp).decode("big5hkscs", "replace")
            m = re.search(r'(?:SRC|src)="([^"]+\.(?:wav|mp3))"', page)
            if m:
                data = net.get(urllib.parse.urljoin("sound.php", m.group(1)), validate=is_wav)
        except Exception:
            pass
        if data is None:
            raise IOError("下載唔到 %s 的聲音檔: %s" % (jp, e1))
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)
    return p


def read_wav_mono16(path):
    """Read a PCM WAV -> (array('h') mono 16-bit samples, framerate). Pure stdlib."""
    with wave.open(str(path), "rb") as w:
        nch, sw, fr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 1:
        s = [(b - 128) << 8 for b in raw]
    elif sw == 2:
        a = array.array("h")
        a.frombytes(raw[: len(raw) // 2 * 2])
        if sys.byteorder == "big":
            a.byteswap()
        s = a.tolist()
    elif sw == 3:
        s = [int.from_bytes(raw[i + 1:i + 3], "little", signed=True) for i in range(0, len(raw) - 2, 3)]
    elif sw == 4:
        a = array.array("i")
        a.frombytes(raw[: len(raw) // 4 * 4])
        if sys.byteorder == "big":
            a.byteswap()
        s = [v >> 16 for v in a]
    else:
        raise ValueError("unsupported sample width %d" % sw)
    if nch > 1:
        s = [sum(s[i:i + nch]) // nch for i in range(0, len(s) - nch + 1, nch)]
    return s, fr


def resample(samples, src, dst):
    if src == dst or not samples:
        return samples
    ratio = src / dst
    n_out = int(len(samples) / ratio)
    out, last = [], len(samples) - 1
    for i in range(n_out):
        x = i * ratio
        j = int(x)
        f = x - j
        a = samples[j]
        b = samples[j + 1] if j < last else a
        out.append(int(a + (b - a) * f))
    return out


def trim_silence(s, rate, rel=0.03, margin=0.05):
    if not s:
        return s
    peak = max(abs(v) for v in s)
    if peak == 0:
        return s
    th = max(int(peak * rel), 300)
    start = next((i for i, v in enumerate(s) if abs(v) > th), 0)
    end = next((i for i in range(len(s) - 1, -1, -1) if abs(s[i]) > th), len(s) - 1)
    m = int(margin * rate)
    return s[max(0, start - m): min(len(s), end + m + 1)]


def fade(s, rate, ms=8):
    n = min(len(s) // 2, int(rate * ms / 1000))
    if n <= 0:
        return s
    for i in range(n):
        g = i / n
        s[i] = int(s[i] * g)
        s[-1 - i] = int(s[-1 - i] * g)
    return s


def peak_normalize(s, target=29200):
    """Scale a clip so its peak is near target (~0.89 of full 16-bit)."""
    if not s:
        return s
    peak = max(abs(v) for v in s)
    if peak == 0:
        return s
    g = target / peak
    return [int(max(-32768, min(32767, v * g))) for v in s]


def concat_wavs(paths, out_path, gap=GAP_SEC, trim=TRIM, smooth=False):
    clips = [read_wav_mono16(p) for p in paths]
    rate = max(fr for _, fr in clips)              # common output rate
    out = array.array("h")
    silence = [0] * int((0 if smooth else gap) * rate)
    xf = int(0.045 * rate) if smooth else 0        # 45ms crossfade between characters
    fade_ms = 12 if smooth else 8
    for k, (s, fr) in enumerate(clips):
        s = resample(s, fr, rate)
        if trim:
            s = trim_silence(s, rate, rel=0.02 if smooth else 0.03,
                             margin=0.03 if smooth else 0.05)
        s = list(s)
        if smooth:
            s = peak_normalize(s)
        s = fade(s, rate, ms=fade_ms)
        if k:
            if xf and len(out) >= xf and len(s) >= xf:
                last = xf - 1 or 1
                for i in range(xf):
                    a = out[-xf + i]
                    b = s[i]
                    t = i / last
                    out[-xf + i] = max(-32768, min(32767, int(a * (1.0 - t) + b * t)))
                s = s[xf:]
            elif silence:
                out.extend(silence)
        out.extend(max(-32768, min(32767, v)) for v in s)
    if smooth and out:
        peak = max(abs(int(v)) for v in out)
        if peak > 0:
            g = 29200 / peak
            if abs(g - 1) > 0.02:
                for i, v in enumerate(out):
                    out[i] = max(-32768, min(32767, int(v * g)))
    if sys.byteorder == "big":
        out.byteswap()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(out.tobytes())
    return len(out) / rate


# ---------------------------------------------------------------- html

def safe_name(word):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\[\]]', "", word).strip(" .")
    return name or "word"


def rel_url(target, base_dir):
    rel = os.path.relpath(str(target), str(base_dir)).replace(os.sep, "/")
    return urllib.parse.quote(rel)


def make_html(word, items, wav_path, html_path, loop=False):
    d = html_path.parent
    cells = []
    for ch, jp, alts, snd in items:
        btn = ""
        if snd:
            btn = ('<button onclick="play(\'%s\')" title="%s">&#9654;</button>'
                   % (rel_url(snd, d), html.escape(jp)))
        alt = ""
        if alts:
            alt = '<div class="alt">其他: %s</div>' % html.escape(" / ".join(alts))
        cells.append('<div class="c"><div class="ch">%s</div><div class="jp">%s</div>%s%s</div>'
                     % (html.escape(ch), html.escape(jp or "?"), btn, alt))
    jps = " ".join("%s %s" % (ch, jp or "?") for ch, jp, _, _ in items)
    doc = """<!DOCTYPE html>
<html lang="zh-HK"><head><meta charset="utf-8">
<title>{title} - 粵語讀音</title>
<style>
body {{ font-family: "Microsoft JhengHei","PingFang HK","Noto Sans CJK TC",sans-serif;
       text-align:center; background:#fffaf0; margin:40px; }}
.word {{ display:flex; justify-content:center; gap:28px; flex-wrap:wrap; margin:20px 0 30px; }}
.c {{ display:flex; flex-direction:column; align-items:center; }}
.ch {{ font-size:96px; line-height:1.1; }}
.jp {{ font-size:28px; color:#b22222; font-family:Consolas,monospace; margin:4px 0 8px; }}
.alt {{ font-size:13px; color:#888; margin-top:4px; }}
button {{ font-size:18px; padding:4px 14px; cursor:pointer; border-radius:8px; border:1px solid #bbb; background:#fff; }}
audio {{ width:420px; max-width:90%; }}
.big {{ font-size:22px; padding:8px 26px; margin:12px; }}
.src {{ color:#999; font-size:12px; margin-top:40px; }}
</style></head><body>
<div class="word">{cells}</div>
<audio id="all" controls autoplay {loopattr} src="{wav}"></audio><br>
<label style="font-size:18px;margin:8px;">
<input type="checkbox" id="loopbox" {loopchecked} onchange="document.getElementById('all').loop=this.checked"> 循環播放</label>
<button class="big" onclick="document.getElementById('all').play()">&#9654; 播放全詞</button>
<p style="font-size:20px">{jps}</p>
<div class="src">讀音及聲音: 香港中文大學 粵語審音配詞字庫 (Lexi-can)</div>
<script>
function play(u) {{ var a = new Audio(u); a.play(); }}
</script>
</body></html>
""".format(title=html.escape(word), cells="\n".join(cells),
           wav=rel_url(wav_path, d), jps=html.escape(jps),
           loopattr="loop" if loop else "",
           loopchecked="checked" if loop else "")
    html_path.write_text(doc, encoding="utf-8")


def open_in_browser(path):
    try:
        if os.name == "nt":
            os.startfile(str(path))          # handles Chinese paths natively
        else:
            webbrowser.open(path.resolve().as_uri())
    except Exception:
        try:
            webbrowser.open(path.resolve().as_uri())
        except Exception as e:
            warn("開唔到瀏覽器: %s  (請自己打開 %s)" % (e, path))


# ---------------------------------------------------------------- main logic

def tokenize(word):
    """Split input into (char, forced_jp) pairs. '行[hong4]' forces a reading."""
    out = []
    for m in re.finditer(r"(.)(?:\[([a-zA-Z]+[1-6])\]|\(([a-zA-Z]+[1-6])\))?", word):
        out.append((m.group(1), (m.group(2) or m.group(3) or "").lower() or None))
    return out


def process(word, lex, net, open_browser=True, gap=GAP_SEC, loop=False, smooth=False):
    word = word.strip()
    if not word:
        return None
    items, sounds = [], []
    display = ""
    for ch, forced in tokenize(word):
        if ch.isspace():
            continue
        if not is_cjk(ch):
            warn("略過非中文字元: %r" % ch)
            continue
        try:
            readings = lex.lookup(ch)
        except Exception as e:
            warn("查唔到「%s」的讀音 (網絡/網站問題): %s" % (ch, e))
            items.append((ch, None, [], None)); display += ch
            continue
        if readings is None:
            warn("「%s」唔係 Big5 字 (可能係簡體字/罕見字), 略過" % ch)
            continue
        if not readings:
            warn("字庫冇「%s」呢個字, 略過" % ch)
            items.append((ch, None, [], None)); display += ch
            continue
        jps = [r["jp"] for r in readings]
        jp = forced if forced else choose_reading(readings)
        if forced and forced not in jps:
            warn("「%s」字庫冇 %s 呢個讀音 (有: %s), 照用 %s" % (ch, forced, " ".join(jps), forced))
        alts = [x for x in jps if x != jp]
        snd = None
        try:
            snd = get_sound(net, jp)
        except Exception as e:
            warn(str(e))
        items.append((ch, jp, alts, snd)); display += ch
        if snd:
            sounds.append(snd)

    if not items:
        say("  冇可以讀的中文字。")
        return None

    # print readings
    say("  " + "  ".join("%s %s" % (c, j or "?") for c, j, _, _ in items))
    for c, j, alts, _ in items:
        if alts:
            say("    (「%s」其他讀音: %s  — 可輸入 %s[%s] 指定)" % (c, " ".join(alts), c, alts[0]))

    if not sounds:
        say("  冇聲音檔, 唔整 WAV。")
        return None
    OUTPUT.mkdir(parents=True, exist_ok=True)
    name = safe_name(re.sub(r"\[[^\]]*\]|\([a-zA-Z]+[1-6]\)", "", word))
    wav_path = OUTPUT / (name + ".wav")
    html_path = OUTPUT / (name + ".html")
    dur = concat_wavs(sounds, wav_path, gap=gap, smooth=smooth)
    make_html(display, items, wav_path, html_path, loop=loop)
    say("  已輸出: %s (%.2f 秒)" % (wav_path, dur))
    say("          %s" % html_path)
    if open_browser:
        open_in_browser(html_path)
    return wav_path, html_path, items, dur


def main():
    setup_console()
    ap = argparse.ArgumentParser(description="粵語讀音 (中大 粵語審音配詞字庫)")
    ap.add_argument("words", nargs="*", help="要查的詞語 (留空 = 互動模式)")
    ap.add_argument("--no-browser", action="store_true", help="唔好自動開瀏覽器")
    ap.add_argument("--gap", type=float, default=GAP_SEC, help="字與字之間停頓秒數 (預設 0.12)")
    ap.add_argument("--loop", action="store_true", help="網頁播放器循環播放")
    ap.add_argument("--smooth", action="store_true", help="字與字交叉淡化並將音量正規化，聽起來更順")
    args = ap.parse_args()

    net = Net()
    lex = Lexicon(net)
    if args.words:
        for w in args.words:
            print("【%s】" % w)
            process(w, lex, net, not args.no_browser, args.gap, loop=args.loop, smooth=args.smooth)
        return
    print("粵語讀音工具 — 輸入繁體中文詞語後按 Enter (直接按 Enter 或輸入 q 離開)")
    print("多音字可以指定讀音, 例如: 銀行[hong4]\n")
    while True:
        try:
            w = input("詞語> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not w or w.lower() in ("q", "quit", "exit"):
            break
        try:
            process(w, lex, net, not args.no_browser, args.gap, loop=args.loop, smooth=args.smooth)
        except KeyboardInterrupt:
            print("\n  (已取消)")
        except Exception as e:
            warn("出錯: %s" % e)
        print()


if __name__ == "__main__":
    main()
