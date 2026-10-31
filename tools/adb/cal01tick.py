"""CAL-01 capture metronome: soft tick whenever a new original lands.

Usage:
  cal01tick.py test                      # play 3 soft ticks (sound check)
  cal01tick.py watch --target 22 --stop-x 602 --stop-y 1109
      # poll device originals; tick on each new file; at +target files
      # play the stop double-blip and tap Stop via adb.

Tick sound: 740 Hz sine, 90 ms, exponential decay, moderate level.
Stop sound: two 440 Hz blips. Playback via winsound (system volume).
"""
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
import wave

ADB = r"C:\Users\HP\Android\android-sdk\platform-tools\adb.exe"
DEV = "192.168.0.42:43947"


def tone(path, freq, ms, level=0.25, decay=28.0):
    rate = 22050
    n = int(rate * ms / 1000)
    frames = bytearray()
    for i in range(n):
        t = i / rate
        env = math.exp(-t * 1000 / decay / 10.0)
        v = int(32767 * level * env * math.sin(2 * math.pi * freq * t))
        frames += struct.pack("<h", v)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def play(path, block=True):
    import winsound
    winsound.PlaySound(path, winsound.SND_FILENAME | (
        0 if block else winsound.SND_ASYNC))


def count_originals():
    p = subprocess.run(
        [ADB, "-s", DEV, "shell",
         "run-as org.anastylos.capture ls files/evidence/originals | wc -l"],
        capture_output=True, text=True, timeout=30)
    return int(p.stdout.strip())


def main(argv):
    tmp = tempfile.gettempdir()
    tick = os.path.join(tmp, "cal01_tick.wav")
    stop = os.path.join(tmp, "cal01_stop.wav")
    tone(tick, 740, 90)
    tone(stop, 440, 140, level=0.3)
    if argv[1] == "test":
        for _ in range(3):
            play(tick)
            time.sleep(0.7)
        return 0
    target = int(argv[argv.index("--target") + 1])
    sx = argv[argv.index("--stop-x") + 1]
    sy = argv[argv.index("--stop-y") + 1]
    base = count_originals()
    print(f"base={base} target=+{target}", flush=True)
    deadline = time.time() + 8 * 60
    while time.time() < deadline:
        time.sleep(2.0)
        try:
            now = count_originals()
        except Exception as exc:
            print(f"poll failed: {exc}", flush=True)
            continue
        new = now - base
        if new > 0 and new != getattr(main, "_last", 0):
            print(f"new={new}/{target}", flush=True)
            play(tick, block=False)
            main._last = new
        if new >= target:
            break
    play(stop)
    time.sleep(0.4)
    play(stop)
    subprocess.run([ADB, "-s", DEV, "shell",
                    f"input tap {sx} {sy}"], timeout=30)
    print("stopped", flush=True)
    return 0


main(sys.argv)
