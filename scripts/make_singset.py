"""Build a SINGING-VOICE evaluation set (testdata/eval_singing/).

Unlike testdata/eval_real/ (read-aloud speech), this set targets *sung*
audio with correct lyrics, to check how badly a speech-tuned ASR engine
degrades on singing (melisma, pitch, rubato, vibrato, non-speech vocal
sounds like "la la la").

Sources (both freely downloadable, no manual form / gating required):

  - ko + en: CSD (Children's Song Dataset), Zenodo record 4916302.
    50 Korean + 50 English children's songs sung a cappella by one
    professional singer, each with syllable-level timing (csv), phoneme
    romanization (txt) and grapheme lyrics (lyric) that line up 1:1 with
    the timing rows -- this lets us cut the recording into lyric-aligned
    phrase clips with an exact reference transcript.
    License: CC BY-NC-SA 4.0 (research / internal-eval use; no
    redistribution of the raw corpus).
    https://zenodo.org/records/4916302  (DOI 10.5281/zenodo.4916302)

  - ja: PJS (Phoneme-balanced Japanese Singing-voice corpus) ver1.1.
    100 short original songs by one singer, each with a paired *spoken*
    recording of the same lyric text (pjsNNN_song.wav / pjsNNN_speech.wav),
    which gives a controlled sung-vs-spoken comparison. Lyrics are embedded
    in each song's musicxml as hiragana syllables, so ja is scored in kana
    space (see eval side). License: CC BY-SA 4.0.
    https://sites.google.com/site/shinnosuketakamichi/research-topics/pjs_corpus
    (Google Drive file id 1hPHwOkSe2Vnq6hXrhVtzNskJjVMQmvN_; fetch with
    gdown, or place PJS.zip in testdata/_dl_tmp/ manually)

Both corpora are downloaded once into testdata/_dl_tmp/ (gitignored cache,
not committed) and only the needed members are extracted from the zips.
Clips are cut with ffmpeg to 16kHz mono PCM WAV and written to
testdata/eval_singing/, alongside a manifest.json using the same
{"wav", "lang", "ref"} schema as testdata/eval_real/manifest.json.

Usage:
    python scripts/make_singset.py                 # build everything
    python scripts/make_singset.py --skip-download  # reuse cached zips
    python scripts/make_singset.py --lang ko,en     # only some langs

No ASR engine is imported or run by this script -- it only prepares data.
"""
import argparse
import csv
import json
import os
import random
import shutil
import subprocess
import sys
import urllib.request
import zipfile

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "testdata", "eval_singing")
MANIFEST_PATH = os.path.join(OUT_DIR, "manifest.json")
README_PATH = os.path.join(OUT_DIR, "README.txt")

CACHE_DIR = os.path.join(ROOT, "testdata", "_dl_tmp")
EXTRACT_DIR = os.path.join(CACHE_DIR, "extract")

CSD_ZIP_URL = "https://zenodo.org/api/records/4916302/files/CSD.zip/content"
CSD_ZIP_PATH = os.path.join(CACHE_DIR, "CSD.zip")
CSD_ZIP_SIZE = 1851131390  # bytes, from the Zenodo record's file listing

PJS_GDRIVE_ID = "1hPHwOkSe2Vnq6hXrhVtzNskJjVMQmvN_"
PJS_ZIP_PATH = os.path.join(CACHE_DIR, "PJS.zip")
PJS_ZIP_SIZE = 275179158
PJS_ROOT = "PJS_corpus_ver1.1"

RNG_SEED = 20260825  # fixed for reproducible clip selection
MIN_DUR = 5.0
MAX_DUR = 20.0
N_PER_LANG = 15

# CSD song ids to draw candidate clips from, per language. Only the "a"
# key/pitch variant of each song is used (the "b" variant is the same
# lyrics transposed, so it would just duplicate reference text).
CSD_SONGS = {
    "ko": ["kr001a", "kr002a", "kr003a", "kr004a", "kr005a", "kr006a"],
    "en": ["en001a", "en002a", "en003a", "en004a", "en005a", "en006a"],
}
CSD_LANG_DIR = {"ko": "korean", "en": "english"}


def log(msg):
    print(msg, flush=True)


