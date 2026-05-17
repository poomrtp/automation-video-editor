#!/usr/bin/env python3
"""
analyze_video.py — ใช้ Claude CLI วิเคราะห์ video แล้ว generate EDL สำหรับ DaVinci Resolve

ใช้งาน:
    python analyze_video.py myvideo.mp4
    python analyze_video.py myvideo.mp4 --interval 20
    python analyze_video.py myvideo.mp4 --min-score 5
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


# ─── STEP 1: Extract frames จาก video ───────────────────────────────────────

def get_video_duration(video_path: str) -> float:
    """ดึงความยาว video เป็น seconds"""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"❌ ffprobe error: {result.stderr}")
        sys.exit(1)
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def extract_keyframes(video_path: str, interval_sec: int = 30) -> list[dict]:
    """ดึง frame ทุก N วินาที เก็บไว้ใน temp_frames/"""
    frames_dir = Path("temp_frames")
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir()

    print(f"🎞️  Extracting frames (every {interval_sec}s)...")
    result = subprocess.run([
        "ffmpeg", "-i", video_path,
        "-vf", f"fps=1/{interval_sec}",
        "-q:v", "3",                         # quality 3 = ขนาดพอดี ไม่ใหญ่เกิน
        str(frames_dir / "frame_%06d.jpg"),
        "-y", "-loglevel", "error"
    ], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"❌ ffmpeg error: {result.stderr}")
        sys.exit(1)

    frames = []
    for i, frame_file in enumerate(sorted(frames_dir.glob("*.jpg"))):
        timestamp = i * interval_sec
        with open(frame_file, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        frames.append({
            "timestamp_sec": timestamp,
            "b64": b64,
            "path": str(frame_file)
        })

    print(f"✅ Got {len(frames)} frames")
    return frames


# ─── STEP 2: ส่งให้ Claude CLI วิเคราะห์ ────────────────────────────────────

def build_prompt(frames: list[dict], duration: float) -> str:
    """สร้าง prompt สำหรับส่งให้ Claude"""
    frame_list = "\n".join(
        f"- Frame {i+1}: timestamp={f['timestamp_sec']}s"
        for i, f in enumerate(frames)
    )

    return f"""คุณเป็น video editor ผู้เชี่ยวชาญ YouTube และ Short-form content

ข้อมูล video:
- ความยาวรวม: {duration:.0f} วินาที ({duration/60:.1f} นาที)  
- จำนวน frames: {len(frames)} frames (ทุก ~30 วินาที)
- Frames ที่แนบมา: base64 images ด้านล่าง

{frame_list}

งาน:
1. ให้คะแนนแต่ละ segment (ระหว่าง frame แต่ละคู่) 0-10:
   - 0-3: น่าเบื่อมาก (หน้าจอนิ่ง, พูดซ้ำ, เงียบนาน, ไม่มี action)
   - 4-6: ปานกลาง (เนื้อหาดี แต่ไม่ highlight)
   - 7-10: น่าสนใจมาก (key point, demo, อารมณ์, ตลก, action)

2. ระบุ segments ที่ควรตัดออก (score < 5)

3. เลือก 2-3 highlight สำหรับ YouTube Shorts (รวมไม่เกิน 55 วินาที)

