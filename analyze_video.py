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
    audio_data: list,
    transcription: dict,
) -> str:

    # Build audio lookup by timestamp
    audio_by_ts = {r["timestamp_sec"]: r for r in audio_data}

    lines = []
    for i, f in enumerate(frames):
        ts = int(f["timestamp_sec"])
        audio = audio_by_ts.get(ts, {})
        exc = audio.get("excitement", "?")
        db = audio.get("rms_db", "?")
        voice_tag = ""
        if exc != "?" and int(exc) >= 7:
            voice_tag = "LOUD VOICE REACTION"
        elif exc != "?" and int(exc) >= 5:
            voice_tag = "active talking"

        img_tag = "<-- IMAGE ATTACHED" if i in sample_indices else ""
        trans = transcription.get(i, "") if transcription else ""
        trans_part = f'  voice: "{trans}"' if trans else ""

        line = (
            f"  Frame {i+1}: video_time={ts}s"
            f"  audio_excitement={exc}/10 ({db}dBFS)"
            f"  {voice_tag}"
            f"  {img_tag}"
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
    audio_data: list,
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
    prompt = build_prompt(frames, sample_indices, duration, audio_data, transcription)

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


def generate_youtube_edl(analysis: dict, video_filename: str, fps: int, min_score: int = 5) -> str:
    all_segs = analysis.get("segments", [])
    good = [s for s in all_segs if s.get("score", 0) >= min_score]
    if not good:
        print("No segments above threshold -- using all")
        good = all_segs

    lines = ["TITLE: Auto Cut - YouTube", "FCM: NON-DROP FRAME", ""]
    rec = 0.0
    for i, seg in enumerate(good, 1):
        src_in = float(seg["start_sec"])
        src_out = float(seg["end_sec"])
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

    path = "output_youtube.edl"
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    print(f"YouTube EDL: {path}  ({rec/60:.1f} min, {len(good)} segments)")
    return path


def generate_shorts_edl(analysis: dict, video_filename: str, fps: int) -> list:
    highlights = sorted(
        analysis.get("shorts_highlights", []),
        key=lambda x: x["start_sec"]
    )

    paths = []
    for i, seg in enumerate(highlights, 1):
        src_in = float(seg["start_sec"])
        src_out = float(seg["end_sec"])
        dur = min(src_out - src_in, 60.0)
        src_out = src_in + dur

        raw_title = seg.get("title", "") or f"short_{i}"
        filename = f"short_{slugify(raw_title)}.edl"

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
    parser = argparse.ArgumentParser(description="Auto-cut video with Claude CLI")
    parser.add_argument("video", help="Path to video file")
    parser.add_argument("--interval", type=int, default=30,
                        help="Frame extraction interval in seconds (default: 30)")
    parser.add_argument("--min-score", type=int, default=5,
                        help="Cut segments below this score (default: 5)")
    parser.add_argument("--no-whisper", action="store_true",
                        help="Skip Whisper transcription even if installed")
    args = parser.parse_args()

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

    audio_data = analyze_audio_loudness(video_path, interval_sec=args.interval)

    transcription = None
    if not args.no_whisper:
        transcription = transcribe_audio_whisper(video_path, interval_sec=args.interval)

    analysis = analyze_with_claude_cli(frames, duration, audio_data, transcription)

    Path("analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Analysis saved: analysis.json")

    generate_youtube_edl(analysis, video_filename, fps, min_score=args.min_score)

    print("Shorts EDL files:")
    shorts_paths = generate_shorts_edl(analysis, video_filename, fps)

    print_summary(analysis, audio_data)

    shutil.rmtree("temp_frames", ignore_errors=True)

    print("\nNext steps in DaVinci Resolve:")
    print("  1. File -> Import -> Media  -- add the original video")
    print("  2. File -> Import -> Timeline  -- select output_youtube.edl")
    print("  3. For each Short, import its EDL as a separate timeline:")
    for p in shorts_paths:
        print(f"       {p}")
    print("  4. Export each Short timeline separately\n")


if __name__ == "__main__":
    main()
