#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_video.py - Claude CLI video analyzer -> EDL for DaVinci Resolve

Usage:
    python analyze_video.py myvideo.mp4
    python analyze_video.py myvideo.mp4 --interval 20
    python analyze_video.py myvideo.mp4 --min-score 5
"""

import argparse
import array
import base64
import json
import math
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path


# ── STEP 1: Video metadata ───────────────────────────────────────────────────

def get_video_duration(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ffprobe error: {result.stderr}")
        sys.exit(1)
    return float(json.loads(result.stdout)["format"]["duration"])


def get_video_fps(video_path: str) -> int:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-print_format", "json", video_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return 30
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        return 30
    frac = streams[0].get("r_frame_rate", "30/1")
    try:
        num, den = frac.split("/")
        fps_float = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        return 30
    for common in [24, 25, 30, 48, 50, 60, 120]:
        if abs(fps_float - common) < 0.5:
            return common
    return round(fps_float)


# ── STEP 2: Extract video frames ─────────────────────────────────────────────

def extract_keyframes(video_path: str, interval_sec: int = 30) -> list:
    frames_dir = Path("temp_frames")
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir()

    print(f"Extracting frames every {interval_sec}s...")
    result = subprocess.run([
        "ffmpeg", "-i", video_path,
        "-vf", f"fps=1/{interval_sec}",
        "-q:v", "3",
        str(frames_dir / "frame_%06d.jpg"),
        "-y", "-loglevel", "error"
    ], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr}")
        sys.exit(1)

    frames = []
    for i, frame_file in enumerate(sorted(frames_dir.glob("*.jpg"))):
        with open(frame_file, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        frames.append({
            "timestamp_sec": i * interval_sec,
            "b64": b64,
        })

    print(f"Got {len(frames)} frames")
    return frames


# ── STEP 3: Audio analysis ───────────────────────────────────────────────────

def analyze_audio_loudness(video_path: str, interval_sec: int = 30) -> list:
    """
    Extract per-segment RMS loudness from the audio track.
    Returns list of {timestamp_sec, rms_db, excitement} dicts.
    excitement is 0-10 relative to the average loudness of the whole video.
    """
    audio_path = Path("temp_audio.wav")
    print("Analyzing audio loudness...")

    subprocess.run([
        "ffmpeg", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        str(audio_path), "-y", "-loglevel", "error"
    ], capture_output=True, check=True)

    with wave.open(str(audio_path), "rb") as wf:
        sample_rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    audio_path.unlink(missing_ok=True)

    samples = array.array("h", raw)
    window = sample_rate * interval_sec

    results = []
    for start in range(0, len(samples), window):
        seg = samples[start: start + window]
        if len(seg) < window // 2:
            break
        rms = math.sqrt(sum(x * x for x in seg) / len(seg))
        db = 20 * math.log10(max(rms, 1) / 32768.0)
        results.append({
            "timestamp_sec": start // sample_rate,
            "rms_db": round(db, 1),
        })

    # Normalize to 0-10 excitement scale relative to this video's average
    if results:
        dbs = [r["rms_db"] for r in results]
        mean_db = sum(dbs) / len(dbs)
        variance = sum((d - mean_db) ** 2 for d in dbs) / len(dbs)
        std_db = math.sqrt(variance) or 1.0
        for r in results:
            z = (r["rms_db"] - mean_db) / std_db
            r["excitement"] = min(10, max(0, round(5 + z * 2)))

    print(f"Audio analysis done ({len(results)} windows)")
    return results


# ── STEP 3b: Librosa deep audio scan (Task 9) ────────────────────────────────

def analyze_audio_librosa(video_path: str, interval_sec: int = 20) -> list:
    """
    Detect voice shout peaks and onset bursts using librosa.
    Returns list of {timestamp_sec, librosa_score} or [] if librosa not installed.
    """
    try:
        import librosa
        import numpy as np
    except ImportError:
        print("  (librosa not installed — skipping deep audio scan)")
        return []

    audio_path = Path("temp_audio_librosa.wav")
    subprocess.run([
        "ffmpeg", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
        str(audio_path), "-y", "-loglevel", "error"
    ], capture_output=True, check=True)

    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    audio_path.unlink(missing_ok=True)

    window_samples = sr * interval_sec
    raw = []
    for i, start in enumerate(range(0, len(y), window_samples)):
        seg = y[start:start + window_samples]
        if len(seg) < window_samples // 2:
            break
        rms = float(np.sqrt(np.mean(seg ** 2)))
        onset_env = librosa.onset.onset_strength(y=seg, sr=sr)
        onset_peak = float(np.percentile(onset_env, 95))
        centroid = float(np.mean(librosa.feature.spectral_centroid(y=seg, sr=sr)))
        raw.append({"timestamp_sec": i * interval_sec, "rms": rms,
                    "onset_peak": onset_peak, "centroid": centroid})

    if not raw:
        return []

    def _norm(vals):
        mn, mx = min(vals), max(vals)
        r = mx - mn or 1.0
        return [(v - mn) / r * 10 for v in vals]

    rms_n    = _norm([r["rms"] for r in raw])
    onset_n  = _norm([r["onset_peak"] for r in raw])
    cent_n   = _norm([r["centroid"] for r in raw])

    results = []
    for i, r in enumerate(raw):
        score = onset_n[i] * 0.50 + rms_n[i] * 0.35 + cent_n[i] * 0.15
        results.append({"timestamp_sec": r["timestamp_sec"],
                        "librosa_score": min(10, max(0, round(score)))})

    print(f"Librosa audio scan done ({len(results)} windows)")
    return results


# ── STEP 3c: OpenCV vision scan for LoL UI events (Task 10) ──────────────────

def scan_frames_opencv(frames: list) -> list:
    """
    Detect LoL kill feed entries and multi-kill banners per frame using OpenCV.
    Returns list of {timestamp_sec, vision_score, detections} or [] if cv2 not installed.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("  (opencv-python not installed — skipping vision scan)")
        return []

    results = []
    for frame in frames:
        ts = frame["timestamp_sec"]
        img_bytes = base64.b64decode(frame["b64"])
        img_arr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
        if img is None:
            results.append({"timestamp_sec": ts, "vision_score": 0, "detections": []})
            continue

        h, w = img.shape[:2]
        detections = []
        score = 0

        # Kill feed — top-right (65-100% width, 0-30% height)
        kf = img[0:int(h * 0.30), int(w * 0.65):]
        kf_hsv = cv2.cvtColor(kf, cv2.COLOR_BGR2HSV)
        area = kf.shape[0] * kf.shape[1] + 1

        red_mask = cv2.bitwise_or(
            cv2.inRange(kf_hsv, np.array([0, 100, 100]),   np.array([10, 255, 255])),
            cv2.inRange(kf_hsv, np.array([160, 100, 100]), np.array([180, 255, 255])),
        )
        if np.sum(red_mask > 0) / area > 0.005:
            detections.append("kill_feed_red")
            score += 3

        blue_mask = cv2.inRange(kf_hsv, np.array([100, 100, 100]), np.array([130, 255, 255]))
        if np.sum(blue_mask > 0) / area > 0.005:
            detections.append("kill_feed_blue")
            score += 2

        # Multi-kill banner — center-bottom (25-75% width, 60-85% height)
        banner = img[int(h * 0.60):int(h * 0.85), int(w * 0.25):int(w * 0.75)]
        banner_hsv = cv2.cvtColor(banner, cv2.COLOR_BGR2HSV)
        banner_area = banner.shape[0] * banner.shape[1] + 1

        gold_mask = cv2.inRange(banner_hsv, np.array([15, 150, 150]), np.array([35, 255, 255]))
        if np.sum(gold_mask > 0) / banner_area > 0.01:
            detections.append("multikill_banner")
            score += 5

        # Bright flash — center (30-70% width, 35-65% height)
        center = img[int(h * 0.35):int(h * 0.65), int(w * 0.30):int(w * 0.70)]
        gray = cv2.cvtColor(center, cv2.COLOR_BGR2GRAY)
        if np.sum(gray > 220) / (center.shape[0] * center.shape[1] + 1) > 0.15:
            detections.append("bright_flash")
            score += 2

        results.append({"timestamp_sec": ts, "vision_score": min(10, score),
                        "detections": detections})

    detected = sum(1 for r in results if r["detections"])
    print(f"OpenCV vision scan done ({len(results)} frames, {detected} with detections)")
    return results


