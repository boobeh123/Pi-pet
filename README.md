# Pi Pet

A little raspberry that lives in the terminal and reacts to the Raspberry Pi's real vitals.

<p align="center">
  <img src="docs/piPetDemo.gif" alt="Pi Pet in a terminal cycling through its five moods: content and blinking, sleepy with floating Zs, hot with sweat drops, busy and running, and surfing on waves, with live temperature, load, fan, and Wi-Fi stats underneath" width="641">
</p>

<sub>Recorded from real runs on the Pi (`--mood` forces each mood for the demo).</sub>

## Moods

The pet checks the Pi every 2 seconds and picks the first mood that fits:

| Mood | When | What it does |
|---|---|---|
| Hot | CPU at 70 °C or more | `> <` face, sweat drops |
| Busy | 1-minute load of 2.0 or more | Bounces and runs |
| Surfing | Wi-Fi traffic over 200 KB/s | `^w^` face, sways on waves |
| Sleepy | Load under 0.3 and almost no Wi-Fi traffic | Eyes closed, floating Zs |
| Content | Anything else | Smiles and blinks |

It also says something to fit its mood, and changes the line every ~10 seconds.

## Running it

Python 3 standard library only, so there's nothing to install.

```bash
python3 piPet.py              # live, press q to quit
python3 piPet.py --once       # print one frame and exit
python3 piPet.py --mood hot   # force a mood: content, sleepy, hot, busy, surfing
python3 piPet.py --help       # list the options
```

In a small terminal window it drops the speech bubble first and keeps the pet and a shorter status line. Below 24×11 it asks for a bigger window.

Want to see it get busy for real? In another terminal, `yes > /dev/null` keeps one CPU core at 100% (Ctrl+C to stop); run it two or three times to push the load past 2.

## Tests

```bash
python3 -m unittest discover -s tests -p "*Test.py"
```

30 standard-library tests, no sensors needed. They check:

- every character the pet draws is exactly one terminal column wide (wide or ambiguous-width characters like `﹏` knock the body out of line in some terminals), and every frame's lines line up
- the screen layout at several window sizes: nothing spills past the edge or draws over anything else, and the status line always shows
- the mood rules and their priority (hot > busy > surfing > sleepy > content)
- the Wi-Fi speed: no spike at startup, and counter resets or a missing interface read as 0
- the command-line options, and the status line when a sensor is missing

## Where the data comes from

| Stat | Source |
|---|---|
| CPU temperature | `/sys/class/thermal/thermal_zone0/temp` |
| Fan RPM | `/sys/devices/platform/cooling_fan/hwmon/hwmon*/fan1_input` |
| Load | `os.getloadavg()` |
| Wi-Fi traffic | `/proc/net/dev` (bytes in + out on `wlan0`, compared between polls) |
