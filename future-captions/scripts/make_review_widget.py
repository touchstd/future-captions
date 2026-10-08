#!/usr/bin/env python3
"""Fill assets/transcript_review.html with the current transcript.

Usage: python make_review_widget.py transcript.json OUT.html
Pass the contents of OUT.html to the inline widget tool (e.g. Visualizer show_widget).
The widget sends back either "Transcript approved" or
"Apply these transcript edits:\n[N] new text\n[M] (delete)".
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fc_utils import LANG_NAMES, load, normalize  # noqa: E402

src, out = sys.argv[1], sys.argv[2]
data = normalize(load(src))
payload = {
    "language": LANG_NAMES.get(data.get("language", ""), data.get("language", "unknown")),
    "estimated": data.get("word_timing") == "estimated",
    "segments": [{"n": i, "start": s["start"], "end": s["end"], "text": s["text"]}
                 for i, s in enumerate(data["segments"], 1)],
}
tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "transcript_review.html"),
           encoding="utf-8").read()
js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
with open(out, "w", encoding="utf-8") as f:
    f.write(tpl.replace("__FC_DATA__", js))
print(f"OK {len(payload['segments'])} segments -> {out}")