ตอบเป็น JSON เท่านั้น ห้ามมี markdown หรือ text นอก JSON:
{{
  "segments": [
    {{
      "start_sec": 0,
      "end_sec": 30,
      "score": 8,
      "reason": "intro hook น่าสนใจ"
    }}
  ],
  "cuts_to_remove": [
    {{
      "start_sec": 60,
      "end_sec": 90,
      "reason": "ช่วงนี้พูดซ้ำ ไม่มีข้อมูลใหม่"
    }}
  ],
  "shorts_highlights": [
    {{
      "start_sec": 120,
      "end_sec": 150,
      "reason": "demo ชัดเจน เหมาะทำ Short"
    }}
  ],
  "summary": "สรุปสั้นๆ ว่าเนื้อหา video นี้เป็นอะไร"
}}"""


def analyze_with_claude_cli(frames: list[dict], duration: float) -> dict:
    """เรียก claude CLI พร้อมส่ง prompt — ใช้ Pro subscription โดยไม่ต้องมี API key"""

    prompt = build_prompt(frames, duration)

    # เขียน prompt ลงไฟล์ชั่วคราว
    prompt_file = Path("temp_prompt.txt")
    prompt_file.write_text(prompt, encoding="utf-8")

    print("🤖 Sending to Claude CLI...")

    # เรียก claude CLI
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True, timeout=120
    )

    prompt_file.unlink(missing_ok=True)

    if result.returncode != 0:
        print(f"❌ Claude CLI error:\n{result.stderr}")
        print("💡 ลองรัน: claude --version  เพื่อตรวจสอบว่า login แล้วหรือยัง")
        sys.exit(1)

    raw = result.stdout.strip()

    # clean JSON ถ้ามี markdown fence
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"❌ JSON parse error: {e}")
        print(f"Raw output:\n{raw[:500]}")
        sys.exit(1)


# ─── STEP 3: Generate EDL files ─────────────────────────────────────────────

def sec_to_tc(seconds: float, fps: int = 25) -> str:
    """แปลง seconds → timecode HH:MM:SS:FF"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    f = int((seconds % 1) * fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def generate_youtube_edl(analysis: dict, video_filename: str, min_score: int = 5) -> str:
    """สร้าง EDL สำหรับ YouTube — ตัด segments ที่ score ต่ำออก"""

    all_segments = analysis.get("segments", [])
    good_segments = [s for s in all_segments if s.get("score", 0) >= min_score]

    if not good_segments:
        print("⚠️  ไม่มี segment ที่ผ่านเกณฑ์ — ใช้ทุก segment แทน")
        good_segments = all_segments

    edl_lines = [
        "TITLE: Auto Cut - YouTube",
        "FCM: NON-DROP FRAME",
        ""
    ]

    rec = 0.0
    for i, seg in enumerate(good_segments, 1):
        src_in = float(seg["start_sec"])
        src_out = float(seg["end_sec"])
        rec_out = rec + (src_out - src_in)

        edl_lines.append(
            f"{i:03d}  AX       AA/V  C        "
            f"{sec_to_tc(src_in)} {sec_to_tc(src_out)} "
            f"{sec_to_tc(rec)} {sec_to_tc(rec_out)}"
        )
        edl_lines.append(f"* FROM CLIP NAME: {video_filename}")
        edl_lines.append(f"* SCORE: {seg.get('score', '?')} — {seg.get('reason', '')}")
        edl_lines.append("")
        rec = rec_out

    path = "output_youtube.edl"
    Path(path).write_text("\n".join(edl_lines), encoding="utf-8")
    total_min = rec / 60
    print(f"✅ YouTube EDL: {path}  ({total_min:.1f} นาที, {len(good_segments)} segments)")
    return path


def generate_shorts_edl(analysis: dict, video_filename: str) -> str:
    """สร้าง EDL สำหรับ Shorts — เลือก highlights ไม่เกิน 55 วินาที"""

    highlights = analysis.get("shorts_highlights", [])

    # จำกัดรวมไม่เกิน 55 วินาที
    selected = []
    total = 0.0
    for h in highlights:
        dur = float(h["end_sec"]) - float(h["start_sec"])
        if total + dur <= 55:
            selected.append(h)
            total += dur

    # เรียงตาม timestamp จริง
    selected.sort(key=lambda x: x["start_sec"])

    edl_lines = [
        "TITLE: Auto Cut - Shorts",
        "FCM: NON-DROP FRAME",
        ""
    ]

    rec = 0.0
    for i, seg in enumerate(selected, 1):
        src_in = float(seg["start_sec"])
        src_out = float(seg["end_sec"])
        rec_out = rec + (src_out - src_in)

        edl_lines.append(
            f"{i:03d}  AX       AA/V  C        "
            f"{sec_to_tc(src_in)} {sec_to_tc(src_out)} "
            f"{sec_to_tc(rec)} {sec_to_tc(rec_out)}"
        )
        edl_lines.append(f"* FROM CLIP NAME: {video_filename}")
        edl_lines.append(f"* {seg.get('reason', '')}")
        edl_lines.append("")
        rec = rec_out

    path = "output_shorts.edl"
    Path(path).write_text("\n".join(edl_lines), encoding="utf-8")
    print(f"✅ Shorts EDL:  {path}  ({total:.0f} วินาที, {len(selected)} clips)")
    return path


