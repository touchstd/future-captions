#!/usr/bin/env python3
"""Render captions for Future Captions.

Outputs (in --out-dir):
  <name>_captioned.mp4          the original video with captions burned in (H.264 + AAC)
  <name>_captions_alpha.mov     captions only, transparent background (QuickTime Animation, ARGB alpha)
  <name>_captions_alpha.webm    optional (--webm): VP9 with alpha, for web/After Effects alternatives
  <name>.srt                    plain subtitle file, same caption breaks
  <name>.zip                    with --zip-to DIR: all of the above in DIR/<name>.zip (folder <name>/ inside)

Usage:
  python render.py --video IN.mp4 --transcript transcript.json --preset 01 --lines 1 --out-dir OUT
  python render.py --demo --preset 02 --lines 2 --out-dir OUT      # "The Future is Now" preview clip

Presets: 01 Karaoke box | 02 Elegant serif (blur + motion) | 03 Cinema (hard cut) | 04 Word by word
         05 Dynamic karaoke (stretchy tilting box, hopping words) | 06 Cartoon (comic pop, squash & stretch, wobble)
Text:    --punct keep|hide   --case original|lower|upper   --emphasis "grand prize,wow"  (05/06 highlight)
Tweaks:  --scale 1.2  --accent "#FF4D6D"  --text-color "#FFFFFF"  --position bottom|middle|top  --webm
"""
import argparse
import json
import math
import os
import random
import zipfile
import subprocess
import sys
import unicodedata
from fractions import Fraction

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fc_utils import fmt_time, load, normalize  # noqa: E402

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "fonts")

PRESETS = {
    "01": dict(name="Karaoke", font="Montserrat-Variable.ttf", weight="Bold", size=0.060, gap=0.22, max_w=0.80, max_dur=3.5),
    "02": dict(name="Elegant", font="PlayfairDisplay-Variable.ttf", weight="Medium", size=0.068, max_w=0.80, max_dur=4.0),
    "03": dict(name="Cinema", font="Inter-Variable.ttf", weight="Medium", size=0.048, max_w=0.86, max_dur=6.0),
    "04": dict(name="Word by word", font="Montserrat-Variable.ttf", weight="Bold", size=0.090, max_w=0.90, max_dur=99),
    "05": dict(name="Dynamic karaoke", font="BricolageGrotesque-Variable.ttf", weight="ExtraBold", size=0.070,
               max_w=0.78, max_dur=3.0, gap=0.26, accent="#FFD23F"),
    "06": dict(name="Cartoon", font="Bangers-Regular.ttf", weight=None, size=0.088, max_w=0.80, max_dur=3.0,
               gap=0.30, accent="#FFE14D"),
}
DEFAULT_ACCENT = {"01": "#7F77DD"}

_font_cache = {}


def font(file, size, weight):
    key = (file, size, weight)
    if key not in _font_cache:
        f = ImageFont.truetype(os.path.join(FONT_DIR, file), size)
        if weight:
            try:
                f.set_variation_by_name(weight)
            except Exception:
                pass
        _font_cache[key] = f
    return _font_cache[key]


def hex_rgba(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


# ---------------------------------------------------------------- video probing
def probe(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height,avg_frame_rate,r_frame_rate:stream_tags=rotate:stream_side_data=rotation"
        ":format=duration", "-of", "json", path])
    j = json.loads(out)
    st = j["streams"][0]
    w, h = int(st["width"]), int(st["height"])
    rot = 0
    for sd in st.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = rot or int(float((st.get("tags") or {}).get("rotate", 0)))
    if abs(rot) % 180 == 90:
        w, h = h, w
    fps = None
    for k in ("avg_frame_rate", "r_frame_rate"):
        try:
            f = Fraction(st.get(k, "0/0"))
            if 1 <= f <= 120:
                fps = f
                break
        except (ZeroDivisionError, ValueError):
            pass
    fps = fps or Fraction(30)
    return w, h, fps, float(j["format"]["duration"])


# ---------------------------------------------------------------- caption building
class Chunk:
    def __init__(self, words):
        self.words = words          # list of dicts: word, start, end
        self.lines = [words]        # list of lists
        self.start = words[0]["start"]
        self.end = words[-1]["end"]


