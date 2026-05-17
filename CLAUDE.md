# Claude Video Automation Instructions

This file contains instructions for the Claude Code CLI and information about the project structure.

## Project Overview
The `analyze_video.py` script uses the `claude` CLI command to analyze videos. It does this by:
1. Extracting frames using `ffmpeg`.
2. Building a prompt with Base64 encoded images.
3. Sending the prompt to the `claude` CLI using `subprocess`.
4. Parsing the JSON response to generate EDL files.

## Environment Requirements
- **FFmpeg**: Must be in the system PATH (`ffmpeg` and `ffprobe`).
- **Claude CLI**: Must be installed and logged in (`claude login`).
- **Python**: 3.8 or higher.

## Script Usage
- `python analyze_video.py <video_path>`: Standard run.
- `--interval <sec>`: Change frequency of frame extraction (default 30s).
- `--min-score <0-10>`: Minimum score to keep a segment for YouTube (default 5).

## Customizing the Prompt
The prompt is defined in the `build_prompt()` function within `analyze_video.py`. If Claude struggles with the format, adjust the instructions there.