# ── STEP 3d: Combine all signals (Task 11) ────────────────────────────────────

def normalize_and_combine_scores(
    audio_data: list,
    librosa_data: list,
    vision_data: list,
) -> list:
    """
    Merge RMS excitement (25%), librosa shout score (35%), and OpenCV vision score (40%)
    into combined_score per segment. Weights shift gracefully when modules are missing.
    """
    lib_by_ts = {r["timestamp_sec"]: r for r in librosa_data}
    vis_by_ts = {r["timestamp_sec"]: r for r in vision_data}

    has_lib = bool(librosa_data)
    has_vis = bool(vision_data)

    combined = []
    for r in audio_data:
        ts = r["timestamp_sec"]
        rms_score = r.get("excitement", 5)
        lib_score = lib_by_ts.get(ts, {}).get("librosa_score", rms_score)
        vis_score = vis_by_ts.get(ts, {}).get("vision_score", 0)
        detections = vis_by_ts.get(ts, {}).get("detections", [])

        if has_lib and has_vis:
            combined_score = vis_score * 0.40 + lib_score * 0.35 + rms_score * 0.25
        elif has_lib:
            combined_score = lib_score * 0.60 + rms_score * 0.40
        elif has_vis:
            combined_score = vis_score * 0.60 + rms_score * 0.40
        else:
            combined_score = rms_score

        # Pure voice peak: loud audio but zero visual confirmation and no librosa onset
        # These are almost always "loud talking" not fight reactions — cap score at 5
        is_pure_voice = (rms_score >= 7 and vis_score == 0 and lib_score < 7)
        if is_pure_voice:
            combined_score = min(combined_score, 5)

        entry = dict(r)
        entry["librosa_score"] = lib_score
        entry["vision_score"] = vis_score
        entry["vision_detections"] = detections
        entry["combined_score"] = min(10, max(0, round(combined_score)))
        entry["pure_voice_peak"] = is_pure_voice
        combined.append(entry)

    return combined


