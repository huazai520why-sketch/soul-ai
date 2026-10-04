import xml.etree.ElementTree as ET
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

path = sys.argv[1] if len(sys.argv) > 1 else r"E:\soul\ui_open.xml"
tree = ET.parse(path)
root = tree.getroot()
rows = []
for n in root.iter("node"):
    txt = (n.get("text") or "").strip()
    rid = n.get("resource-id") or ""
    click = n.get("clickable")
    b = n.get("bounds")
    cls = n.get("class")
    if txt or click == "true":
        rows.append((txt, rid, cls, b, click))

out_path = r"E:\soul\ui_parsed.txt"
buf = []
seen = set()
buf.append("=== 有文字的节点 ===")
for txt, rid, cls, b, click in rows:
    if txt:
        key = (txt, rid, b)
        if key in seen:
            continue
        seen.add(key)
        buf.append(f"{txt!r:32} | {rid:48} | {b}")

buf.append("\n=== 可点击但无文字的节点(前50) ===")
c = 0
for txt, rid, cls, b, click in rows:
    if click == "true" and not txt:
        buf.append(f"{rid:52} | {cls:42} | {b}")
        c += 1
        if c >= 50:
            break

with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(buf) + "\n")
print("written:", out_path, "| nodes:", len(rows))
