import json
from pathlib import Path

words = Path("request.txt").read_text(encoding="utf-8").split()
if not words:
    print(json.dumps({"outcome": "BLOCKED", "summary": "Request is empty"}))
else:
    Path("out").mkdir(exist_ok=True)
    Path("out/brief.txt").write_text(" ".join(words[:80]) + "\n", encoding="utf-8")
    print(json.dumps({"outcome": "READY", "refs": ["out/brief.txt"]}))
