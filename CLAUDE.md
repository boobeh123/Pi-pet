# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Pi Pet is a terminal pet (a raspberry) whose mood, scene and visitors follow a Raspberry Pi 5's real readings. It's a single Python 3 file using only the standard library (curses): no dependencies, no build step, no linter config.

## Commands

```bash
python3 piPet.py                       # live view (p to pet, w to water, ? for help, q to quit)
python3 piPet.py --once --mood dizzy   # print a snapshot and exit; --mood forces any key of FACES
python3 piPet.py --iface eth0          # watch one interface instead of following the default route

python3 -m unittest discover -s tests -p "*Test.py"                # all tests (~10 s)
python3 -m unittest discover -s tests -p "*Test.py" -k SceneTest   # one class
python3 -m unittest discover -s tests -p "*Test.py" -k test_status_line_always_shows   # one test
```

Add `-B` to keep `__pycache__` out of the tree.

**Checking the live view without a terminal.** curses needs a TTY, so use `script`. Note `timeout --foreground`: without it the child runs in a background process group, gets SIGTTOU when curses touches the terminal, and just hangs.

```bash
script -q -c "stty cols 80 rows 24; TERM=xterm-256color timeout --foreground 2 python3 -B piPet.py --mood sleepy; echo EXIT=\$?" /dev/null < /dev/null
```

**The live view writes the owner's memories file** (`~/.local/state/pi-pet/memories.json`: visits, pettings, shooting stars). For live checks, point it at a scratch folder with `XDG_STATE_HOME=<scratch dir>` inside the `script -c "..."` command, so test runs don't add fake visits. `--once` never touches it.

`EXIT=124` means it ran until the timeout. Look for `Traceback` in the output. To send keys, pipe them in: `(sleep 1.5; printf p; sleep 4; printf q) | script ...`.

## Architecture

The data flows one way: **sensors → `Vitals` → `mood()` → `layout()` → curses**.

- **Readers** (`read_*`, `get_default_interface`, `read_process_names`, `is_adguard_installed`) read `/sys`, `/proc` and `os.statvfs` directly. Each returns `None` when its source is missing, and a `None` reading must never trigger a mood or alert. The project runs on non-Pi machines, where every sensor is missing.
- **`Vitals`** refreshes every reading on each `refresh()`, which happens every 2 s in the live loop.
  - Network speed comes from `update_net_rate()`. It skips readings closer than `MIN_RATE_SECONDS`, and it resets the measurement when the default-route interface changes.
  - `mood()` is an ordered priority chain: dizzy > hot > stuffed > hungry > groggy > lonely > busy > surfing > sleepy > content. `happy` exists only for petting and `--mood`; the sensors never pick it.
