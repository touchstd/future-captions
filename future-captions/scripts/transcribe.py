#!/usr/bin/env python3
"""Transcribe a video's speech with word-level timestamps, fully offline via sherpa-onnx.

Engines (chosen automatically from the detected language):
  - NVIDIA Parakeet TDT 0.6B v3: exact word timings, 25 European languages (incl. EN, PT, ES, FR, DE, IT, RU, UK, PL)
  - Whisper small (fallback): any other language (JA, ZH, KO, AR, HI...); word timings estimated within each phrase
Language ID: Whisper tiny.  Speech detection: Silero VAD.
All models download from GitHub releases (github.com is reachable by default in Claude's sandbox).

Usage:
  python transcribe.py VIDEO --out-dir OUT [--language pt]
Writes OUT/transcript.json (source of truth) and OUT/transcript_review.txt (for the user).
Requires: pip install sherpa-onnx soundfile
"""
import argparse
import os
import subprocess
import sys
import tarfile
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fc_utils import LANG_NAMES, format_review, retime_segment, save  # noqa: E402

REL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
CACHE = os.path.expanduser("~/.cache/future-captions")
PARAKEET = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
WHISPER_LID = "sherpa-onnx-whisper-tiny"
WHISPER_ASR = "sherpa-onnx-whisper-small"
PARAKEET_LANGS = {"bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it", "lv", "lt",
                  "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk"}
SR = 16000


def fetch(name, is_tar=True):
    """Download a model once per session into the cache (from GitHub releases)."""
    os.makedirs(CACHE, exist_ok=True)
    target = os.path.join(CACHE, name)
    if os.path.exists(target):
        return target
    url = REL + name + (".tar.bz2" if is_tar else "")
    print(f"  downloading model {name} ...", flush=True)
    tmp = target + (".tar.bz2" if is_tar else ".part")
    try:
        urllib.request.urlretrieve(url, tmp)
    except Exception as e:
        sys.exit(f"ERROR: could not download {url} (network?). github.com must be reachable.\n{e}")
    if is_tar:
        with tarfile.open(tmp, "r:bz2") as t:
            t.extractall(CACHE)
        os.remove(tmp)
    else:
        os.rename(tmp, target)
    return target


def load_audio(video, wav):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", str(SR), wav],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(wav):
        sys.exit("ERROR: could not extract audio. Does the video have an audio track?\n" + r.stderr)
    import soundfile as sf
    a, _ = sf.read(wav, dtype="float32")
    return a if a.ndim == 1 else a[:, 0]


def speech_regions(sherpa_onnx, audio):
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = fetch("silero_vad.onnx", is_tar=False)
    cfg.silero_vad.min_silence_duration = 0.35
    cfg.silero_vad.min_speech_duration = 0.2
    cfg.silero_vad.max_speech_duration = 20
    cfg.sample_rate = SR
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=len(audio) / SR + 30)
    win = cfg.silero_vad.window_size
    for i in range(0, len(audio), win):
        vad.accept_waveform(audio[i:i + win])
    vad.flush()
    regions = []
    while not vad.empty():
        s = vad.front.start
        regions.append((s, s + len(vad.front.samples)))
        vad.pop()
    return regions


def detect_language(sherpa_onnx, audio, regions):
    d = fetch(WHISPER_LID)
    lid = sherpa_onnx.SpokenLanguageIdentification(sherpa_onnx.SpokenLanguageIdentificationConfig(
        whisper=sherpa_onnx.SpokenLanguageIdentificationWhisperConfig(
            encoder=f"{d}/tiny-encoder.int8.onnx", decoder=f"{d}/tiny-decoder.int8.onnx"), num_threads=2))
    speech = np.concatenate([audio[a:b] for a, b in regions])[: 30 * SR]  # first ~30 s of speech
    st = lid.create_stream()
    st.accept_waveform(SR, speech)
    return lid.compute(st)


def words_from_tokens(r, offset):
    """Parakeet tokens -> words. A token starting with a space opens a new word; punctuation sticks."""
    words = []
    durs = list(r.durations) if len(r.durations) == len(r.tokens) else [0.08] * len(r.tokens)
    for tok, ts, du in zip(r.tokens, r.timestamps, durs):
        start, end = offset + ts, offset + ts + max(du, 0.04)
        if tok.startswith(" ") or not words:
            if tok.strip():
                words.append({"word": tok.strip(), "start": round(start, 3), "end": round(end, 3)})
        else:
            words[-1]["word"] += tok
            words[-1]["end"] = round(end, 3)
    return words


