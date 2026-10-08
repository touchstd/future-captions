"""Shared helpers for Future Captions (transcript I/O, time formatting, retiming)."""
import json
import re

LANG_NAMES = {
    "en": "English", "pt": "Portuguese", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "nl": "Dutch", "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
    "ru": "Russian", "ar": "Arabic", "hi": "Hindi", "tr": "Turkish", "pl": "Polish",
    "sv": "Swedish", "uk": "Ukrainian", "he": "Hebrew", "id": "Indonesian", "vi": "Vietnamese",
}


def fmt_time(t, sep="."):
    t = max(0.0, float(t))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def parse_time(v):
    """Accepts seconds ('12.5') or timestamps ('00:00:12.500', '0:12.5', '00:00:12,500')."""
    v = str(v).strip().replace(",", ".")
    if ":" not in v:
        return float(v)
    parts = [float(p) for p in v.split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def split_words(text):
    return [w for w in re.split(r"\s+", text.strip()) if w]


def retime_segment(seg):
    """Make seg['words'] consistent with seg['text'].

    Same word count -> keep each word's timing, only update its text.
    Different count -> spread words across the segment, weighted by character length.
    """
    new_words = split_words(seg["text"])
    old = seg.get("words") or []
    if len(old) == len(new_words) and old:
        for w, txt in zip(old, new_words):
            w["word"] = txt
        seg["words"] = old
        return seg
    start, end = float(seg["start"]), float(seg["end"])
    total = sum(len(w) + 1 for w in new_words) or 1
    t = start
    words = []
    for w in new_words:
        d = (end - start) * (len(w) + 1) / total
        words.append({"word": w, "start": round(t, 3), "end": round(t + d * 0.92, 3)})
        t += d
    seg["words"] = words
    return seg


def normalize(data):
    """Ensure every segment has text and words that agree with each other."""
    for seg in data["segments"]:
        joined = " ".join(w["word"].strip() for w in seg.get("words", []) if w["word"].strip())
        seg["text"] = seg.get("text", joined).strip()
        if split_words(joined) != split_words(seg["text"]):
            retime_segment(seg)
        else:
            for w in seg["words"]:
                w["word"] = w["word"].strip()
            seg["words"] = [w for w in seg["words"] if w["word"]]
    data["segments"] = [s for s in data["segments"] if s["text"]]
    return data


def format_review(data):
    code = data.get("language", "?")
    name = LANG_NAMES.get(code, code)
    prob = data.get("language_probability")
    head = f"Detected language: {name} ({code}"
    head += f", {round(prob * 100)}% confidence)" if prob else ")"
    lines = [head, ""]
    for i, seg in enumerate(data["segments"], 1):
        lines.append(f"[{i}] {fmt_time(seg['start'])} --> {fmt_time(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
