import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm_chain import health_check_all

result = health_check_all()
print(json.dumps(result, indent=2, default=str))

print("\n--- Summary ---")
for name, r in result.items():
    status = r.get("status", "?")
    code = r.get("code", "?")
    print(f"  {name:<25} {status:<15} HTTP {code}")

ok = sum(1 for r in result.values() if r.get("status") == "ok")
print(f"\nTotal OK: {ok}/{len(result)}")
