---
name: "future-captions"
description: Future Captions — a video captioning studio. Transcribes a video's speech (automatic language detection, word-level timestamps), lets the user review and correct the transcript, offers text style options (hide or keep punctuation; as spoken, lowercase or UPPERCASE) and six animated caption presets (Karaoke box, Elegant serif with blur and motion, Cinema hard cut, Word by word, Dynamic karaoke with a stretchy box, Cartoon comic pop) in one or two lines, and exports the video with burned-in captions plus a transparent-background (alpha channel) captions video and an SRT file. Use this skill whenever the user opens or mentions Future Captions, uploads a video and asks for captions or subtitles, wants animated/karaoke/word-by-word/cartoon captions, needs subtitles burned into a video, or needs a captions overlay with transparency for Premiere, DaVinci Resolve, Final Cut, After Effects or CapCut.
---

# Future Captions

A guided, step-by-step caption studio. Claude runs every action; the user only uploads, reviews and chooses.

## Ground rules

- **Always speak English with the user**, whatever language the video or the user's messages are in. Transcripts stay in the spoken language of the video — never translate them unless the user asks.
- Follow the steps in order and **stop at every checkpoint** marked ⏸ until the user answers.
- Keep messages short and friendly. Never show commands, tracebacks or file paths the user doesn't need; summarize problems in one plain sentence.
- Work directory: `/home/claude/fc_work`. Final files go to `/mnt/user-data/outputs/`, named after the source video (e.g. `Interview 01.mp4` → `Interview 01_captioned.mp4`), and are delivered **as separate files** with `present_files` so the user can download only the ones they want.
- Scripts live in this skill's `scripts/` folder (referred to below as `$S`). Resolve the real path first, e.g. `S=$(dirname "$(find /mnt/skills -path '*future-captions/SKILL.md' | head -1)")/scripts`.

## Step 0 — Welcome and upload

If no video is attached yet, reply with exactly this kind of message and stop:

> 🎬 **Welcome to Future Captions.** Upload the video you'd like to caption (MP4, MOV or WebM) and I'll take it from there.

⏸ Wait for the upload. Uploaded files are in `/mnt/user-data/uploads/`. If several videos arrive, ask which one to caption first.

## Step 1 — Transcribe

1. Install the engine once per session (quietly): `pip install -q sherpa-onnx soundfile --break-system-packages`
2. Tell the user in one line that you're transcribing (the first run of a session also downloads the speech models, about 600 MB, from GitHub — that takes a minute), then run:
   `python "$S/transcribe.py" "<video>" --out-dir /home/claude/fc_work`
   - Everything runs offline in the sandbox with sherpa-onnx. The language is auto-detected (Whisper tiny), speech is found with Silero VAD, and the engine is picked automatically:
     - **Parakeet TDT 0.6B v3** for 25 European languages (English, Portuguese, Spanish, French, German, Italian, Dutch, Polish, Russian, Ukrainian and more) — exact word timing.
     - **Whisper small** for any other language (Japanese, Chinese, Korean, Arabic, Hindi…) — word timing is estimated inside each phrase. If `word_timing` in transcript.json is `"estimated"`, tell the user in one line that karaoke/word-by-word sync will be approximate and that Preset 03 (Cinema) works best for this language.
   - If the detection is wrong, rerun with `--language <code>` (e.g. `--language pt`).
   - If a model download fails, tell the user: *"I couldn't download the speech model. Please make sure code execution has network access to github.com, then send me a message to retry."* ⏸
   - If it reports no audio or no speech, say so and stop.