def split_sentences(words, text_fallback=None):
    """Group words into readable review segments: sentence ends or pauses over 0.6 s."""
    segs, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > 0.6 or cur[-1]["word"][-1:] in ".?!。？！"):
            segs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        segs.append(cur)
    return [{"start": s[0]["start"], "end": s[-1]["end"], "text": " ".join(w["word"] for w in s), "words": s}
            for s in segs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out-dir", default="fc_work")
    ap.add_argument("--language", default=None, help="Force a language code; default is auto-detect")
    args = ap.parse_args()
    try:
        import sherpa_onnx
    except ImportError:
        sys.exit("ERROR: sherpa-onnx missing. Run: pip install sherpa-onnx soundfile --break-system-packages")

    os.makedirs(args.out_dir, exist_ok=True)
    audio = load_audio(args.video, os.path.join(args.out_dir, "audio_16k.wav"))
    regions = speech_regions(sherpa_onnx, audio)
    if not regions:
        sys.exit("ERROR: no speech found in this video.")
    lang = args.language or detect_language(sherpa_onnx, audio, regions)
    exact = lang in PARAKEET_LANGS
    print(f"  language: {LANG_NAMES.get(lang, lang)} ({lang}) -> engine: "
          f"{'Parakeet v3 (exact word timing)' if exact else 'Whisper small (estimated word timing)'}", flush=True)

    if exact:
        d = fetch(PARAKEET)
        rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=f"{d}/encoder.int8.onnx", decoder=f"{d}/decoder.int8.onnx", joiner=f"{d}/joiner.int8.onnx",
            tokens=f"{d}/tokens.txt", model_type="nemo_transducer", num_threads=os.cpu_count() or 2)
    else:
        d = fetch(WHISPER_ASR)
        rec = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=f"{d}/small-encoder.int8.onnx", decoder=f"{d}/small-decoder.int8.onnx",
            tokens=f"{d}/small-tokens.txt", language=lang, task="transcribe", num_threads=os.cpu_count() or 2)

    pad, tail = int(0.25 * SR), np.zeros(int(0.5 * SR), dtype="float32")
    segments = []
    for n, (a, b) in enumerate(regions, 1):
        s0 = max(0, a - pad)
        clip = np.concatenate([audio[s0:min(len(audio), b + pad)], tail])
        st = rec.create_stream()
        st.accept_waveform(SR, clip)
        rec.decode_stream(st)
        r = st.result
        if not r.text.strip():
            continue
        if exact:
            ws = words_from_tokens(r, s0 / SR)
            if ws:  # Parakeet stamps the first token at t=0 of the clip; snap edges to real speech bounds
                ws[0]["start"] = round(max(ws[0]["start"], a / SR - 0.05), 3)
                ws[-1]["end"] = round(min(ws[-1]["end"], b / SR + 0.1), 3)
                for w in ws:
                    w["end"] = max(w["end"], w["start"] + 0.05)
            segments += split_sentences(ws)
        else:  # no word timestamps from Whisper: spread words across this speech region
            seg = {"start": round(a / SR, 3), "end": round(b / SR, 3), "text": r.text.strip(), "words": []}
            segments.append(retime_segment(seg))
        print(f"  [{n}/{len(regions)}] {a / SR:7.2f}s  {r.text.strip()[:70]}", flush=True)

    data = {"language": lang, "engine": "parakeet-tdt-0.6b-v3" if exact else "whisper-small",
            "word_timing": "exact" if exact else "estimated",
            "duration": round(len(audio) / SR, 3), "segments": segments}
    json_path = os.path.join(args.out_dir, "transcript.json")
    review_path = os.path.join(args.out_dir, "transcript_review.txt")
    save(data, json_path)
    with open(review_path, "w", encoding="utf-8") as f:
        f.write(format_review(data))
    print(f"\nOK  {len(segments)} segments -> {json_path}\nREVIEW -> {review_path}")


if __name__ == "__main__":
    main()
