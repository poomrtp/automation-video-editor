# Automation Video Editor (Claude Edition)

This project has been updated to use the Claude CLI for video analysis and automated trimming.

## Goals
- Automatically identify and trim uninteresting video segments.
- Generate separate timelines for YouTube (long-form) and YouTube Shorts (short-form).
- Leverage the Claude Pro subscription via CLI for advanced visual analysis.

## Technical Stack
- **AI:** Claude Code CLI (Pro Subscription)
- **Video Editor:** DaVinci Resolve
- **Scripting:** Python 3
- **Tools:** FFmpeg (for frame extraction and duration analysis)
- **Bridge:** EDL (Edit Decision List)

## Workflow
1. **Frame Extraction:** `ffmpeg` extracts keyframes at a set interval.
2. **AI Analysis:** Claude analyzes the frames and duration, providing a JSON of scored segments and highlights.
3. **EDL Generation:** Python script converts the analysis into `.edl` files.
4. **Import:** DaVinci Resolve imports the `.edl` files to create automated timelines.

## Key Files
- `analyze_video.py`: Main entry point for the automation.
- `README.md`: Bilingual (TH/EN) setup and usage instructions.
- `requirements.txt`: Project dependencies.
