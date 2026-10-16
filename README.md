# Pi Pet

A little raspberry that lives in the terminal and reacts to the Raspberry Pi's real vitals.

<p align="center">
  <img src="docs/piPetDemo.gif" alt="Pi Pet's raspberry standing on the grass with its pals (a gold Claude sparkle, a doctor with a clipboard, and a blue AdGuard guard) and a potted plant. Under a moonlit night sky with a ringed planet, a shooting star goes by and the pet says make a wish; it's petted and hops with hearts while the pals cheer; a watering can waters the plant; the help page explains the scene. Then the sky turns from a red dawn and a cloudy day to dusk, the pet sleeps, wakes up groggy, gets busy, surfs, overheats, gets hungry, stuffed and dizzy with red alerts under its stats, celebrates Halloween with bats and a jack-o'-lantern, and ends lonely and offline" width="720">
</p>

<sub>Rendered frame by frame by Pi Pet's own `layout()`, the same code that draws the live view, with scripted readings so that a day, a night, Halloween and every mood fit in 35 seconds.</sub>

## Moods

The pet checks the Pi every 2 seconds and picks the first mood that fits:

| Mood | When | What it does |
|---|---|---|
| Dizzy | The kernel's undervoltage alarm is on (the power supply can't keep up) | `@ @` eyes, stars circling its head |
| Hot | CPU at 70 °C or more | `> <` face, sweat drops |
| Stuffed | SD card 90% full or more | Puffed-up cheeks, the occasional burp |
| Hungry | Under 10% of memory still available | Chomps |
| Groggy | Booted less than 5 minutes ago | One eye open, yawns |
| Lonely | No route to the internet | `. .` eyes, frown, a little rain cloud |
| Busy | 1-minute load of 2.0 or more | Bounces and runs |
| Surfing | Network traffic over 200 KB/s | `^w^` face, sways on waves |
| Sleepy | Load under 0.3 and almost no network traffic | Eyes closed, floating Zs |
| Content | Anything else | Smiles and blinks |

On Wi-Fi, the status line shows signal bars like a phone (`wlan0 |||| 1.0 KB/s`: four bars at -55 dBm or stronger, one fewer every 10 dBm). With one bar or less, the alert line says `weak Wi-Fi` and the pet frets about it now and then.