def build_chunks(segments, P, W, lines, fnt, size):
    space = fnt.getlength(" ") + P.get("gap", 0) * size
    max_line = P["max_w"] * W

    def width(ws):
        return sum(fnt.getlength(w["word"]) for w in ws) + space * (len(ws) - 1)

    def best_split(ws):
        if lines == 1 or len(ws) < 3:
            return [ws], width(ws) <= max_line
        best = None
        for k in range(1, len(ws)):
            a, b = width(ws[:k]), width(ws[k:])
            score = max(a, b) + 0.15 * abs(a - b)
            if best is None or score < best[0]:
                best = (score, k, max(a, b))
        _, k, mw = best
        return [ws[:k], ws[k:]], mw <= max_line

    chunks = []
    for seg in segments:
        cur = []
        for w in seg["words"]:
            if cur:
                gap = w["start"] - cur[-1]["end"]
                _, fits = best_split(cur + [w])
                too_slow = w["end"] - cur[0]["start"] > P["max_dur"]
                sentence = cur[-1]["word"][-1:] in ".?!" and len(cur) >= 2
                if not fits or too_slow or gap > 0.7 or sentence:
                    chunks.append(cur)
                    cur = []
            cur.append(w)
        if cur:
            chunks.append(cur)

    out = []
    for ws in chunks:
        c = Chunk(ws)
        c.lines, _ = best_split(ws)
        out.append(c)
    for i, c in enumerate(out):  # hold each caption a little, never overlapping the next
        nxt = out[i + 1].start if i + 1 < len(out) else c.end + 1.0
        c.end = min(max(c.end + 0.35, c.start + 0.8), nxt)
    return out


# ---------------------------------------------------------------- layout + drawing
class Layout:
    """Per-chunk geometry in full-frame coordinates, plus a crop region for fast drawing."""

    def __init__(self, chunk, fnt, size, W, H, position, gap=0.0):
        space = fnt.getlength(" ") + gap * size
        lh = size * 1.28
        if position == "top":
            first_base = H * 0.10 + size
        elif position == "middle":
            first_base = H / 2 + size * 0.35 - (len(chunk.lines) - 1) * lh / 2
        else:
            margin = H * (0.20 if H > W else 0.085)
            first_base = H - margin - (len(chunk.lines) - 1) * lh
        self.words = []  # (word dict, x, baseline, width)
        for li, line in enumerate(chunk.lines):
            lw = sum(fnt.getlength(w["word"]) for w in line) + space * (len(line) - 1)
            x = (W - lw) / 2
            base = first_base + li * lh
            for w in line:
                ww = fnt.getlength(w["word"])
                self.words.append((w, x, base, ww))
                x += ww + space
        pad = size * 1.7
        xs = [x for _, x, _, _ in self.words] + [x + ww for _, x, _, ww in self.words]
        bs = [b for _, _, b, _ in self.words]
        self.x0 = int(max(0, min(xs) - pad))
        self.y0 = int(max(0, min(bs) - size - pad))
        self.x1 = int(min(W, max(xs) + pad))
        self.y1 = int(min(H, max(bs) + pad))
        self.size = (self.x1 - self.x0, self.y1 - self.y0)

    def local(self, x, y):
        return x - self.x0, y - self.y0


