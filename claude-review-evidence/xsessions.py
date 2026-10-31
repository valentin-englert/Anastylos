"""Session -> observation/image map from a pulled capture.db (read-only copy)."""
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
if len(sys.argv) > 2 and sys.argv[2] == "schema":
    for t in tables:
        print(t, [c[1] for c in con.execute(f"pragma table_info({t})")])
    sys.exit()
q = """select s.name, s.session_id, count(i.image_id), min(i.captured_at), max(i.captured_at)
       from sessions s left join observations o on o.session_id = s.session_id
       left join images i on i.observation_id = o.observation_id
       group by s.session_id order by s.created_at"""
if len(sys.argv) > 2 and sys.argv[2] == "list":
    for row in con.execute(q):
        print(row)
    sys.exit()
name = sys.argv[2]
for oid, iid, fn, sha in con.execute(
        """select o.observation_id, i.image_id, i.filename, i.sha256 from sessions s
           join observations o on o.session_id = s.session_id
           join images i on i.observation_id = o.observation_id
           where s.name = ? order by i.captured_at""", (name,)):
    print(oid, iid, fn, sha)
