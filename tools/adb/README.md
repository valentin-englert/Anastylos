# ADB-Spickzettel (SM-A536B, WLAN + USB)

Alle CAMERA-01/02-Gerätetests liefen mit diesen Befehlen. Getestet über
Wireless Debugging (`192.168.0.42:43947`) und USB (`R5CTA06SFRB`).
PowerShell-Syntax; `adb` muss im PATH sein.

## 0. Gerät ansprechen

```powershell
adb devices -l
$P = '192.168.0.42:43947'   # WLAN-Serial; ggf. anpassen (siehe adb devices)
```

## 1. Tappen, Wischen, Tasten

```powershell
adb -s $P shell "input tap 540 1770"              # Tap auf x/y
adb -s $P shell "input swipe 540 1800 540 700 400" # Scrollen (Dauer ms)
adb -s $P shell "input swipe 120 1658 1000 1658 500" # Slider-Drag
adb -s $P shell "input tap 540 1985"              # Slider-Sprung per Tap
adb -s $P shell "input keyevent 4"                # Zurück (Tastatur schließen!)
```

Hinweise: Bildschirm muss an/entsperrt sein (Timeout max. 10 min).
Versehentliche Taps können die Tastatur öffnen → `keyevent 4`.
Auf dem Guided-Screen (30 fps Analyse) kommen injizierte Gesten nur
sporadisch an — Chips gingen 2/11-mal, Slider-Drags gar nicht; dort ggf.
Finger benutzen. Session-Screen: 100 % zuverlässig.

## 2. UI-Koordinaten finden

```powershell
adb -s $P shell "uiautomator dump /sdcard/ui.xml"
adb -s $P pull /sdcard/ui.xml C:\tmp\cam02ui.xml
py -3.12 tools/adb/cam02uidump.py            # alle Texte + Center-Koordinaten
py -3.12 tools/adb/cam02map.py Zoom Torch    # filtern nach Stichwort
py -3.12 tools/adb/cam02sliders.py           # Slider-Bounds (SeekBar)
py -3.12 tools/adb/cam02chip.py              # Still-Chip Auswahlstatus
py -3.12 tools/adb/cam02switch.py            # Toggle-Schalter (checked?)
```

Anderer Dump-Pfad: `$env:CAM02UI = 'C:\pfad\ui.xml'`.
Achtung: `ERROR: could not get idle state` (Live-Preview) → Dump ist dann
STALE (alte Datei). In dem Fall per Screenshot navigieren.

## 3. Screenshots

```powershell
adb -s $P shell screencap -p /sdcard/shot.png
adb -s $P pull /sdcard/shot.png cam01-evidence/shot.png
```

## 4. Logcat

```powershell
adb -s $P logcat -c                                    # leeren (VOR Aktion!)
adb -s $P logcat -d | Select-String -Pattern 'AnastylosCam' | Select-Object -Last 5
adb -s $P logcat -d -t 200                              # letzte 200 Zeilen
adb -s $P logcat -g                                     # Buffer-Größen
```

Achtung: Die Guided-Analyse spamt OpenCV-Init-Logs und rotiert den
5-MiB-Buffer in Minuten — immer direkt nach der Aktion lesen.

## 5. App steuern

```powershell
adb -s $P shell am force-stop org.anastylos.capture
adb -s $P shell "monkey -p org.anastylos.capture -c android.intent.category.LAUNCHER 1"
adb -s $P shell "pm path org.anastylos.capture"
adb -s $P shell "dumpsys package org.anastylos.capture" | Select-String 'versionName|lastUpdateTime'
```

## 6. Dateien lesen (run-as, kein Root nötig)

```powershell
# Auflisten / suchen / hashen (Achtung: Glob * expandiert NICHT unter run-as → find nehmen)
adb -s $P shell "run-as org.anastylos.capture ls -la /data/data/org.anastylos.capture/files/evidence"
adb -s $P shell "run-as org.anastylos.capture find /data/data/org.anastylos.capture/files/evidence/originals -name '*.jpg' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -3"
adb -s $P shell "run-as org.anastylos.capture sha256sum /data/data/org.anastylos.capture/files/evidence/originals/<session>/<id>.jpg"
```

Binär-sicher pullen (`>` in PowerShell korrumpiert Binärdaten; `cp` nach
/sdcard ist unter run-as verboten) — Muster als Python-Einzeiler-Datei,
nicht inline (Quoting!):

```python
import subprocess
r = subprocess.run(['adb', '-s', $P, 'exec-out',
    'run-as org.anastylos.capture cat /data/data/.../datei.jpg'], capture_output=True)
open('lokal.jpg', 'wb').write(r.stdout)
```

## 7. APK-Hash verifizieren

```powershell
(Get-FileHash capture-companion\android\app\build\outputs\apk\debug\app-debug.apk -Algorithm SHA256).Hash
$p = (adb -s $P shell "pm path org.anastylos.capture") -replace 'package:',''
adb -s $P shell "sha256sum $p"   # muss identisch sein
```

## 8. Auswertung (diese Tools)

```powershell
py -3.12 tools/adb/cam02exif.py bild.jpg        # EXIF: ISO/Belichtung/Blende/Maße (Stdlib only)
py -3.12 tools/adb/cam02luma.py a.png b.png     # mittlere Helligkeit oben 1100 px (PNG, Stdlib only)
```

## 9. Kamera-Hardware einsehen

```powershell
adb -s $P shell "dumpsys media.camera"   # Characteristics aller Kameras (sehr lang)
adb -s $P shell wm size                  # 1080x2400
```