def transcribe_audio_whisper(video_path: str, interval_sec: int = 30):
    """
    Optional: transcribe voice with Whisper (supports Thai).
    Returns dict {window_index: transcribed_text} or None if Whisper not installed.
    Install with: pip install openai-whisper
    """
    try:
        import whisper  # type: ignore
    except ImportError:
        print("  (Whisper not installed -- skipping transcription)")
        print("  To enable voice transcription: pip install openai-whisper")
        return None

    audio_path = Path("temp_audio_whisper.wav")
    print("Transcribing voice with Whisper (this may take a few minutes)...")

    subprocess.run([
        "ffmpeg", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        str(audio_path), "-y", "-loglevel", "error"
    ], capture_output=True, check=True)

    model = whisper.load_model("small")
    result = model.transcribe(
        str(audio_path),
        language=None,     # auto-detect Thai / English mix
        verbose=False,
        word_timestamps=False,
    )
    audio_path.unlink(missing_ok=True)

    # Group transcription segments into our 30-second windows
    windows: dict = {}
    for seg in result.get("segments", []):
        window_idx = int(seg["start"]) // interval_sec
        windows.setdefault(window_idx, []).append(seg["text"].strip())

    condensed = {k: " ".join(v) for k, v in windows.items()}
    total_words = sum(len(v.split()) for v in condensed.values())
    print(f"Transcription done ({total_words} words across {len(condensed)} windows)")
    return condensed


# ── STEP 4: Build prompt ─────────────────────────────────────────────────────

