# Task List — LoL Game 2 Re-Edit

| # | Task | Status |
|---|------|--------|
| 1 | Delete old output files (analysis.json, all *.edl in root) | done |
| 2 | Update `build_prompt()` — YouTube long: aggressive silence removal, keep peaks + story arc only | done |
| 3 | Update `build_prompt()` — YouTube Shorts: 100% retention rule, hook in 3s, every second must matter | done |
| 4 | Run `analyze_video.py` on `vdo/LoL game 2.mp4` with `--interval 20 --min-score 7` | done |
| 5 | Verify output EDL files generated correctly | done |
| 6 | Update this file with final task status | done |

---

## Output Files

| File | Duration | Notes |
|------|----------|-------|
| `output_youtube.edl` | 11 min | 12 segments, 27 min of silence/filler removed |
| `short_first_blood_loud_reaction.edl` | 60s | 4:20–5:20, first blood burst, 8/10 audio hook |
| `short_midgame_clutch_reaction.edl` | 60s | 30:20–31:20, mid-game clutch peak |
| `short_loudest_late_game_peak.edl` | 60s | 35:20–36:20, loudest moment in second half |

## Run Command Used
```
python analyze_video.py "vdo/LoL game 2.mp4" --interval 20 --min-score 7
```