# ─── STEP 4: แสดงผลสรุป ─────────────────────────────────────────────────────

def print_summary(analysis: dict):
    print("\n" + "="*50)
    print("📊 ANALYSIS SUMMARY")
    print("="*50)

    summary = analysis.get("summary", "")
    if summary:
        print(f"เนื้อหา: {summary}\n")

    segments = analysis.get("segments", [])
    print(f"Segments ทั้งหมด: {len(segments)}")
    for s in segments:
        bar = "█" * s.get("score", 0) + "░" * (10 - s.get("score", 0))
        print(f"  [{s['start_sec']:>4}s-{s['end_sec']:>4}s] {bar} {s.get('score',0)}/10  {s.get('reason','')}")

    cuts = analysis.get("cuts_to_remove", [])
    if cuts:
        total_cut = sum(c["end_sec"] - c["start_sec"] for c in cuts)
        print(f"\n✂️  ตัดออก {len(cuts)} ช่วง รวม {total_cut:.0f} วินาที ({total_cut/60:.1f} นาที)")
        for c in cuts:
            print(f"  [{c['start_sec']}s-{c['end_sec']}s] {c.get('reason','')}")

    highlights = analysis.get("shorts_highlights", [])
    if highlights:
        print(f"\n⚡ Shorts highlights: {len(highlights)} clips")
        for h in highlights:
            print(f"  [{h['start_sec']}s-{h['end_sec']}s] {h.get('reason','')}")

    print("="*50)


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Auto-cut video ด้วย Claude CLI")
    parser.add_argument("video", help="Path ไปยัง video file (.mp4, .mov, etc.)")
    parser.add_argument("--interval", type=int, default=30,
                        help="ดึง frame ทุกกี่วินาที (default: 30)")
    parser.add_argument("--min-score", type=int, default=5,
                        help="ตัด segment ที่ score ต่ำกว่านี้ออก (default: 5)")
    args = parser.parse_args()

    video_path = args.video
    if not Path(video_path).exists():
        print(f"❌ ไม่พบไฟล์: {video_path}")
        sys.exit(1)

    video_filename = Path(video_path).name
    print(f"\n🎬 Video: {video_filename}")

    # 1. ดึงความยาว video
    duration = get_video_duration(video_path)
    print(f"⏱  ความยาว: {duration/60:.1f} นาที ({duration:.0f}s)")

    # 2. Extract frames
    frames = extract_keyframes(video_path, interval_sec=args.interval)

    # 3. วิเคราะห์ด้วย Claude
    analysis = analyze_with_claude_cli(frames, duration)

    # 4. บันทึก analysis JSON
    analysis_path = "analysis.json"
    Path(analysis_path).write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"💾 Analysis saved: {analysis_path}")

    # 5. Generate EDL files
    generate_youtube_edl(analysis, video_filename, min_score=args.min_score)
    generate_shorts_edl(analysis, video_filename)

    # 6. แสดงสรุป
    print_summary(analysis)

    # 7. Cleanup frames
    shutil.rmtree("temp_frames", ignore_errors=True)

    print("\n🎯 ขั้นตอนต่อไปใน DaVinci Resolve:")
    print("  1. File → Import → Media  →  เลือก video ต้นฉบับ")
    print("  2. File → Import → Timeline  →  เลือก output_youtube.edl")
    print("  3. ทำซ้ำข้อ 2 กับ output_shorts.edl สำหรับ Shorts")
    print("  4. ตรวจ timeline แล้ว export ได้เลย\n")


if __name__ == "__main__":
    main()