def build_prompt(
    frames: list,
    sample_indices: set,
    duration: float,
    combined_data: list,
    transcription: dict,
) -> str:

    data_by_ts = {r["timestamp_sec"]: r for r in combined_data}

    lines = []
    for i, f in enumerate(frames):
        ts = int(f["timestamp_sec"])
        d = data_by_ts.get(ts, {})
        exc       = d.get("excitement", "?")
        db        = d.get("rms_db", "?")
        lib       = d.get("librosa_score", "?")
        vis       = d.get("vision_score", "?")
        combined  = d.get("combined_score", exc)
        detects   = d.get("vision_detections", [])

        pure_voice = d.get("pure_voice_peak", False)

        tags = []
        if pure_voice:
            tags.append("PURE_VOICE_PEAK(max5)")
        elif exc != "?" and int(exc) >= 7:
            tags.append("LOUD VOICE")
        if lib != "?" and int(lib) >= 7:
            tags.append("SHOUT DETECTED")
        if detects:
            tags.append(f"VISION:[{','.join(detects)}]")
        tag_str = "  " + "  ".join(tags) if tags else ""

        img_tag = "  <-- IMAGE ATTACHED" if i in sample_indices else ""
        trans = transcription.get(i, "") if transcription else ""
        trans_part = f'  voice: "{trans}"' if trans else ""

        line = (
            f"  Frame {i+1}: video_time={ts}s"
            f"  rms={exc}/10({db}dBFS)"
            f"  librosa={lib}/10"
            f"  vision={vis}/10"
            f"  COMBINED={combined}/10"
            f"{tag_str}"
            f"{img_tag}"
            f"{trans_part}"
        )
        lines.append(line)

    frame_desc = "\n".join(lines)

    has_voice = bool(transcription)
    voice_note = (
        "  - Voice transcription is provided in the 'voice:' field per frame.\n"
        "    Prioritize moments where players shout, laugh, react strongly, or say\n"
        "    exciting things (kills, near-death, clutch plays).\n"
    ) if has_voice else (
        "  - audio_excitement shows how loud the Discord voice was (0=silent, 10=peak reaction).\n"
        "    High excitement voice + exciting gameplay = strongest highlight candidate.\n"
        "    High excitement voice alone = good Short even if visuals look calm.\n"
    )

    prompt = (
        "Analyze these video frames and return ONLY a JSON object"
        " -- no markdown, no explanation.\n"
        "\n"
        "=== CRITICAL RULES ===\n"
        "1. 'video_time=Xs' = seconds from start of the RECORDING FILE (includes lobby/loading).\n"
        "   This is NOT the in-game timer. Never use in-game clocks for your timestamps.\n"
        "2. start_sec / end_sec in your JSON must EXACTLY match one of the video_time values above.\n"
        "3. Only describe specific content for IMAGE ATTACHED frames.\n"
        "   For other frames, rely on audio_excitement and voice transcription.\n"
        "4. Audio signals for highlight detection:\n"
        + voice_note +
        "\n"
        f"Video: {duration:.0f}s total ({duration/60:.1f} min), one frame every 20s.\n"
        "\n"
        f"All {len(frames)} frames:\n"
        f"{frame_desc}\n"
        "\n"
        "=== YOUTUBE LONG VIDEO SCORING ===\n"
        "Score each segment 0-10. Be AGGRESSIVE — default to cutting, not keeping.\n"
        "  0-2 = MUST CUT: dead silence, loading screen, idle/afk, voice excitement < 3\n"
        "  3-4 = CUT: routine laning with no reactions, filler talking, excitement 3-4\n"
        "  5-6 = STORY BRIDGE ONLY: keep ONLY if directly connecting two high-action moments\n"
        "        and removing it would make the timeline feel incoherent. Otherwise cut.\n"
        "  7-8 = KEEP: clear action or loud voice reaction (excitement >= 7), kill, fight\n"
        "  9-10 = ALWAYS KEEP: multi-kill, clutch, loudest peak, game-defining moment\n"
        "Goal: the final YouTube cut should feel like 100% content — no dead air at all.\n"
        "Every kept segment must earn its place with either action OR strong voice energy.\n"
        "\n"
        "=== YOUTUBE SHORTS RULES (retention-optimized) ===\n"
        "A Short FAILS if the viewer has any reason to swipe away at any second.\n"
        "Rules for each shorts_highlight:\n"
        "  - Duration: 45-60 seconds MAXIMUM. Never pick a window longer than 60s.\n"
        "  - Hook: The FIRST 3 seconds must show the peak action or loudest voice reaction.\n"
        "    Do NOT start before the exciting thing happens — cut the build-up.\n"
        "  - Every 3 seconds: something must happen (kill, reaction, ability, voice peak).\n"
        "  - NO cool-down periods, post-fight lulls, or walking to base — end before that.\n"
        "  - Pick start_sec at the moment of impact, not the lead-up.\n"
        "  - Each Short must be self-contained and understandable without context.\n"
        "  - Prefer shorter and punchier over longer and padded.\n"
        "  - Pick max 5 Shorts, only from segments scoring 8+.\n"
        "\n"
        "=== CUT QUALITY RULES (anti-patterns to avoid) ===\n"
        "1. NEVER end a segment mid-fight. If the immediately following window scores >= 6\n"
        "   and the fight reaction is still ongoing, extend end_sec to include it.\n"
        "   Fights end when excitement drops to <= 4 AND voice has settled.\n"
        "2. NEVER start a Short before the peak event. start_sec must be at the moment\n"
        "   of impact — not the calm or build-up before it.\n"
        "3. PURE_VOICE_PEAK(max5) frames are loud talking with zero visual event confirmed.\n"
        "   Score them max 5/10. They CANNOT be Short candidates.\n"
        "4. A valid Short requires at least one VISION:[...] signal within 60s of start_sec.\n"
        "   Voice-only peaks (no kill feed, no multikill banner) do not qualify for Shorts.\n"
        "5. Prefer to end every cut at a moment where excitement <= 3 (natural silence).\n"
        "   Never cut mid-sentence or while someone is still reacting.\n"
        "\n"
        "Return ONLY this JSON:\n"
        "{{\n"
        '  "segments": [\n'
        '    {{"start_sec": 0, "end_sec": 20, "score": 7,\n'
        '      "reason": "what you see/hear -- cite image or voice evidence"}}\n'
        "  ],\n"
        '  "cuts_to_remove": [\n'
        '    {{"start_sec": 60, "end_sec": 80, "reason": "reason"}}\n'
        "  ],\n"
        '  "shorts_highlights": [\n'
        "    {{\n"
        '      "start_sec": 120,\n'
        '      "end_sec": 175,\n'
        '      "title": "3_to_5_word_filename_no_spaces",\n'
        '      "reason": "retention hook + what makes every second engaging"\n'
        "    }}\n"
        "  ],\n"
        '  "summary": "One sentence describing the video"\n'
        "}}"
    )
    return prompt