def download(url, dest_path, expected_size=None):
    if os.path.exists(dest_path):
        size = os.path.getsize(dest_path)
        if expected_size is None or size == expected_size:
            log(f"  cached: {dest_path} ({size} bytes)")
            return
        log(f"  cached file is incomplete ({size} of {expected_size}); resuming")
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "hayamimi-eval-singing/1.0"})
    tmp_path = dest_path + ".part"
    resume_from = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
    if resume_from:
        req.add_header("Range", f"bytes={resume_from}-")
    mode = "ab" if resume_from else "wb"
    log(f"  downloading {url} -> {dest_path}")
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp_path, mode) as out:
        shutil.copyfileobj(resp, out, length=1024 * 1024)
    os.replace(tmp_path, dest_path)
    log(f"  done: {os.path.getsize(dest_path)} bytes")


def extract_members(zip_path, members, dest_dir):
    missing = [m for m in members if not os.path.exists(os.path.join(dest_dir, m))]
    if not missing:
        return
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir, members=missing)


def ffmpeg_cut(src_wav, start, end, dest_wav):
    os.makedirs(os.path.dirname(dest_wav), exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", src_wav,
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-ar", "16000", "-ac", "1",
        dest_wav,
    ]
    subprocess.run(cmd, check=True)


# --- CSD (ko / en) -----------------------------------------------------

def csd_parse_phrases(lang_dir, song_id, extract_root):
    csv_path = os.path.join(extract_root, "CSD", lang_dir, "csv", f"{song_id}.csv")
    txt_path = os.path.join(extract_root, "CSD", lang_dir, "txt", f"{song_id}.txt")
    lyric_path = os.path.join(extract_root, "CSD", lang_dir, "lyric", f"{song_id}.txt")

    rows = []
    with open(csv_path, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)  # header: start,end,pitch,syllable
        for row in r:
            rows.append((float(row[0]), float(row[1])))

    txt_lines = open(txt_path, encoding="utf-8").read().split("\n")
    lyric_lines = open(lyric_path, encoding="utf-8").read().split("\n")

    # txt/lyric share the same blank-line layout; each non-blank txt line's
    # syllable count tells us how many consecutive csv rows (= notes) that
    # lyric line spans, since CSD pairs one syllable with one MIDI note.
    cursor = 0
    phrases = []
    for tl, ll in zip(txt_lines, lyric_lines):
        if not tl.strip():
            continue
        n = len(tl.split())
        seg = rows[cursor:cursor + n]
        cursor += n
        if not seg:
            continue
        start, end = seg[0][0], seg[-1][1]
        text = " ".join(ll.strip().split())  # normalize whitespace
        if text:
            phrases.append((start, end, text))
    return phrases


def merge_phrases(phrases, min_dur=MIN_DUR, max_dur=MAX_DUR):
    """Greedily concatenate consecutive lyric lines into MIN_DUR..MAX_DUR clips."""
    clips = []
    cur = []
    cur_start = None
    for start, end, text in phrases:
        if cur_start is None:
            cur_start = start
        if end - cur_start > max_dur and cur:
            clips.append((cur_start, cur[-1][1], " ".join(t for _, _, t in cur)))
            cur = []
            cur_start = start
        cur.append((start, end, text))
        if cur[-1][1] - cur_start >= min_dur:
            clips.append((cur_start, cur[-1][1], " ".join(t for _, _, t in cur)))
            cur = []
            cur_start = None
    return clips


def build_csd_clips(lang, n_needed):
    lang_dir = CSD_LANG_DIR[lang]
    song_ids = CSD_SONGS[lang]
    members = []
    for sid in song_ids:
        for sub, ext in [("wav", "wav"), ("csv", "csv"), ("txt", "txt"), ("lyric", "txt")]:
            members.append(f"CSD/{lang_dir}/{sub}/{sid}.{ext}")
    extract_members(CSD_ZIP_PATH, members, EXTRACT_DIR)

    candidates = []  # (song_id, start, end, text)
    for sid in song_ids:
        phrases = csd_parse_phrases(lang_dir, sid, EXTRACT_DIR)
        for start, end, text in merge_phrases(phrases):
            candidates.append((sid, start, end, text))

    # Dedupe identical reference text (repeated choruses/verses) and pick a
    # deterministic, song-diverse subset.
    seen_text = set()
    deduped = []
    for c in candidates:
        if c[3] in seen_text:
            continue
        seen_text.add(c[3])
        deduped.append(c)

    rng = random.Random(RNG_SEED)
    by_song = {}
    for c in deduped:
        by_song.setdefault(c[0], []).append(c)
    for lst in by_song.values():
        rng.shuffle(lst)

    selected = []
    song_order = sorted(by_song.keys())
    idx = 0
    while len(selected) < n_needed and any(by_song.values()):
        sid = song_order[idx % len(song_order)]
        if by_song[sid]:
            selected.append(by_song[sid].pop())
        idx += 1
        if idx > 10_000:
            break

    results = []
    for i, (sid, start, end, text) in enumerate(selected, 1):
        src_wav = os.path.join(EXTRACT_DIR, "CSD", lang_dir, "wav", f"{sid}.wav")
        wav_name = f"{lang}_{i:02d}.wav"
        dest_wav = os.path.join(OUT_DIR, wav_name)
        ffmpeg_cut(src_wav, start, end, dest_wav)
        results.append({
            "wav": wav_name,
            "lang": lang,
            "ref": text,
            "source": f"CSD/{sid} [{start:.2f}-{end:.2f}s]",
        })
    return results