3. Show the transcript for review **with two choice buttons: "Approve transcript" and "Edit transcript"**:
   - **If a tool that renders inline HTML widgets is available** (for example the Visualizer's `show_widget`), build the review panel and render the generated file's contents exactly as-is:
     `python "$S/make_review_widget.py" /home/claude/fc_work/transcript.json /home/claude/fc_work/review.html`
     The panel lists every numbered segment with its timestamps and has the two buttons. **Edit transcript** turns each segment into an editable text box in place (with Save changes / Cancel); clearing a box deletes that segment. Write one short line before it, e.g. *"Here's your transcript. Approve it or edit it right in the panel."* and do not repeat the transcript in text.
   - **Otherwise**, show `/home/claude/fc_work/transcript_review.txt` in full inside a code block, then present the two choices with a tappable-options tool if available (options: `Approve transcript`, `Edit transcript`), or ask in text.

⏸ Wait for the user's choice.

## Step 2 — Apply edits (repeat until approved)

- **"Transcript approved"** (or "Approve transcript") → go to Step 3.
- **"Edit transcript" chosen outside the panel** (fallback path) → reply: *"Sure. Send the lines you want to change as `[N] new text` (one per line), or `[N] (delete)` to remove one. You can also describe timing fixes, like 'captions feel late'."* ⏸
- **"Apply these transcript edits:"** followed by `[N] text` / `[N] (delete)` lines (sent by the panel's Save changes button, or typed by the user) → turn every line into an operation and run them all in one command.

Translate the edits (and any free-form requests) into `edit_transcript.py` operations. Numbers always refer to the review the user saw, even when several deletes run together:

| User says | Operation |
|---|---|
| `[3] new text` / "Segment 3 should say …" | `--set 3 "new text"` |
| `[7] (delete)` / "Remove segment 7" | `--delete 7` |
| "Replace Jon with John everywhere" | `--replace-all "Jon" "John"` |
| "Segment 2 starts at 00:12.4 and ends at 00:15" | `--timing 2 00:00:12.400 00:00:15.000` |
| "Captions appear late / early" | `--shift -0.2` (earlier) / `--shift 0.2` (later) |
| "Merge 4 and 5" (run alone, it renumbers) | `--merge 4` |
| "Split 6 after the word 'today'" (run alone, it renumbers) | `--split 6 K` (K = position of that word in the segment) |

Command: `python "$S/edit_transcript.py" /home/claude/fc_work/transcript.json <operations>`

Then say in one line which segments changed and **show the review again exactly as in Step 1.3** (rebuild the panel with `make_review_widget.py`, or the code block + two choices). ⏸

## Step 3 — Choose text style, then the preset

The user picks, in this order: **text style** (punctuation, case), **layout** (one or two lines), then the **preset**.

1. **If a tool that renders inline HTML widgets is available**, render `assets/preset_preview.html` exactly as-is (read the file and pass its contents), with one short line before it such as *"Pick your text style first, then choose a preset."* The panel has:
   - **1 · Text style**: Punctuation (Keep / Hide) and Case (As spoken / lowercase / UPPERCASE); Layout (One line / Two lines). Every preview updates live.
   - **2 · Choose a preset**: six animated previews of "The Future is Now!". Each **Choose** button sends all choices at once, e.g. `Use Preset 05 · two lines · punctuation: hide · case: UPPERCASE`.
2. **Otherwise** (no widget tool):
   a. First ask the text style with a tappable-options tool if available (otherwise in text): *Punctuation* → `Keep punctuation` / `Hide punctuation`; *Case* → `As spoken` / `lowercase` / `UPPERCASE`. ⏸
   b. Then render the preview clips with those choices and present them:
      `for p in 01 02 03 04 05 06; do python "$S/render.py" --demo --preset $p --lines 1 --punct <keep|hide> --case <original|lower|upper> --out-dir /mnt/user-data/outputs/previews; done`
   c. Ask *Preset* (01–06) and *Layout* (`One line` / `Two lines`). ⏸
3. The presets, for describing them when needed:
   - **01 · Karaoke** — bold modern sans; a rounded color box glides across the words as they're spoken.
   - **02 · Elegant** — modern serif; each word rises out of a soft blur, then the line dissolves upward.
   - **03 · Cinema** — classic film subtitles: clean, legible text; each subtitle replaces the previous one with a hard cut, no animation.
   - **04 · Word by word** — one bold word at a time, no animation (layout doesn't apply).
   - **05 · Dynamic karaoke** — Bricolage Grotesque; words hop in with a twist, a yellow box stretches and leans as it slides to the spoken word, which jumps and turns dark; highlighted words turn the box pink and grow; each caption exits rising and shrinking.
   - **06 · Cartoon** — comic lettering (Bangers) with a thick outline and hard shadow; words spring in with elastic squash-and-stretch and a playful tilt, the spoken word turns yellow and wobbles, then words pop out one by one. Its letterforms always look capitalized.

Map the answers to flags: punctuation keep/hide → `--punct keep|hide`; case as spoken/lowercase/UPPERCASE → `--case original|lower|upper`; lines → `--lines 1|2`. Hiding punctuation keeps what belongs to a word or number (I'm, bye-bye, $120,877.50, 50%).

## Step 4 — Render and deliver

1. **Presets 05 and 06 only — pick highlight words.** Read the approved transcript and choose the most impactful words or short phrases (numbers and money, names, strong emotions, punchlines, surprising words) — roughly one per two or three captions, never more than one per caption. Pass them as `--emphasis "grand prize,congratulations,really"`. After delivery, tell the user in one line which words you highlighted and that they can change them.
2. Tell the user rendering has started (it takes roughly 1–3× the video length).
3. Run (every MOV the skill produces must stay QuickTime Animation with alpha so After Effects reads the transparency — never change the encoder settings in `render.py`):
   `python "$S/render.py" --video "<video>" --transcript /home/claude/fc_work/transcript.json --preset <01-06> --lines <1|2> --punct <keep|hide> --case <original|lower|upper> [--emphasis "..."] --out-dir /mnt/user-data/outputs`
   Output names come from the uploaded video's file name. Never rename them. Don't pack them into a ZIP unless the user asks for one (then add `--zip-to /mnt/user-data/outputs`, which creates `<video name>.zip`, and present it alongside the files).
   Optional flags when the user asks: `--scale 1.2` (bigger text), `--accent "#FF4D6D"` (box / active-word color), `--emph-color "#34D1BF"` (highlight color for 05/06), `--text-color "#FFE14D"`, `--position middle|top`, `--webm` (extra web-friendly alpha file).
   The script ends with `After Effects alpha check: OK`. If it prints a WARNING instead, do not deliver the MOV: rerun once; if it fails again, tell the user in one line that the transparent file failed its check and deliver the MP4 and SRT only.
4. Deliver with **one** `present_files` call listing each file separately, in this order, so each gets its own download button:
   - `<name>_captioned.mp4` — the video with captions burned in.
   - `<name>_captions_alpha.mov` — captions only on a transparent background, in **QuickTime Animation** (32-bit ARGB, "Millions of Colors+"), which After Effects imports with its alpha channel natively. Same size, frame rate and duration as the original: place it on the layer above the video at 0:00 and it lines up automatically. Works in Premiere Pro, DaVinci Resolve and Final Cut Pro too. If edges ever look haloed, right-click the footage → Interpret Footage → Main → Alpha: **Straight – Unmatted**.
   - `<name>.srt` — a standard subtitle file (bonus, for YouTube or players), with the same text style.
   - `<name>_captions_alpha.webm` — only if `--webm` was used.
   After the files, add one short line per file saying what it's for (as above, condensed), so the user can pick what to download.
5. Close with one line offering tweaks: size, color, position, highlight words, text style, a different preset, or one/two lines. Tweaks only need Step 4 again — never re-transcribe unless the user uploads a new video.

## Troubleshooting

- Upload too large: suggest exporting a shorter or lower-resolution copy (1080p is plenty), or running this skill in Claude Code / Claude desktop where files are read from disk.
- Caption timing drifts in one place: fix that segment with `--timing`; for a global offset use `--shift`.
- Too many words on screen: re-render with `--scale 1.15` (fewer words fit per line) or choose one line.
- Vertical videos (9:16) are detected automatically and captions sit higher, clear of social-app buttons.
