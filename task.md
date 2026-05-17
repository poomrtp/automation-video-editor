# Task List — Video Editor Improvement Plan

| #   | Task                                                                                                | Status |
| --- | --------------------------------------------------------------------------------------------------- | ------ |
| 1   | Delete old output files (analysis.json, all \*.edl in root)                                         | done   |
| 2   | Update `build_prompt()` — YouTube long: aggressive silence removal, keep peaks + story arc only     | done   |
| 3   | Update `build_prompt()` — YouTube Shorts: 100% retention rule, hook in 3s, every second must matter | done   |
| 4   | Run `analyze_video.py` on `vdo/LoL game 2.mp4` with `--interval 20 --min-score 7`                   | done   |
| 5   | Verify output EDL files generated correctly                                                         | done   |
| 6   | Update this file with final task status                                                             | done   |
| 7   | Add `--youtube` and `--shorts` CLI flags for selective EDL generation                               | done   |
| 8   | Run `pip install -r requirements.txt` to install OpenCV, MoviePy, and NumPy                         | done   |
| 9   | Implement Librosa Audio Scan function to detect shouting/loud peak moments                          | done   |
| 10  | Implement OpenCV UI Scan to detect LoL Kill Feed / Multi-kill popups                                | done   |
| 11  | Create `normalize_and_combine_scores()` logic to merge Text, Audio, and Vision data                 | done   |
| 12  | Update EDL generation script to trigger shorts based on the new combined peak scores                | done   |
| 13  | Test the complete multimodal pipeline on `vdo/LoL game 2.mp4`                                       | blocked — video file missing from vdo/ |

---

## Phase 2 — Cut Quality Fixes (Bad Cuts & False Positives)

> **Goal:** Fix two problems — (1) cuts that happen mid-fight before the reaction finishes, (2) mic spikes with no visual context being included as highlights.

| #  | Task | Problem It Fixes | Effort | Status |
|----|------|-----------------|--------|--------|
| 14 | `pip install opencv-python librosa` — enable visual validation | Both problems | 5 min | done |
| 15 | Add `pure_voice_peak` flag in `normalize_and_combine_scores()` — cap score at 5 if voice peak has zero vision signal | Mic spike false positives | 30 min | done |
| 16 | Add `merge_adjacent_segments()` — merge kept segments within 40s gap into one continuous clip | Cuts mid-fight | 1 hr | done |
| 17 | Add 15s cooldown buffer to every kept segment so reaction has room to finish before cut | Cuts mid-fight | 30 min | done |
| 18 | Add natural cut point scan in `generate_youtube_edl()` — find quietest audio sample within 8s of boundary and cut there | Cuts mid-sentence | 30 min | done |
| 19 | Add context window validation in `generate_shorts_edl()` — reject Short if no kill/onset event within ±60s | Mic spike false positives | 45 min | done |
| 20 | Update `build_prompt()` anti-patterns — tell Claude never to end mid-fight, never start Short without visual payoff, cap pure voice peaks at 5 | Both problems | 20 min | done |
| 21 | End-to-end test on `vdo/2026-05-16 14-02-28.mp4` and verify no mid-fight cuts or false-positive Shorts in output | Verification | 30 min | done |

---

## Output Files

| File                                  | Duration | Notes                                         |
| ------------------------------------- | -------- | --------------------------------------------- |
| `output_youtube.edl`                  | 11 min   | 12 segments, 27 min of silence/filler removed |
| `short_first_blood_loud_reaction.edl` | 60s      | 4:20–5:20, first blood burst, 8/10 audio hook |
| `short_midgame_clutch_reaction.edl`   | 60s      | 30:20–31:20, mid-game clutch peak             |
| `short_loudest_late_game_peak.edl`    | 60s      | 35:20–36:20, loudest moment in second half    |

## Run Command Used

```
python analyze_video.py "vdo/LoL game 2.mp4" --interval 20 --min-score 7
```