# --- PJS (ja) -----------------------------------------------------------

def pjs_lyric(zf, song_id):
    """Hiragana lyric of one PJS song, joined from its musicxml <text> nodes."""
    import re as _re
    xml = zf.read(f"{PJS_ROOT}/{song_id}/{song_id}.musicxml").decode("utf-8")
    syls = _re.findall(r"<text>([^<]+)</text>", xml)
    return "".join(s.strip() for s in syls)


def build_pjs_clips(n_needed):
    """Whole-song ja clips from PJS, plus paired spoken versions.

    Every PJS song ships a sung take (pjsNNN_song.wav) and a spoken take of
    the same lyric (pjsNNN_speech.wav) by the same person, so alongside the
    singing clips we emit the paired speech into OUT_DIR/../eval_singing_speech/
    with the same reference text -- a controlled sung-vs-spoken comparison.
    Lyrics are hiragana; ja must be scored in kana space on the eval side.
    """
    speech_dir = os.path.join(os.path.dirname(OUT_DIR), "eval_singing_speech")
    with zipfile.ZipFile(PJS_ZIP_PATH) as zf:
        names = set(zf.namelist())
        song_ids = sorted({n.split("/")[1] for n in names
                           if n.startswith(f"{PJS_ROOT}/pjs") and n.count("/") >= 2})

        rng = random.Random(RNG_SEED)
        rng.shuffle(song_ids)

        results = []
        speech_records = []
        i = 0
        for sid in song_ids:
            if len(results) >= n_needed:
                break
            song_member = f"{PJS_ROOT}/{sid}/{sid}_song.wav"
            speech_member = f"{PJS_ROOT}/{sid}/{sid}_speech.wav"
            xml_member = f"{PJS_ROOT}/{sid}/{sid}.musicxml"
            if not {song_member, speech_member, xml_member} <= names:
                continue
            text = pjs_lyric(zf, sid)
            if not text:
                continue
            extract_members(PJS_ZIP_PATH, [song_member, speech_member], EXTRACT_DIR)
            i += 1
            wav_name = f"ja_{i:02d}.wav"
            ffmpeg_cut_full(os.path.join(EXTRACT_DIR, song_member),
                            os.path.join(OUT_DIR, wav_name))
            results.append({
                "wav": wav_name, "lang": "ja", "ref": text,
                "source": f"PJS/{sid} (sung, kana lyric from musicxml)",
            })
            ffmpeg_cut_full(os.path.join(EXTRACT_DIR, speech_member),
                            os.path.join(speech_dir, wav_name))
            speech_records.append({
                "wav": wav_name, "lang": "ja", "ref": text,
                "source": f"PJS/{sid} (spoken twin of eval_singing/{wav_name})",
            })

    if speech_records:
        os.makedirs(speech_dir, exist_ok=True)
        with open(os.path.join(speech_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(speech_records, f, ensure_ascii=False, indent=2)
    return results


def ffmpeg_cut_full(src_wav, dest_wav):
    os.makedirs(os.path.dirname(dest_wav), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src_wav,
                    "-ar", "16000", "-ac", "1", dest_wav], check=True)


README_TEMPLATE = """testdata/eval_singing/ -- singing-voice ASR evaluation set
=============================================================

Purpose
-------
Checks how much a speech-tuned ASR engine's accuracy degrades on *sung*
audio (melisma, pitch/vibrato, rubato, sustained vowels, "la la la"-style
non-lexical vocals) compared to read-aloud speech (see testdata/eval_real/).

Generated by scripts/make_singset.py (seed={seed}). Re-running the script
reproduces the same clip selection given the same cached source zips.

Sources
-------
- ko + en: CSD (Children's Song Dataset)
  https://zenodo.org/records/4916302  (DOI 10.5281/zenodo.4916302)
  Choi, S., Kim, W., Park, S., Yong, S., & Nam, J. (2020). Children's Song
  Dataset for Singing Voice Research. ISMIR 2020.
  License: CC BY-NC-SA 4.0 -- research / internal evaluation use only,
  no redistribution of the raw corpus. Reference text = the corpus's own
  grapheme-level lyric annotations, cut to phrase boundaries derived from
  its syllable-level timing (csv) + phoneme (txt) files.

- ja: PJS (Phoneme-balanced Japanese Singing-voice corpus) ver1.1
  https://sites.google.com/site/shinnosuketakamichi/research-topics/pjs_corpus
  Koguchi, J., Takamichi, S., & Morise, M. (2020). PJS: phoneme-balanced
  Japanese singing-voice corpus. APSIPA ASC 2020.
  License: CC BY-SA 4.0. Each ja clip is a whole short song (sung a
  cappella); the paired *spoken* recording of the same lyric by the same
  person goes to ../eval_singing_speech/ for a controlled sung-vs-spoken
  comparison. References are hiragana lyric syllables extracted from the
  corpus musicxml, so ja must be scored in kana space (eval_singing.py
  converts hypotheses to hiragana with pykakasi before CER).

Format
------
- wav/: 16kHz mono PCM WAV, matching testdata/eval_real/'s format.
- manifest.json: list of {{"wav", "lang", "ref", "source"}} records, same
  "wav"/"lang"/"ref" keys as testdata/eval_real/manifest.json ("source" is
  an extra provenance field, safe to ignore for eval harnesses that only
  read wav/lang/ref).

Regenerating
------------
    python scripts/make_singset.py

This only prepares data -- it does not import or run the ASR engine.
"""


def write_readme(counts, total_dur):
    lines = README_TEMPLATE.format(seed=RNG_SEED, min_dur=MIN_DUR, max_dur=MAX_DUR,
                                   )
    lines += "\nCounts (at last generation)\n----------------------------\n"
    for lang, n in counts.items():
        lines += f"  {lang}: {n} clips\n"
    lines += f"  total duration: {total_dur:.1f}s ({total_dur / 60:.1f} min)\n"
    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--lang", default="ko,en,ja", help="comma-separated subset of ko,en,ja")
    args = ap.parse_args()
    langs = [s.strip() for s in args.lang.split(",") if s.strip()]

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    if not args.skip_download:
        if "ko" in langs or "en" in langs:
            log("Fetching CSD.zip (Zenodo, ~1.85GB, cached after first run)...")
            download(CSD_ZIP_URL, CSD_ZIP_PATH, CSD_ZIP_SIZE)
        if "ja" in langs:
            if not (os.path.exists(PJS_ZIP_PATH) and os.path.getsize(PJS_ZIP_PATH) == PJS_ZIP_SIZE):
                log("Fetching PJS.zip (Google Drive, ~0.26GB) via gdown...")
                subprocess.run([sys.executable, "-m", "gdown",
                                f"https://drive.google.com/uc?id={PJS_GDRIVE_ID}",
                                "-O", PJS_ZIP_PATH], check=True)

    all_records = []
    counts = {}
    if "ko" in langs:
        log("Building ko clips from CSD...")
        recs = build_csd_clips("ko", N_PER_LANG)
        counts["ko"] = len(recs)
        all_records += recs
    if "en" in langs:
        log("Building en clips from CSD...")
        recs = build_csd_clips("en", N_PER_LANG)
        counts["en"] = len(recs)
        all_records += recs
    if "ja" in langs:
        log("Building ja clips from PJS (sung + spoken twins)...")
        recs = build_pjs_clips(N_PER_LANG)
        counts["ja"] = len(recs)
        all_records += recs

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)
    log(f"Wrote {MANIFEST_PATH} ({len(all_records)} records)")

    total_dur = 0.0
    for r in all_records:
        p = os.path.join(OUT_DIR, r["wav"])
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", p],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            total_dur += float(out)
        except Exception:
            pass

    write_readme(counts, total_dur)
    log(f"Wrote {README_PATH}")
    log(f"Counts: {counts}, total duration: {total_dur:.1f}s")


if __name__ == "__main__":
    main()