# ── STEP 5: Claude CLI call ──────────────────────────────────────────────────

def analyze_with_claude_cli(
    frames: list,
    duration: float,
    combined_data: list,
    transcription: dict,
) -> dict:

    max_images = 10
    if len(frames) <= max_images:
        sample_indices = set(range(len(frames)))
    else:
        step = max(1, len(frames) // max_images)
        sample_indices = set(range(0, len(frames), step))
        sample_indices.add(0)
        sample_indices.add(len(frames) - 1)
        sample_indices = set(sorted(sample_indices)[:max_images])

    sample = [frames[i] for i in sorted(sample_indices)]
    prompt = build_prompt(frames, sample_indices, duration, combined_data, transcription)

    content = [{"type": "text", "text": prompt}]
    for f in sample:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": f["b64"],
            },
        })

    message_line = json.dumps(
        {"type": "user", "message": {"role": "user", "content": content}},
        ensure_ascii=False,
    ) + "\n"

    claude_cmd = shutil.which("claude") or shutil.which("claude.cmd") or "claude"
    print(f"Sending to Claude ({len(sample)} images + audio data)...")

    result = subprocess.run(
        [claude_cmd, "--print", "--verbose",
         "--input-format", "stream-json",
         "--output-format", "stream-json"],
        input=message_line,
        capture_output=True, encoding="utf-8", errors="replace",
        timeout=600, cwd=Path.home(),
    )

    if result.returncode != 0:
        print(f"Claude CLI error:\n{result.stderr}")
        sys.exit(1)

    raw_parts = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                raw_parts.append(delta.get("text", ""))
        elif event.get("type") == "result":
            raw_parts = [event.get("result", "")]
            break

    raw = "".join(raw_parts).strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Raw output:\n{raw[:500]}")
        sys.exit(1)


# ── STEP 6: Generate EDL files ───────────────────────────────────────────────

def merge_adjacent_segments(segments: list, gap_sec: int = 40,
                             cooldown_sec: int = 15, duration: float = 0) -> list:
    """
    Tasks 16+17: Merge kept segments whose gap <= gap_sec into one continuous block
    (fixes mid-fight cuts), then extend each block's end by cooldown_sec so the
    reaction has room to finish before the cut.
    """
    if not segments:
        return segments

    sorted_segs = sorted(segments, key=lambda x: x["start_sec"])
    merged = [dict(sorted_segs[0])]

    for seg in sorted_segs[1:]:
        last = merged[-1]
        gap = seg["start_sec"] - last["end_sec"]
        if gap <= gap_sec:
            last["end_sec"] = seg["end_sec"]
            last["score"] = max(last.get("score", 0), seg.get("score", 0))
            last["reason"] = last.get("reason", "") + f" | {seg.get('reason', '')}"
        else:
            merged.append(dict(seg))

    cap = duration if duration > 0 else float("inf")
    for seg in merged:
        seg["end_sec"] = min(seg["end_sec"] + cooldown_sec, cap)

    return merged

