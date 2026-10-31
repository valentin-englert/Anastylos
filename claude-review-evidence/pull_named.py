"""Pull a named session's originals from the Xiaomi: SHA-256 vs device and vs DB, sorted by size."""
import hashlib, os, struct, subprocess, sys
sys.path.insert(0, os.path.dirname(__file__))
import sqlite3
serial, db, name, out = sys.argv[1:5]
pkg = "org.anastylos.capture"
con = sqlite3.connect(db)
rows = con.execute("""select o.observation_id, i.image_id, i.filename, i.sha256, i.captured_at from sessions s
    join observations o on o.session_id = s.session_id join images i on i.observation_id = o.observation_id
    where s.name = ? order by i.captured_at""", (name,)).fetchall()
def jpeg_size(b):
    i = 2
    while i < len(b):
        if b[i] != 0xFF: i += 1; continue
        m = b[i + 1]
        if m in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", b[i + 5:i + 9]); return w, h
        i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
    return None
for oid, iid, fn, sha, at in rows:
    remote = subprocess.run(["adb", "-s", serial, "shell", f"run-as {pkg} find files/evidence/originals/{oid} -name '*.jpg'"],
                            capture_output=True, text=True).stdout.split()
    if not remote: print("MISSING", oid); continue
    data = subprocess.run(["adb", "-s", serial, "exec-out", f"run-as {pkg} cat {remote[0]}"], capture_output=True).stdout
    dev = subprocess.run(["adb", "-s", serial, "shell", f"run-as {pkg} sha256sum {remote[0]}"], capture_output=True, text=True).stdout.split()[0]
    local = hashlib.sha256(data).hexdigest()
    w, h = jpeg_size(data) or (0, 0)
    sub = os.path.join(out, f"{w}x{h}"); os.makedirs(sub, exist_ok=True)
    open(os.path.join(sub, f"{at.replace(':', '').replace('.', '')}-{iid}.jpg"), "wb").write(data)
    ok = "OK" if local == dev == sha else f"MISMATCH dev={dev[:8]} db={sha[:8]}"
    print(ok, local[:16], f"{w}x{h}", at)