- **Builders** (`build_pet`, `build_extras`, `build_pals`, `build_plant`, `build_sky`, `build_bubble`, `build_status`, `build_alerts`) return text, or cells *relative to the pet's top-left corner*. They never know the window size, except `build_sky` and `build_ground`, which span the full width.
- **`layout(height, width, mood, tick, saying, vitals)`** is a pure function that returns `(row, col, text, color)` cells, all clipped to the window. All placement logic lives here. `run_live` paints the cells with curses, and `print_once` turns them into text with `render_lines()` (at the terminal's size capped to 80×24, without the key hint). Testing the drawing means calling `layout()`, never curses. A new color needs an entry in both `curses_colors()` and `ANSI_COLORS`; a test checks the ANSI side.
  - **`show_help=True`** replaces the whole scene with the help page from `build_help()` (labels plus `explain_mood`, `describe_sky`, `describe_pals`, `describe_plant`, wrapped with `textwrap`). New scene features should get a line there too.
  - **When space runs out, things drop in this order:** scene (sky and ground; needs ≥40 columns and room for everything else), then the alert line, then the bubble, then the quit hint. Pals and the plant appear only if they fit horizontally. Below `MIN_WIDTH`×`MIN_HEIGHT` (24×11), only "window too small" is shown.
  - **Fixed zones around the pet**, which is 13 columns wide (`PET_WIDTH`):
    - Extras (Zs, rain cloud, dizzy stars, hearts) stay in pet rows 0–5, right of the body, at most column 21.
    - Pals line up to the left by how permanent they are: the guard nearest, then the doctor, then Claude.
    - The plant sits at `PLANT_OFFSET` (24) to the right, and the Halloween jack-o'-lantern at `PUMPKIN_OFFSET` (31). While watering (`layout(..., watering=True)`), the can hangs in pet rows 0-1 with its spout at the plant's middle column, and drops fall from it to the plant's top.
    - Pals and the plant stand on the pet's lowest row (`feet_row`), just above the ground.
    - Nothing goes in the bubble's rows. That was a real bug once.
  - **The bounce** (busy/happy) arrives from `build_pet` as a leading `''` line. `layout()` strips it and moves the pet instead: up into the hop row when the ground is shown, down into the blank row otherwise. That way the bubble never moves.
  - Nothing is drawn on the bottom row or in the last column. curses raises an error when writing the bottom-right corner.
- **`LiveState`** owns the live view's timing rules (`FPS` = 4 ticks/s), apart from curses so `LiveStateTest` can drive them with a fake clock (`now`) and scripted readings:
  - `press(key, now)` handles keys (p pets, w waters for `WATER_SECONDS`, ? or h toggles `show_help`) and returns False to quit;
  - `step(tick, now)` refreshes the sensors every 2 s, picks a new saying every ~10 s or on a mood change (never at tick 0, so the opening line survives), runs the petting timer (`petted_until`) and Claude's arrival greeting, and returns the mood to show.
  - `notice(cells, now)` looks at each drawn frame: a shooting star appearing starts a `WISH_SECONDS` "make a wish!" (`wish_until`), which petting blocks and the saying rotation waits for.
  - Memories: `LiveState(memories=..., wall_time=...)` opens with "missed you!" after `MISSED_YOU_SECONDS` away, and `remember(key)` counts pettings (once per petting) and shooting stars into `memories` and `unsaved`. It never touches files: `run_live` loads them with `load_memories`, and saves with `update_memories`, which re-reads the file and adds only the new counts, so two Pi Pets running at once don't overwrite each other. `save_memories` writes a temp file and `os.replace`s it.
  - `run_live` is only the curses loop around it, with the colors from `curses_colors()`.
  - Animation speed comes from tick arithmetic in the builders, e.g. `tick // 2`.
- **Holidays** come from `holiday_on(month, day)` into `vitals.holiday` (and `vitals.year` for the birthday age). They reach the drawing through parameters: `build_pet(..., holiday)` (party hat), `build_sky(..., holiday)` (bats, fireworks, Pi Day clouds), `build_ground(width, holiday, tick)` (Christmas lights, returned as `(col, text, color)` pieces that never overlap) and the pumpkin in `layout()`. `holiday_sayings()` feeds `pick_saying()`, and `HOLIDAY_DESCRIPTIONS` feeds the help page. A new holiday needs an entry in both dicts (a test checks).
- **`pick_saying()`**: calm moods (`CHATTY_MOODS`) sometimes use a friend line (`CLAUDE_SAYINGS`, `GUARD_SAYINGS`, `PLANT_SAYINGS`) instead of their own `SAYINGS`.

## Rules the tests enforce

- **Every drawn character must be exactly one terminal column wide.** Only East Asian width `Na`/`N`/`H` is allowed, with no combining marks: no emoji, and no ambiguous-width characters like `﹏`. All lines of one sprite must have equal length. Check new art with `unicodedata.east_asian_width`.
- `FACES` and `SAYINGS` must have the same keys. Adding a key to `FACES` also makes it a valid `--mood` choice automatically.
- `LayoutTest` and `SceneTest` sweep every mood × saying × tick × several window sizes (and times of day, with both pals and the full-grown plant). They check that nothing spills outside the window and that no two non-space characters overlap. Any new on-screen element must pass these sweeps.

## Working on tests

- `make_vitals(...)` in `tests/piPetTest.py` builds a `Vitals` with `object.__new__`, which bypasses the real sensors. Its defaults describe a healthy Pi. **Any new `Vitals` attribute must be added there** with a healthy default, or a dozen tests fail with `AttributeError`.
- Sensor readers are tested with `mock.patch('builtins.open', ...)`, `mock.mock_open(read_data=...)` and patched `glob.glob`/`os.statvfs`, not with real files.
- New sweep coverage belongs in the existing `all_layouts` and `SceneTest` loops, through the `make_vitals` arguments they pass. Don't add separate full-`layout()` sweeps; they already take ~6 s. (`CloudTest` sweeps only `build_sky`, which is cheap.)

## Conventions

- Python code uses snake_case (PEP 8). File names are camelCase (`piPet.py`, `piPetTest.py`) to match the owner's standards. The owner's standards say camelCase for functions and variables too; snake_case was kept on purpose, and the question is still open.
- Commit messages are plain-English, imperative, and under 72 characters (e.g. "Add a potted plant that grows with the Pi's uptime").
- The README documents every mood, pal, data source and the test count. Keep it in sync with code changes (moods table, Friends table, "Where the data comes from" table, the test list and count).
- The AdGuard guard reads only the process name `AdGuardHome`, checks that the install paths exist, and checks for a UDP socket bound to port 53 in `/proc/net/udp6` (`is_udp_port_bound`; never sends a query). Don't read AdGuard's config (root-only) or call its API (needs the owner's admin login). Don't stop the service to test the sleeping guard, because the owner's desktop uses it for DNS; use `make_vitals(adguard_running=False)` instead.
- `docs/piPetDemo.gif` (Oct 10 2026, after the planet) is rendered, not recorded: a throwaway script called `layout(24, 80, ...)` for each frame with `make_vitals(...)` readings scripted per scene (night with a shooting star found via `build_shooting_star`, petting, dawn, day, every mood, dusk). It drew the cells with PIL in DejaVu Sans Mono 15 px (9×18 px cells, bold font for bold colors, dark-theme RGB per layout color), then built the GIF with `ffmpeg -framerate 4 ... palettegen=stats_mode=full:max_colors=64` and `paletteuse=dither=none` (~110 KB). The script isn't in the repo. Re-render the GIF when the scene changes noticeably.