def sec_to_tc(seconds: float, fps: int) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    f = int(round((seconds % 1) * fps))
    if f >= fps:
        f = fps - 1
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def slugify(text: str, max_len: int = 40) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text.strip("_")[:max_len].rstrip("_")


def generate_youtube_edl(analysis: dict, video_filename: str, fps: int, min_score: int = 5,
                          prefix: str = "", combined_data: list = None,
                          interval_sec: int = 30, duration: float = 0) -> str:
    all_segs = analysis.get("segments", [])
    good = [s for s in all_segs if s.get("score", 0) >= min_score]
    if not good:
        print("No segments above threshold -- using all")
        good = all_segs

    # Task 16+17: merge adjacent segments and add cooldown buffer
    good = merge_adjacent_segments(good, gap_sec=40, cooldown_sec=15, duration=duration)

    # Task 18: build excitement lookup for natural cut point snapping
    quiet_by_ts = {r["timestamp_sec"]: r.get("excitement", 5) for r in (combined_data or [])}

    lines = ["TITLE: Auto Cut - YouTube", "FCM: NON-DROP FRAME", ""]
    rec = 0.0
    for i, seg in enumerate(good, 1):
        src_in  = float(seg["start_sec"])
        src_out = float(seg["end_sec"])

        # Snap src_out to the quietest audio window within the next interval
        if quiet_by_ts:
            candidates = {ts: exc for ts, exc in quiet_by_ts.items()
                          if src_out <= ts <= src_out + interval_sec}
            if candidates:
                src_out = float(min(candidates, key=candidates.get))

        rec_out = rec + (src_out - src_in)
        lines.append(
            f"{i:03d}  AX       AA/V  C        "
            f"{sec_to_tc(src_in, fps)} {sec_to_tc(src_out, fps)} "
            f"{sec_to_tc(rec, fps)} {sec_to_tc(rec_out, fps)}"
        )
        lines.append(f"* FROM CLIP NAME: {video_filename}")
        lines.append(f"* SCORE: {seg.get('score','?')} -- {seg.get('reason','')}")
        lines.append("")
        rec = rec_out

    path = f"{prefix}output_youtube.edl"
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    print(f"YouTube EDL: {path}  ({rec/60:.1f} min, {len(good)} segments)")
    return path


def generate_shorts_edl(analysis: dict, video_filename: str, fps: int,
                         prefix: str = "", combined_data: list = None) -> list:
    highlights = sorted(
        analysis.get("shorts_highlights", []),
        key=lambda x: x["start_sec"]
    )

    # Task 19: context window validation only when signals are available
    has_signals = bool(combined_data) and any(
        r.get("vision_score", 0) > 0 or r.get("librosa_score", 0) >= 7
        for r in combined_data
    )

    paths = []
    for i, seg in enumerate(highlights, 1):
        src_in  = float(seg["start_sec"])
        src_out = float(seg["end_sec"])
        dur     = min(src_out - src_in, 60.0)
        src_out = src_in + dur

        raw_title = seg.get("title", "") or f"short_{i}"

        # Reject Short if no visual/onset event within ±60s
        if has_signals:
            window = [r for r in combined_data if abs(r["timestamp_sec"] - src_in) <= 60]
            has_visual = any(r.get("vision_score", 0) >= 2 for r in window)
            has_onset  = any(r.get("librosa_score", 0) >= 7 for r in window)
            if not has_visual and not has_onset:
                print(f"  SKIPPED {raw_title} — no visual/onset signal within 60s (pure voice peak)")
                continue

        filename = f"{prefix}short_{slugify(raw_title)}.edl"
        m_in, s_in = divmod(int(src_in), 60)
        m_out, s_out = divmod(int(src_out), 60)

        lines = [
            f"TITLE: {raw_title}",
            "FCM: NON-DROP FRAME",
            "",
            (
                f"001  AX       AA/V  C        "
                f"{sec_to_tc(src_in, fps)} {sec_to_tc(src_out, fps)} "
                f"{sec_to_tc(0, fps)} {sec_to_tc(dur, fps)}"
            ),
            f"* FROM CLIP NAME: {video_filename}",
            f"* {seg.get('reason', '')}",
            "",
        ]

        Path(filename).write_text("\n".join(lines), encoding="utf-8")
        print(f"  {filename}  ({dur:.0f}s)  [{m_in}:{s_in:02d} - {m_out}:{s_out:02d} in video]")
        paths.append(filename)

    return paths


