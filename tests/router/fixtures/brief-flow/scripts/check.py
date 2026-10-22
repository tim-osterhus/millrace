import json
from pathlib import Path

words = Path("out/brief.txt").read_text(encoding="utf-8").split()
outcome = "ACCEPT" if 1 <= len(words) <= 80 else "REVISE"
print(json.dumps({"outcome": outcome, "summary": f"Brief has {len(words)} words"}))