def text_layer(layout, items, fnt, size, color, shadow=0.55, stroke=0):
    """White text with a soft drop shadow (keeps legibility on bright footage)."""
    img = Image.new("RGBA", layout.size, (0, 0, 0, 0))
    sh = Image.new("RGBA", layout.size, (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(img), ImageDraw.Draw(sh)
    off = max(1, size * 0.04)
    for w, x, base, _ in items:
        lx, ly = layout.local(x, base)
        ds.text((lx + off, ly + off), w["word"], font=fnt, fill=(0, 0, 0, int(255 * shadow)), anchor="ls",
                stroke_width=int(size * 0.05), stroke_fill=(0, 0, 0, int(255 * shadow)))
        d.text((lx, ly), w["word"], font=fnt, fill=color, anchor="ls",
               stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    sh = sh.filter(ImageFilter.GaussianBlur(size * 0.12))
    return Image.alpha_composite(sh, img)


def fade(img, a):
    if a >= 0.999:
        return img
    r, g, b, al = img.split()
    al = al.point(lambda v: int(v * max(0.0, a)))
    return Image.merge("RGBA", (r, g, b, al))


def ease_out(p):
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) ** 3


def back_out(p, k=1.9):
    p = min(1.0, max(0.0, p))
    return 1 + (k + 1) * (p - 1) ** 3 + k * (p - 1) ** 2


def elastic_out(p):
    p = min(1.0, max(0.0, p))
    if p in (0.0, 1.0):
        return p
    return 2 ** (-9 * p) * math.sin((p * 10 - 0.75) * (2 * math.pi) / 3) + 1


def ease_in(p):
    p = min(1.0, max(0.0, p))
    return p * p * p


def ease_in_out(p):
    p = min(1.0, max(0.0, p))
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


# ---------------------------------------------------------------- text style options
KEEP_PUNCT = set("%$€£#@&")


def strip_punct(w):
    """Hide punctuation but keep what belongs to the word: I'm, bye-bye, 120,877.50, 50%."""
    isp = lambda c: unicodedata.category(c).startswith("P") and c not in KEEP_PUNCT  # noqa: E731
    ch = list(w)
    while ch and isp(ch[0]):
        ch.pop(0)
    while ch and isp(ch[-1]):
        ch.pop()
    out = []
    for k, c in enumerate(ch):
        if isp(c) and c not in "'’-‐":
            if c in ".," and 0 < k < len(ch) - 1 and ch[k - 1].isdigit() and ch[k + 1].isdigit():
                out.append(c)
            continue
        out.append(c)
    return "".join(out)


def apply_text_style(segments, case, punct):
    for seg in segments:
        words = []
        for w in seg["words"]:
            t = strip_punct(w["word"]) if punct == "hide" else w["word"]
            t = t.upper() if case == "upper" else t.lower() if case == "lower" else t
            if t:
                words.append(dict(w, word=t))
        seg["words"] = words
    return [s for s in segments if s["words"]]


def mark_emphasis(segments, phrases):
    norm = lambda x: strip_punct(x).lower()  # noqa: E731
    words = [w for s in segments for w in s["words"]]
    for ph in phrases:
        toks = [norm(t) for t in ph.split() if norm(t)]
        n = len(toks)
        for i in range(len(words) - n + 1):
            if n and [norm(w["word"]) for w in words[i:i + n]] == toks:
                for w in words[i:i + n]:
                    w["emph"] = True


# ---------------------------------------------------------------- per-word sprites
def word_sprite(text, fnt, size, fill, stroke=0, stroke_fill=(17, 17, 17, 255), hard_shadow=None, soft=0.0):
    """A word drawn so that the image centre is the word's visual centre."""
    ww = fnt.getlength(text)
    pad = int(size * 0.6)
    img = Image.new("RGBA", (int(ww + 2 * pad), int(2 * (pad + size))), (0, 0, 0, 0))
    base = img.height / 2 + size * 0.35
    if soft:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((pad, base + size * 0.05), text, font=fnt, fill=(0, 0, 0, int(255 * soft)),
                                anchor="ls", stroke_width=stroke, stroke_fill=(0, 0, 0, int(255 * soft)))
        img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(size * 0.1)))
    d = ImageDraw.Draw(img)
    if hard_shadow:
        dx, dy = hard_shadow
        d.text((pad + dx, base + dy), text, font=fnt, fill=(0, 0, 0, 255), anchor="ls",
               stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    d.text((pad, base), text, font=fnt, fill=fill, anchor="ls", stroke_width=stroke, stroke_fill=stroke_fill)
    return img


def composite_at(canvas, im, x, y):
    """alpha_composite that tolerates sprites hanging off the canvas edge."""
    x, y = int(round(x)), int(round(y))
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1, sy1 = min(im.width, canvas.width - x), min(im.height, canvas.height - y)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    canvas.alpha_composite(im.crop((sx0, sy0, sx1, sy1)), (x + sx0, y + sy0))


def draw_xf(canvas, img, cx, cy, sx=1.0, sy=1.0, angle=0.0, alpha=1.0):
    if sx <= 0.01 or sy <= 0.01 or alpha <= 0.01:
        return
    w, h = max(1, int(img.width * sx)), max(1, int(img.height * sy))
    im = img if (w, h) == img.size else img.resize((w, h), Image.BICUBIC)
    if abs(angle) > 0.05:
        im = im.rotate(angle, resample=Image.BICUBIC, expand=True)
    im = fade(im, alpha)
    composite_at(canvas, im, cx - im.width / 2, cy - im.height / 2)


def word_center(layout, item, size):
    _, x, base, ww = item
    lx, ly = layout.local(x, base)
    return lx + ww / 2, ly - size * 0.35


class Renderer:
    def __init__(self, chunks, preset, W, H, args):
        self.P = PRESETS[preset]
        self.preset = preset
        self.W, self.H = W, H
        self.size = int(min(W, H) * self.P["size"] * args.scale)
        self.fnt = font(self.P["font"], self.size, self.P["weight"])
        self.color = hex_rgba(args.text_color)
        self.accent = hex_rgba(args.accent or self.P.get("accent") or DEFAULT_ACCENT.get(preset, "#7F77DD"))
        self.emph = hex_rgba(args.emph_color)
        self.chunks = chunks
        self.pos = args.position
        self.cache = {}
        self.layouts = [Layout(c, self.fnt, self.size, W, H, self.pos, self.P.get("gap", 0)) for c in chunks]
        if preset == "04":  # each word becomes its own single-word "chunk"
            flat = []
            words = [w for c in chunks for w in c.words]
            for i, w in enumerate(words):
                c = Chunk([w])
                nxt = words[i + 1]["start"] if i + 1 < len(words) else w["end"] + 0.5
                c.end = nxt if nxt - w["end"] < 0.6 else w["end"] + 0.25
                flat.append(c)
            self.chunks = flat
            self.layouts = [Layout(c, self.fnt, self.size, W, H, self.pos, self.P.get("gap", 0)) for c in flat]
        self.starts = [c.start for c in self.chunks]
        self.idx = 0

    def find(self, t):
        while self.idx + 1 < len(self.chunks) and self.chunks[self.idx + 1].start <= t:
            self.idx += 1
        c = self.chunks[self.idx] if self.chunks else None
        if c and c.start <= t < c.end:
            return self.idx
        return None

    def frame(self, t):
        """Returns (cache_key or None, crop image, (x0, y0)) or None when nothing is on screen."""
        i = self.find(t)
        if i is None:
            return None
        c, L = self.chunks[i], self.layouts[i]
        return getattr(self, "p" + self.preset)(i, c, L, t)

    def base(self, i, L, items=None):
        k = ("base", i)
        if k not in self.cache:
            self.cache[k] = text_layer(L, items or L.words, self.fnt, self.size, self.color,
                                       stroke=int(self.size * 0.02) if self.preset == "03" else 0)
        return self.cache[k]

    # 01 Karaoke: rounded box glides to the word being spoken
    def p01(self, i, c, L, t):
        active = 0
        for j, (w, *_r) in enumerate(L.words):
            if w["start"] <= t:
                active = j

        def rect(j):
            _, x, b, ww = L.words[j]
            px, py = self.size * 0.20, self.size * 0.14
            lx, ly = L.local(x, b)
            return (lx - px, ly - self.size * 0.80 - py, lx + ww + px, ly + self.size * 0.22 + py)

        r = rect(active)
        p = (t - L.words[active][0]["start"]) / 0.12
        moving = active > 0 and p < 1
        if moving:
            e, r0 = ease_out(p), rect(active - 1)
            r = tuple(a + (b - a) * e for a, b in zip(r0, r))
        key = None if moving else ("k", i, active)
        if key and key in self.cache:
            return key, self.cache[key], (L.x0, L.y0)
        img = Image.new("RGBA", L.size, (0, 0, 0, 0))
        ImageDraw.Draw(img).rounded_rectangle(r, radius=self.size * 0.18, fill=self.accent)
        img = Image.alpha_composite(img, self.base(i, L))
        if key:
            self.cache[key] = img
        return key, img, (L.x0, L.y0)

    # 02 Elegant: each word rises out of a blur; the caption dissolves upward at the end
    def p02(self, i, c, L, t):
        IN, OUT = 0.45, 0.30
        q = ease_out((t - (c.end - OUT)) / OUT) if t > c.end - OUT else 0.0
        settled = all(t - w["start"] >= IN or t < w["start"] for w, *_r in L.words)
        n_vis = sum(1 for w, *_r in L.words if t >= w["start"])
        key = ("e", i, n_vis) if settled and q == 0 else None
        if key and key in self.cache:
            return key, self.cache[key], (L.x0, L.y0)
        img = Image.new("RGBA", L.size, (0, 0, 0, 0))
        for j, (w, x, b, ww) in enumerate(L.words):
            if t < w["start"]:
                continue
            wk = ("w", i, j)
            if wk not in self.cache:
                self.cache[wk] = text_layer(L, [L.words[j]], self.fnt, self.size, self.color, shadow=0.45)
            layer = self.cache[wk]
            e = ease_out((t - w["start"]) / IN)
            blur = (1 - e) * self.size * 0.16 + q * self.size * 0.12
            dy = (1 - e) * self.size * 0.35 - q * self.size * 0.25
            alpha = e * (1 - q)
            if blur > 0.3:
                layer = layer.filter(ImageFilter.GaussianBlur(blur))
            layer = fade(layer, alpha)
            img.alpha_composite(layer, (0, int(round(dy))) if dy >= 0 else (0, 0),
                                (0, 0) if dy >= 0 else (0, int(round(-dy))))
        if key:
            self.cache[key] = img
        return key, img, (L.x0, L.y0)

    # 03 Cinema: whole caption, hard cut in and out
    def p03(self, i, c, L, t):
        return ("c", i), self.base(i, L), (L.x0, L.y0)

    # 04 Word by word: one word on screen, no animation
    def p04(self, i, c, L, t):
        return ("c", i), self.base(i, L), (L.x0, L.y0)

    # 05 Dynamic karaoke: words hop in with a twist, the box stretches and leans as it slides,
    #    the spoken word jumps; emphasis words turn the box pink and grow; the line exits up and small
    def p05(self, i, c, L, t):
        S = self.size
        IN, JUMP, MOVE, OUT = 0.32, 0.24, 0.20, 0.30
        n = len(L.words)
        active = max([j for j, it in enumerate(L.words) if it[0]["start"] <= t] or [0])
        wa = L.words[active][0]
        pa = (t - wa["start"]) / MOVE
        q = ease_in((t - (c.end - OUT)) / OUT) if t > c.end - OUT else 0.0
        entering = any(0 <= t - (it[0]["start"] - 0.1) < IN for it in L.words)
        static = not entering and t - wa["start"] >= max(JUMP, MOVE) and q == 0
        nvis = sum(1 for it in L.words if t >= it[0]["start"] - 0.1)
        key = ("d", i, active, nvis) if static else None
        if key and key in self.cache:
            return key, self.cache[key], (L.x0, L.y0)
        img = Image.new("RGBA", L.size, (0, 0, 0, 0))

        def box_geo(j):
            cx, cy = word_center(L, L.words[j], S)
            k = 1.08 if L.words[j][0].get("emph") else 1.0
            return cx, cy + S * 0.04, (L.words[j][3] + S * 0.44) * k, S * 1.08 * k

        cx, cy, bw, bh = box_geo(active)
        tilt = 0.0
        if active > 0 and pa < 1:
            px, py, pw, ph = box_geo(active - 1)
            e, st = ease_in_out(pa), math.sin(math.pi * min(1, pa))
            cx, cy = px + (cx - px) * e, py + (cy - py) * e
            bw, bh = (pw + (bw - pw) * e) * (1 + 0.30 * st), (ph + (bh - ph) * e) * (1 - 0.14 * st)
            tilt = -7 * st * (1 if cx >= px else -1)
        elif active == 0 and pa < 1:
            k = back_out(pa / 0.9)
            bw, bh = bw * k, bh * k
        if bw > 2 and bh > 2:
            color = self.emph if wa.get("emph") else self.accent
            bk = ("box", int(bw), int(bh), color)
            if bk not in self.cache:
                b = Image.new("RGBA", (int(bw) + 4, int(bh) + 4), (0, 0, 0, 0))
                ImageDraw.Draw(b).rounded_rectangle((2, 2, int(bw) + 1, int(bh) + 1), radius=S * 0.2, fill=color)
                self.cache[bk] = b
            draw_xf(img, self.cache[bk], cx, cy, angle=tilt)

        for j, it in enumerate(L.words):
            w = it[0]
            e = (t - (w["start"] - 0.1)) / IN
            if e < 0:
                continue
            is_act = j == active
            dark = is_act and (active == 0 or pa >= 0.55)  # darken only once the box is underneath
            sk = ("s5", i, j, dark)
            if sk not in self.cache:
                if dark:
                    self.cache[sk] = word_sprite(w["word"], self.fnt, S, (20, 20, 20, 255))
                else:
                    self.cache[sk] = word_sprite(w["word"], self.fnt, S, self.color, stroke=max(2, int(S * 0.07)),
                                                 soft=0.35)
            wx, wy = word_center(L, it, S)
            sc = 0.55 + 0.45 * back_out(e)
            ang = (1 - ease_out(e)) * (14 if j % 2 else -14)
            dy = (1 - ease_out(e)) * S * 0.45
            if is_act:
                jp = min(1.0, (t - w["start"]) / JUMP)
                dy -= S * 0.16 * math.sin(math.pi * jp)
                sc *= (1 + 0.07 * math.sin(math.pi * jp)) * (1.08 if w.get("emph") else 1.0)
            draw_xf(img, self.cache[sk], wx, wy + dy, sc, sc, ang)

        if q > 0:  # exit: rise and shrink
            out = Image.new("RGBA", L.size, (0, 0, 0, 0))
            draw_xf(out, img, L.size[0] / 2, L.size[1] / 2 - S * 0.6 * q, 1 - 0.3 * q, 1 - 0.3 * q, 0, 1 - q)
            img = out
        if key:
            self.cache[key] = img
        return key, img, (L.x0, L.y0)

    # 06 Cartoon: comic font with hard shadow; words spring in with elastic squash & stretch and a
    #    jaunty tilt, the spoken word turns yellow and wobbles, then words pop out one by one
    def p06(self, i, c, L, t):
        S = self.size
        IN, WOB = 0.55, 0.45
        n = len(L.words)
        active = max([j for j, it in enumerate(L.words) if it[0]["start"] <= t] or [0])
        wa = L.words[active][0]
        exit_start = [c.end - 0.24 - (n - 1 - j) * 0.03 for j in range(n)]
        exiting = t >= min(exit_start)
        entering = any(0 <= t - (it[0]["start"] - 0.05) < IN for it in L.words)
        static = not entering and not exiting and t - wa["start"] >= WOB
        nvis = sum(1 for it in L.words if t >= it[0]["start"] - 0.05)
        key = ("c6", i, active, nvis) if static else None
        if key and key in self.cache:
            return key, self.cache[key], (L.x0, L.y0)
        img = Image.new("RGBA", L.size, (0, 0, 0, 0))
        for j, it in enumerate(L.words):
            w = it[0]
            e = (t - (w["start"] - 0.05)) / IN
            if e < 0:
                continue
            is_act = j == active
            sk = ("s6", i, j, is_act)
            if sk not in self.cache:
                fill = (self.emph if w.get("emph") else self.accent) if is_act else self.color
                self.cache[sk] = word_sprite(w["word"], self.fnt, S, fill, stroke=max(3, int(S * 0.09)),
                                             stroke_fill=(0, 0, 0, 255), hard_shadow=(S * 0.06, S * 0.09))
            rnd = random.Random(i * 1009 + j)
            rest = rnd.uniform(-4, 4)
            start_ang = rnd.choice([-1, 1]) * rnd.uniform(12, 22)
            s = elastic_out(e)
            sq = 0.24 * math.sin(min(e, 1) * 3 * math.pi) * (1 - min(e, 1))
            sx, sy = s * (1 + sq), s * (1 - sq)
            ang = rest + (start_ang - rest) * (1 - ease_out(e))
            dy = (1 - ease_out(e)) * S * 0.25
            if is_act:
                a = (t - w["start"]) / WOB
                big = 1.12 if w.get("emph") else 1.06
                if a < 1:
                    ang += 9 * math.sin(a * 16) * (1 - a)
                    big += 0.08 * (1 - a)
                sx, sy = sx * big, sy * big
            o = (t - exit_start[j]) / 0.24
            if o > 0:  # pop out: small swell, then shrink to nothing
                k = (1 + 0.25 * math.sin(math.pi * min(o, 1))) * (1 - ease_in(o))
                sx, sy = sx * k, sy * k
            wx, wy = word_center(L, it, S)
            draw_xf(img, self.cache[sk], wx, wy + dy, sx, sy, ang)
        if key:
            self.cache[key] = img
        return key, img, (L.x0, L.y0)


# ---------------------------------------------------------------- encoding
# After Effects-ready alpha: QuickTime Animation (qtrle), 32-bit ARGB ("Millions of Colors+") with a straight
# alpha channel. RGB end to end, so there is no color-matrix conversion and colors land exactly; lossless,
# and very small for captions because run-length encoding collapses the transparent areas.
AE_ALPHA = ["-c:v", "qtrle", "-pix_fmt", "argb"]


def verify_ae_alpha(path):
    """Check what After Effects relies on to import the MOV with transparency."""
    problems = []
    j = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=codec_name,pix_fmt", "-of", "json", path]))["streams"][0]
    if j.get("codec_name") != "qtrle":
        problems.append("not QuickTime Animation")
    if j.get("pix_fmt") != "argb":
        problems.append("no alpha channel (expected ARGB)")
    with open(path, "rb") as f:
        head = f.read(4 * 1024 * 1024)
        f.seek(max(0, os.path.getsize(path) - 4 * 1024 * 1024))
        head += f.read()
    i = head.find(b"stsd")
    if i < 0 or int.from_bytes(head[i + 4 + 8 + 8 + 74:i + 4 + 8 + 8 + 76], "big") != 32:
        problems.append("QuickTime depth is not 32-bit (AE would ignore the alpha)")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1", "-vf", "format=rgba",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    if raw and min(raw[3::4]) != 0:
        problems.append("first frame has no transparent pixels")
    if problems:
        print("  WARNING After Effects alpha check failed: " + "; ".join(problems))
        return False
    print("  After Effects alpha check: OK (QuickTime Animation, ARGB straight alpha, 32-bit depth)")
    return True
def render_overlay(R, W, H, fps, duration, out_mov, label=""):
    n = int(math.ceil(duration * fps))
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", *AE_ALPHA, out_mov]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    blank = bytes(W * H * 4)
    last_key, last_bytes = "none", blank
    step = max(1, n // 10)
    for f in range(n):
        t = f / fps
        st = R.frame(float(t))
        if st is None:
            data, last_key = blank, "none"
        else:
            key, crop, (x0, y0) = st
            if key is not None and key == last_key:
                data = last_bytes
            else:
                fr = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                fr.paste(crop, (x0, y0))
                data = fr.tobytes()
            last_key, last_bytes = key, data
        proc.stdin.write(data)
        if f % step == 0:
            print(f"  {label}rendering captions {int(100 * f / n)}%", flush=True)
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit("ERROR: overlay encoding failed")


def composite(video, overlay, out_mp4):
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", video, "-i", overlay, "-filter_complex",
           "[0:v][1:v]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[v]",
           "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-crf", "18", "-preset", "fast",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_mp4]
    if subprocess.run(cmd).returncode != 0:
        sys.exit("ERROR: compositing failed")


def write_srt(chunks, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(chunks, 1):
            text = "\n".join(" ".join(w["word"] for w in line) for line in c.lines)
            f.write(f"{i}\n{fmt_time(c.start, ',')} --> {fmt_time(c.end, ',')}\n{text}\n\n")


def demo_transcript():
    words, t = [], 0.4
    for w in ["The", "Future", "is", "Now"]:
        words.append({"word": w, "start": round(t, 2), "end": round(t + 0.45, 2)})
        t += 0.55
    return {"segments": [{"start": words[0]["start"], "end": words[-1]["end"],
                          "text": "The Future is Now", "words": words}]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video")
    ap.add_argument("--transcript")
    ap.add_argument("--demo", action="store_true", help="Render a 'The Future is Now' preview clip")
    ap.add_argument("--preset", choices=list(PRESETS), required=True)
    ap.add_argument("--lines", type=int, choices=[1, 2], default=1)
    ap.add_argument("--out-dir", default="fc_output")
    ap.add_argument("--name", default=None, help="Base name for output files")
    ap.add_argument("--scale", type=float, default=1.0, help="Caption size multiplier")
    ap.add_argument("--accent", default=None, help="Box / active-word color (preset default if omitted)")
    ap.add_argument("--emph-color", default="#FF4F8B", help="Color for --emphasis words (05/06)")
    ap.add_argument("--emphasis", default="", help='Comma-separated words/phrases to highlight (05/06)')
    ap.add_argument("--punct", choices=["keep", "hide"], default="keep")
    ap.add_argument("--case", choices=["original", "lower", "upper"], default="original")
    ap.add_argument("--text-color", default="#FFFFFF")
    ap.add_argument("--position", choices=["bottom", "middle", "top"], default="bottom")
    ap.add_argument("--webm", action="store_true", help="Also export VP9 WebM with alpha")
    ap.add_argument("--zip-to", default=None, help="Also pack all outputs into DIR/<video name>.zip")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    if args.demo:
        W, H, fps, duration = 1920, 1080, Fraction(30), 3.4
        data = demo_transcript()
        if args.preset == "03" and args.lines == 1:  # show the hard cut between two subtitles
            w = data["segments"][0]["words"]
            data["segments"] = [{"start": p[0]["start"], "end": p[-1]["end"],
                                 "text": " ".join(x["word"] for x in p), "words": p} for p in (w[:2], w[2:])]
        if args.preset in ("05", "06") and not args.emphasis.strip():
            args.emphasis = "Now"
        name = args.name or f"preview_preset{args.preset}_{args.lines}line"
    else:
        if not (args.video and args.transcript):
            sys.exit("ERROR: --video and --transcript are required (or use --demo)")
        W, H, fps, duration = probe(args.video)
        data = normalize(load(args.transcript))
        name = args.name or os.path.splitext(os.path.basename(args.video))[0]

    if args.emphasis.strip():
        mark_emphasis(data["segments"], [p for p in args.emphasis.split(",") if p.strip()])
    data["segments"] = apply_text_style(data["segments"], args.case, args.punct)
    P = PRESETS[args.preset]
    size = int(min(W, H) * P["size"] * args.scale)
    chunks = build_chunks(data["segments"], P, W, args.lines, font(P["font"], size, P["weight"]), size)
    R = Renderer(chunks, args.preset, W, H, args)
    print(f"Preset {args.preset} ({P['name']}), {args.lines} line(s), {W}x{H} @ {float(fps):.3f} fps, "
          f"{len(chunks)} captions")

    overlay = os.path.join(args.out_dir, f"{name}_captions_alpha.mov")
    render_overlay(R, W, H, fps, duration, overlay)

    if args.demo:
        bg = os.path.join(args.out_dir, f"{name}.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"color=c=0x141418:s={W}x{H}:r={fps}:d={duration}", "-i", overlay,
                        "-filter_complex", "[0:v][1:v]overlay=format=auto,format=yuv420p", "-c:v", "libx264",
                        "-crf", "20", "-movflags", "+faststart", bg], check=True)
        os.remove(overlay)
        print(f"DONE\n  preview: {bg}")
        return

    verify_ae_alpha(overlay)
    final = os.path.join(args.out_dir, f"{name}_captioned.mp4")
    composite(args.video, overlay, final)
    srt = os.path.join(args.out_dir, f"{name}.srt")
    write_srt(chunks, srt)
    outs = [final, overlay, srt]
    if args.webm:
        webm = os.path.join(args.out_dir, f"{name}_captions_alpha.webm")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", overlay, "-c:v", "libvpx-vp9", "-pix_fmt",
                        "yuva420p", "-b:v", "0", "-crf", "30", "-row-mt", "1", webm], check=True)
        outs.append(webm)
    if args.zip_to:
        os.makedirs(args.zip_to, exist_ok=True)
        zpath = os.path.join(args.zip_to, f"{name}.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            for o in outs:
                comp = zipfile.ZIP_STORED if o.endswith((".mp4", ".webm")) else zipfile.ZIP_DEFLATED
                z.write(o, f"{name}/{os.path.basename(o)}", compress_type=comp)
        outs.insert(0, zpath)
    print("DONE")
    for o in outs:
        print(f"  {o}  ({os.path.getsize(o) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
