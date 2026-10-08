#!/usr/bin/env python3
"""Apply the user's review changes to transcript.json (keeps word timings consistent).

Segment numbers are the [N] shown in transcript_review.txt (1-based). Operations can be combined
and run in the order given. The review file is regenerated after every run.

  --set N "new text"          replace the text of segment N
  --replace-all OLD NEW       fix a recurring word/name everywhere (case-sensitive)
  --timing N START END        change a segment's start/end (seconds or 00:00:01.250)
  --shift SECONDS             shift ALL timings (e.g. -0.2 makes captions appear earlier)
  --merge N                   merge segment N with N+1
  --split N K                 split segment N after its K-th word
  --delete N                  remove segment N
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fc_utils import format_review, load, normalize, parse_time, retime_segment, save  # noqa: E402


class Ordered(argparse.Action):
    def __call__(self, parser, ns, values, option_string=None):
        ops = getattr(ns, "ops", None) or []
        ops.append((self.dest, values))
        setattr(ns, "ops", ops)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    for name, n in [("set", 2), ("replace_all", 2), ("timing", 3), ("shift", 1),
                    ("merge", 1), ("split", 2), ("delete", 1)]:
        ap.add_argument("--" + name.replace("_", "-"), dest=name, nargs=n, action=Ordered)
    args = ap.parse_args()

    data = normalize(load(args.transcript))
    segs = data["segments"]

    def get(n):
        i = int(n) - 1
        if not 0 <= i < len(segs):
            sys.exit(f"ERROR: segment {n} does not exist (1..{len(segs)})")
        return i

    for op, v in getattr(args, "ops", None) or []:
        if op == "set":
            s = segs[get(v[0])]
            s["text"] = v[1].strip()
            retime_segment(s)
        elif op == "replace_all":
            for s in segs:
                if v[0] in s["text"]:
                    s["text"] = s["text"].replace(v[0], v[1])
                    retime_segment(s)
        elif op == "timing":
            s = segs[get(v[0])]
            a, b = parse_time(v[1]), parse_time(v[2])
            oa, ob = s["start"], s["end"]
            k = (b - a) / max(ob - oa, 1e-6)
            for w in s["words"]:
                w["start"] = round(a + (w["start"] - oa) * k, 3)
                w["end"] = round(a + (w["end"] - oa) * k, 3)
            s["start"], s["end"] = a, b
        elif op == "shift":
            d = float(v[0])
            for s in segs:
                s["start"] = max(0, round(s["start"] + d, 3))
                s["end"] = max(0, round(s["end"] + d, 3))
                for w in s["words"]:
                    w["start"] = max(0, round(w["start"] + d, 3))
                    w["end"] = max(0, round(w["end"] + d, 3))
        elif op == "merge":
            i = get(v[0])
            if i + 1 >= len(segs):
                sys.exit("ERROR: nothing after that segment to merge with")
            a, b = segs[i], segs.pop(i + 1)
            a["text"] = a["text"] + " " + b["text"]
            a["words"] += b["words"]
            a["end"] = b["end"]
        elif op == "split":
            i, k = get(v[0]), int(v[1])
            s = segs[i]
            if not 0 < k < len(s["words"]):
                sys.exit(f"ERROR: K must be between 1 and {len(s['words']) - 1}")
            w1, w2 = s["words"][:k], s["words"][k:]
            segs[i:i + 1] = [
                {"start": w1[0]["start"], "end": w1[-1]["end"], "text": " ".join(w["word"] for w in w1), "words": w1},
                {"start": w2[0]["start"], "end": w2[-1]["end"], "text": " ".join(w["word"] for w in w2), "words": w2},
            ]
        elif op == "delete":  # deferred, so numbers in the same run keep referring to the review shown
            segs[get(v[0])]["_delete"] = True

    data["segments"] = segs = [x for x in segs if not x.get("_delete")]
    save(data, args.transcript)
    review = os.path.join(os.path.dirname(os.path.abspath(args.transcript)), "transcript_review.txt")
    text = format_review(data)
    with open(review, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