# ── STEP 6c: Auto-detected shorts from combined peaks (Task 12) ──────────────

def generate_autodetected_shorts_edl(
    combined_data: list,
    video_filename: str,
    fps: int,
    claude_shorts: list,
    interval_sec: int = 20,
    peak_threshold: int = 8,
    prefix: str = "",
) -> list:
    """
    Generate shorts EDLs directly from combined_score peaks, supplementing
    Claude's suggestions. Skips any peak already covered by a Claude short.
    """
    peaks = [r for r in combined_data if r.get("combined_score", 0) >= peak_threshold]
    if not peaks:
        return []

    # Group consecutive peaks into clusters (gap <= 2 intervals)
    clusters: list[list] = []
    current: list = []
    for p in sorted(peaks, key=lambda x: x["timestamp_sec"]):
        if not current or p["timestamp_sec"] - current[-1]["timestamp_sec"] <= interval_sec * 2:
            current.append(p)
        else:
            clusters.append(current)
            current = [p]
    if current:
        clusters.append(current)

    claude_times = [s["start_sec"] for s in claude_shorts]
    paths = []
    auto_count = 0

    for cluster in clusters:
        peak = max(cluster, key=lambda x: x["combined_score"])
        ts = peak["timestamp_sec"]

        if any(abs(ts - ct) < 60 for ct in claude_times):
            continue

        src_in  = max(0.0, float(ts) - 10)
        src_out = src_in + 60.0
        detections = peak.get("vision_detections", [])
        det_str = ", ".join(detections) if detections else "audio peak"

        auto_count += 1
        filename = f"{prefix}short_auto_peak_{auto_count}.edl"
        m_in, s_in = divmod(int(src_in), 60)
        m_out, s_out = divmod(int(src_out), 60)

        lines = [
            f"TITLE: auto_peak_{auto_count}",
            "FCM: NON-DROP FRAME",
            "",
            (f"001  AX       AA/V  C        "
             f"{sec_to_tc(src_in, fps)} {sec_to_tc(src_out, fps)} "
             f"{sec_to_tc(0, fps)} {sec_to_tc(60, fps)}"),
            f"* FROM CLIP NAME: {video_filename}",
            f"* Auto-detected: combined={peak['combined_score']}/10  signals=[{det_str}]",
            "",
        ]
        Path(filename).write_text("\n".join(lines), encoding="utf-8")
        print(f"  {filename}  (60s)  [{m_in}:{s_in:02d}-{m_out}:{s_out:02d}]"
              f"  combined={peak['combined_score']}/10  [{det_str}]")
        paths.append(filename)

    return paths


# ── STEP 7: Summary ──────────────────────────────────────────────────────────

