# Automation Video Editor (Claude Edition)

This project automatically cuts and highlights gaming videos using the Claude CLI for AI analysis.

## Goals
- Cut all silence and dead zones from YouTube long-form videos — keep only peaks and story bridges.
- Generate retention-optimized YouTube Shorts (45–60s, hook in first 3 seconds).
- Use Claude Pro subscription via CLI for visual + audio analysis.
- Output EDL files importable directly into DaVinci Resolve.

## Technical Stack
- **AI:** Claude Code CLI (Pro Subscription)
- **Video Editor:** DaVinci Resolve
- **Scripting:** Python 3 (stdlib only, no heavy dependencies)
- **Audio Analysis:** Built-in `wave` module for per-segment RMS loudness
- **Optional Transcription:** OpenAI Whisper (`pip install openai-whisper`)
- **Tools:** FFmpeg (frame extraction, audio extraction, duration probe)
- **Bridge:** EDL (Edit Decision List)

## Workflow
1. **Frame Extraction:** `ffmpeg` extracts one frame every 20 seconds.
2. **Audio Analysis:** RMS loudness computed per segment, normalized to 0–10 excitement scale.
3. **AI Analysis:** Claude receives 10 sampled frames + all audio excitement data and returns scored segments, cuts, and Short highlights.
4. **EDL Generation:** Python converts the JSON response into `.edl` files.
5. **Import:** DaVinci Resolve imports EDLs to create automated timelines.

## Scoring System
| Score | Meaning | Action |
|-------|---------|--------|
| 0–2 | Silence, dead air, loading screen | Always cut |
| 3–4 | Routine gameplay, no reactions | Cut |
| 5–6 | Moderate activity or story bridge | Keep only if connecting two action moments |
| 7–8 | Clear action + loud voice reaction | Keep |
| 9–10 | Multi-kill, clutch, loudest peak | Always keep |

## Key Files
- `analyze_video.py` — main script, single entry point.
- `CLAUDE.md` — instructions for Claude Code CLI.
- `README.md` — bilingual (TH/EN) setup and usage guide.
- `requirements.txt` — Python dependencies.
- `task.md` — per-session task tracking (created before each run, updated after).

## Recommended Command
```
python analyze_video.py "vdo/<file>.mp4" --interval 20 --min-score 7
```