**What am I looking at?** Press `?` in the live view for a page that explains the scene from the Pi's real readings: why the pet is in its mood (with the numbers), the network and Wi-Fi signal, what's in the sky (the moon's phase, why there are that many clouds), why each pal is visiting, how the plant has grown, and any alerts. Press `?` again to go back.

**It remembers you.** The live view keeps a few memories in `~/.local/state/pi-pet/memories.json` (or under `$XDG_STATE_HOME`): the day you first met, how many visits, how many times you've petted it and watered the plant, how many shooting stars you've seen together, and when you last left. If you've been away a day or more it opens with "missed you!", and the `?` page has a "Together" line. Delete the file and it forgets everything; `--once` never reads or writes it.

**Pet it:** press `p` in the live view and it's happy for 3 seconds: `^‿^` face, a little hop, and red `<3` hearts floating up.

**Water the plant:** press `w` and a watering can `-=|___|` pours onto the plant for 3 seconds ("the plant says thanks!"). It's just for fun: the plant grows with uptime either way.

Groggy comes before lonely and busy on purpose: right after boot, Wi-Fi isn't connected yet and the load is high, and that's just the pet waking up.

It also says something to fit its mood, and changes the line every ~10 seconds. When something's wrong (power low, disk or memory nearly full, offline, AdGuard stopped, DNS port 53 closed, weak Wi-Fi), a red alert line under the stats says what.

## Running it

Python 3 standard library only, so there's nothing to install.

```bash
python3 piPet.py              # live: p to pet it, w to water the plant, ? for help, q to quit
python3 piPet.py --once       # print a snapshot of the whole scene and exit (colored in a terminal; NO_COLOR=1 turns colors off)
python3 piPet.py --mood hot   # force a mood (see --help for the full list, including happy)
python3 piPet.py --iface eth0 # watch one network interface (default: whichever carries internet traffic)
python3 piPet.py --help       # list the options
```

In a window of about 40×20 or bigger it gets a scene: grass under its feet, and a sky that follows the Pi's clock. A yellow `-O-` sun crosses from 06:00 to 18:00 (highest at noon) while clouds drift by (the busier the CPU, the cloudier the day: 1 cloud under load 0.5, 2 under 1.5, 3 above). In the last hour before sunset the sun and clouds turn red and the first stars come out one by one; at dawn it runs the other way. Then the moon crosses among twinkling stars until morning. The moon is drawn in its real phase: `)` waxing crescent, `D` first quarter, `O` around full, `C` last quarter, `(` waning crescent, and no moon (but more stars) on a new moon night. Its shape is real, but its path is simplified: a real moon rises later each night. Now and then (about once every three minutes, at random) a yellow shooting star `--*` streaks across the night sky, and the pet says "make a wish!" A magenta ringed planet `-()-` sits among the stars all night and drifts 3 columns further each day, hiding behind the moon when they meet. The planet is decorative, not a real planet's position. On the grass, bouncing pets hop up instead of down.

## Friends

Pals visit and stand on the grass to the pet's left, when the window has room for them.

| Pal | Visits when | Looks like |
|---|---|---|
| Guard | AdGuard Home is installed: on watch while it runs and serves DNS, alarmed if it runs but nothing is bound to port 53, asleep if it stops | A blue `(o_o)` with a helmet and a `[+]` shield, glancing left and right; a red `(O_O)` with a flashing `!` when alarmed; `(-_-)` with a `z` when asleep |
| Doctor | The [Pi Vitals](https://github.com/boobeh123/Pi-vitals) dashboard is listening on port 3141 | A `(o-o)` in a cross cap and lab coat with a clipboard, checking on the pet and taking notes; `(O-O)` worried when the pet is dizzy, hot, stuffed or hungry |
| Claude | A Claude Code session is open on the Pi (VS Code or terminal) | A gold sparkle `-^‿^-` that twinkles |

When you pet the pet, the guard and the doctor cheer with their arms up, `(^_^)` and `(^-^)`, and Claude's sparkle twinkles twice as fast. An alarmed or sleeping guard stays that way: a DNS problem doesn't stop for a celebration.

Pals line up by how permanent they are: the guard nearest the pet, then the doctor, then Claude, so the ones that come and go don't make the others move. A narrow window drops the farthest first. If AdGuard Home stops, the alert line says `AdGuard stopped`; if it keeps running but stops serving DNS (nothing bound to UDP port 53), it says `DNS port 53 closed`. On a Pi without AdGuard Home there's no guard.

## The plant

A potted plant on the pet's right grows with the Pi's uptime, so a reboot repots it:

```
sprout     seedling    leafy      in flower
< 1 day    1-3 days    3-7 days   7+ days
                        v         (@)
             v         \|/        \|/
  v          |          |          |
\___/      \___/      \___/      \___/
```

Once it flowers, the pet sometimes brags about it.

When Claude arrives the pet says "oh hi Claude!". While pals are around, its calm moods (content, busy, surfing) sometimes chat with them. Urgent moods stick to their own news.

A clock sits in the bottom-right corner of the live view, like `Thu Oct 15  8:42 PM`, when there's room beside the key hint.

In a smaller terminal window it drops the scene first, then the speech bubble, and keeps the pet and a shorter status line. Below 24×11 it asks for a bigger window.

Want to see it get busy for real? In another terminal, `yes > /dev/null` keeps one CPU core at 100% (Ctrl+C to stop); run it two or three times to push the load past 2.

## Holidays

The Pi's calendar brings a few decorations, and on the day the pet's calm moods sometimes say something to fit:

| Holiday | When | What you'll see |
|---|---|---|
| Halloween | Oct 25-31 | A pumpkin hat `((\|))`, a jack-o'-lantern `(^w^)` with a flickering candle beside the plant, and two bats `^v^` flapping across the night sky |
| Christmas | Dec 20-26 | A red Santa hat `.--*`, and lights strung along the grass, the colors chasing each other |
| New Year | Dec 31 and Jan 1 | Fireworks at night: a rocket goes up, bursts into `\\|/ -*- /\|\`, and fades to sparks |
| Pi Day | Mar 14 | Clouds that read `(3.14159)` |
| Pi Pet's birthday | Oct 8 | A party hat `/*\` instead of its leaf, and "I'm 1 today!" (born Oct 8, 2026) |

## Tests

```bash
python3 -m unittest discover -s tests -p "*Test.py"
```

189 standard-library tests, no sensors needed. They check:

- every character the pet draws is exactly one terminal column wide (wide or ambiguous-width characters like `﹏` knock the body out of line in some terminals), and every frame's lines line up
- the screen layout at several window sizes: nothing spills past the edge or draws over anything else, and the status line always shows
- the mood rules and their priority (dizzy > hot > stuffed > hungry > groggy > lonely > busy > surfing > sleepy > content), and that a missing sensor never triggers a mood
- the extras beside the pet (Zs, rain cloud, stars) never cover the pet or its speech bubble, and the alert line
- reading the power alarm, memory, disk and uptime
- petting: red hearts, the happy hop, and that the sensors alone never make it happy
- the scene: only in big windows, never overlapping anything at any hour, sun by day and moon and stars by night, the sun's path, stars that twinkle without moving, and grass right under the feet
- the Claude pal: finding a `claude` process, visiting only while Claude is here and there's room, standing on the grass left of the pet, twinkling, and when the pet chats with it
- the AdGuard guard: no guard without AdGuard Home, glancing around on watch, dozing and an alert when it stops, standing nearest the pet, and getting the only spot in a narrow window
- the DNS check: finding a bound port-53 socket (and not mistaking an outgoing query for a server), the alarmed guard and its alert, and no alarm unless AdGuard runs with the port closed
- the doctor: finding a listening port, visiting only while Pi Vitals runs, checking and taking notes, worrying about urgent moods, and the pals' lineup order
- the plant: its four stages by uptime, standing on the grass right of the pet for every mood and frame, hidden when there's no room or no uptime reading, and bragging only once it flowers
- the moon: the formula against the 2024-04-08 eclipse (new moon) and the 2024-04-23 full moon, its shape through the month, and new moon nights having no moon and more stars
- dawn and dusk: how deep into twilight each hour is, the red sun and glowing clouds, stars coming out at dusk and fading at dawn, and the dusk stars being the first stars of the night
- the planet: one at night and none by day, staying put all night, drifting and wrapping day by day, and hiding behind the moon
- shooting stars: about one every three minutes, crossing and falling over 1.5 seconds, a different schedule each run, night only, and never overlapping the moon or stars
- the clouds: how many for each load, daytime only, drifting right and wrapping around, never colliding with each other or the sun over a long day, and sometimes hiding the sun
- the live view's timing rules, with a fake clock: the opening line, quit keys, a new saying every 10 seconds (but not at the start) or on a mood change, petting and petting again, and greeting Claude
- wishes: a shooting star (from a real night sky) brings "make a wish!" for a few seconds, once per streak, without being cut short by the saying rotation or interrupting petting
- memories: where the file lives, starting fresh from a missing or damaged file (entry by entry), saving without leaving a temporary file, not crashing where it can't write, two Pi Pets adding up instead of overwriting, "missed you!" after a day away, counting each petting once, and the "Together" line
- the help screen: every mood explained with its reading, the sky for each time of day, moon phase names, the pals and plant explained, alerts in red, wrapping inside small windows, `?` and `h` toggling it, and the short key hint in narrow windows
- cheering: the guard and doctor cheer only while the pet is petted, an alarmed or sleeping guard isn't interrupted, and Claude twinkles faster
- Wi-Fi: reading the signal for one interface (and nothing for wired ones), bars like a phone, bars on the status line, the weak-signal alert and fretting, and the help page's Network line
- the clock: its format (midnight, noon, single-digit hours), its corner, and leaving it out where there's no room or in a snapshot
- watering: `w` pours for 3 seconds and counts once per watering, the can's spout over the middle of every plant stage with drops falling between, no can without a plant or room, and the key hint in three widths
- holidays: every date range, the new art being one column wide, the party, Santa and pumpkin hats (and the leaf on other days), the birthday age line, the jack-o'-lantern on the grass past the plant (and its flicker), bats only on Halloween nights, the Christmas lights covering the grass exactly once and chasing colors, fireworks (how often, rocket-bang-sparks, inside the sky, night only), Pi Day clouds, holiday lines only in calm moods, and the help page's "Today" line; the scene and sky sweeps include every holiday
- `--once`: the snapshot shows the whole scene, trims blank space, leaves out the key hint, and only uses colors when printing to a terminal
- the network speed: no spike at startup, counter resets or a missing interface read as 0, and switching networks starts a fresh measurement
- finding the interface that carries internet traffic, and reading its byte counts
- the command-line options, and the status line when a sensor is missing

## Where the data comes from

| Stat | Source |
|---|---|
| CPU temperature | `/sys/class/thermal/thermal_zone0/temp` |
| Fan RPM | `/sys/devices/platform/cooling_fan/hwmon/hwmon*/fan1_input` |
| Load | `os.getloadavg()` |
| Undervoltage | `/sys/class/hwmon/hwmon*/in0_lcrit_alarm` on the hwmon named `rpi_volt` |
| Disk | `os.statvfs('/')`, same math as `df`'s Use% |
| Memory | `MemAvailable` ÷ `MemTotal` in `/proc/meminfo` |
| Uptime (groggy, the plant) | `/proc/uptime` |
| Memories | `~/.local/state/pi-pet/memories.json` (live view only; written by Pi Pet itself) |
| Holidays | the Pi's local date |
| Time of day | the Pi's local clock (`time.localtime()`) |
| Clouds | the 1-minute load, same as busy |
| Planet | the day of the year (decorative) |
| Moon phase | days since a known new moon (2000-01-06 18:14 UTC) modulo the 29.53-day lunar month; good to about a day |
| Claude visiting | a process named `claude` in `/proc/*/comm` |
| Pi Vitals doctor | a socket listening on port 3141 in `/proc/net/tcp` or `tcp6` (read only, never connects) |
| AdGuard guard | installed if `/opt/AdGuardHome/AdGuardHome` or its systemd unit exists; on watch while a process named `AdGuardHome` runs and a UDP socket is bound to port 53 in `/proc/net/udp` or `udp6` (read only, never sends a query) |
| Online | a default route in `/proc/net/route` |
| Wi-Fi signal | `/proc/net/wireless` (signal level in dBm for the watched interface) |
| Network traffic | `/proc/net/dev` (bytes in + out, compared between polls) on the interface the default route uses in `/proc/net/route` (lowest metric wins, falls back to `wlan0`). `--iface` picks one instead. |