def print_summary(analysis: dict, audio_data: list):
    print("\n" + "=" * 55)
    print("ANALYSIS SUMMARY")
    print("=" * 55)

    summary = analysis.get("summary", "")
    if summary:
        print(f"Content: {summary}\n")

    audio_by_ts = {r["timestamp_sec"]: r for r in audio_data}

    segments = analysis.get("segments", [])
    print(f"Total segments: {len(segments)}")
    for s in segments:
        bar = "#" * s.get("score", 0) + "." * (10 - s.get("score", 0))
        ts = s["start_sec"]
        audio = audio_by_ts.get(ts, {})
        exc = audio.get("excitement", "-")
        voice_flag = " <VOICE PEAK>" if (isinstance(exc, int) and exc >= 7) else ""
        print(
            f"  [{ts:>5}s-{s['end_sec']:>5}s]"
            f" [{bar}] {s.get('score',0)}/10"
            f"  voice={exc}/10{voice_flag}"
            f"  {s.get('reason','')}"
        )

    cuts = analysis.get("cuts_to_remove", [])
    if cuts:
        total = sum(c["end_sec"] - c["start_sec"] for c in cuts)
        print(f"\nCuts: {len(cuts)} sections, {total:.0f}s ({total/60:.1f} min) removed")
        for c in cuts:
            print(f"  [{c['start_sec']}s-{c['end_sec']}s] {c.get('reason','')}")

    highlights = analysis.get("shorts_highlights", [])
    if highlights:
        print(f"\nShorts highlights: {len(highlights)} clips")
        for h in highlights:
            m_in, s_in = divmod(int(h["start_sec"]), 60)
            m_out, s_out = divmod(int(h["end_sec"]), 60)
            print(f"  [{m_in}:{s_in:02d}-{m_out}:{s_out:02d}]  {h.get('title','')}  --  {h.get('reason','')}")

    print("=" * 55)


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Auto-cut video with Claude CLI")
    parser.add_argument("video", help="Path to video file")
    parser.add_argument("--interval", type=int, default=30,
                        help="Frame extraction interval in seconds (default: 30)")
    parser.add_argument("--min-score", type=int, default=5,
                        help="Cut segments below this score (default: 5)")
    parser.add_argument("--no-whisper", action="store_true",
                        help="Skip Whisper transcription even if installed")
    parser.add_argument("--youtube", action="store_true",
                        help="Generate YouTube long-form EDL")
    parser.add_argument("--shorts", action="store_true",
                        help="Generate YouTube Shorts highlights EDLs")
    parser.add_argument("--prefix", type=str, default="",
                        help="Prefix string for all output filenames (e.g. --prefix 2)")
    args = parser.parse_args()

    # Default to both if neither flag is explicitly set
    do_youtube = args.youtube
    do_shorts = args.shorts
    if not do_youtube and not do_shorts:
        do_youtube = True
        do_shorts = True

    video_path = args.video
    if not Path(video_path).exists():
        print(f"File not found: {video_path}")
        sys.exit(1)

    video_filename = Path(video_path).name
    print(f"\nVideo: {video_filename}")

    duration = get_video_duration(video_path)
    fps = get_video_fps(video_path)
    print(f"Duration: {duration/60:.1f} min ({duration:.0f}s)  |  {fps} fps")

    frames = extract_keyframes(video_path, interval_sec=args.interval)

    audio_data    = analyze_audio_loudness(video_path, interval_sec=args.interval)
    librosa_data  = analyze_audio_librosa(video_path, interval_sec=args.interval)
    vision_data   = scan_frames_opencv(frames)
    combined_data = normalize_and_combine_scores(audio_data, librosa_data, vision_data)

    transcription = None
    if not args.no_whisper:
        transcription = transcribe_audio_whisper(video_path, interval_sec=args.interval)

    analysis = analyze_with_claude_cli(frames, duration, combined_data, transcription)

    prefix = args.prefix
    analysis_path = f"{prefix}analysis.json"
    Path(analysis_path).write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Analysis saved: {analysis_path}")

    if do_youtube:
        generate_youtube_edl(analysis, video_filename, fps, min_score=args.min_score,
                             prefix=prefix, combined_data=combined_data,
                             interval_sec=args.interval, duration=duration)

    shorts_paths = []
    if do_shorts:
        print("Shorts EDL files (Claude):")
        shorts_paths = generate_shorts_edl(analysis, video_filename, fps,
                                           prefix=prefix, combined_data=combined_data)
        print("Shorts EDL files (Auto-detected):")
        auto_paths = generate_autodetected_shorts_edl(
            combined_data, video_filename, fps,
            claude_shorts=analysis.get("shorts_highlights", []),
            interval_sec=args.interval,
            prefix=prefix,
        )
        shorts_paths += auto_paths

    print_summary(analysis, audio_data)

    shutil.rmtree("temp_frames", ignore_errors=True)

    if do_youtube or do_shorts:
        print("\nNext steps in DaVinci Resolve:")
        print("  1. File -> Import -> Media  -- add the original video")
        if do_youtube:
            print("  2. File -> Import -> Timeline  -- select output_youtube.edl")
        if do_shorts:
            step_num = 3 if do_youtube else 2
            print(f"  {step_num}. For each Short, import its EDL as a separate timeline:")
            for p in shorts_paths:
                print(f"       {p}")
        print(f"  {4 if do_youtube and do_shorts else 3}. Export timelines as needed\n")


if __name__ == "__main__":
    main()
