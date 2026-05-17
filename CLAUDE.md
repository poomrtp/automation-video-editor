# Claude Video Automation Instructions

This file contains instructions for the Claude Code CLI and information about the project structure.

## Project Overview
The `analyze_video.py` script uses the `claude` CLI command to analyze videos. It does this by:
1. Extracting frames using `ffmpeg`.
2. Analyzing audio loudness per segment using the built-in `wave` module.
3. Building a prompt with Base64 encoded images + audio excitement data.
4. Sending the prompt to the `claude` CLI using `subprocess`.
5. Parsing the JSON response to generate EDL files for DaVinci Resolve.

## Environment Requirements
- **FFmpeg**: Must be in the system PATH (`ffmpeg` and `ffprobe`).
- **Claude CLI**: Must be installed and logged in (`claude login`).
- **Python**: 3.8 or higher. All dependencies are stdlib except optional Whisper.

## Script Usage
- `python analyze_video.py <video_path>`: Standard run.
- `--interval <sec>`: Frame extraction frequency (recommended: `20`).
- `--min-score <0-10>`: Minimum score to keep a segment (recommended: `7` for peaks-only YouTube cut).
- `--no-whisper`: Skip Whisper transcription even if installed.

### Recommended Command
```
python analyze_video.py "vdo/<file>.mp4" --interval 20 --min-score 7
```

## Output Files
- `output_youtube.edl` — long-form YouTube timeline (peaks + story bridges only).
- `short_<title>.edl` — one EDL per YouTube Short (up to 5 clips, 45–60s each).
- `analysis.json` — raw Claude JSON response (ignored by git).

## Prompt Philosophy
The prompt in `build_prompt()` uses two distinct strategies:

### YouTube Long Edit
- Score 0–2: MUST CUT — silence, dead air, loading screens.
- Score 3–4: CUT — routine laning with no voice reactions.
- Score 5–6: STORY BRIDGE — keep only if needed to connect two high-action moments.
- Score 7–8: KEEP — clear action or loud voice (excitement ≥ 7).
- Score 9–10: ALWAYS KEEP — multi-kill, clutch, game-defining peak.

### YouTube Shorts (retention-optimized)
- Hook in the first 3 seconds — start at the moment of impact, not the build-up.
- Every 3 seconds must have something happening.
- 45–60 second window maximum.
- No cool-down periods or post-fight lulls — end before they start.
- Only pick from segments scoring 8+.

## Task Tracking
Use `task.md` in the project root to list and track tasks for each video edit session. Update status to `done` at the end of the run.

## Customizing the Prompt
The prompt is defined in `build_prompt()` in `analyze_video.py`. Adjust scoring thresholds or Shorts rules there if Claude's output needs tuning.
