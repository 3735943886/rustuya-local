"""Run the chain over every core fixture; return per-platform golden/hit/produced counts."""
import collections
import json

import conftest  # noqa: F401  (sets sys.path)
import fixtures  # tuya2ildevice/tests/golden
from il_ha.core.descriptor import parse_descriptor  # il-ha core (see conftest)
from il_ha.core.plan import plan_entities
from tuya2ildevice import descriptor_of

from conftest import GOLDEN


def run(**driver_kw) -> dict:
    driver_kw.setdefault("allow_hazardous", True)      # parity with core, which writes garage doors; production default is off (il.md S-1)
    gold = json.loads((GOLDEN / "golden.json").read_text())
    total, hit, produced = collections.Counter(), collections.Counter(), collections.Counter()
    errors, short = [], collections.defaultdict(list)
    codes = fixtures.all_codes()
    for code in codes:
        try:
            specs = plan_entities(parse_descriptor(descriptor_of(fixtures.load(code), **driver_kw)))
        except Exception as e:  # a crash is a finding, not a skip
            errors.append((code, repr(e)[:120]))
            specs = []
        got = collections.Counter(s.platform for s in specs)
        produced.update(got)
        for platform, rows in gold.get(code, {}).items():
            total[platform] += len(rows)
            hit[platform] += min(len(rows), got.get(platform, 0))
            if got.get(platform, 0) != len(rows):
                short[platform].append((code, len(rows), got.get(platform, 0)))
    return {"fixtures": len(codes), "total": dict(total), "hit": dict(hit), "produced": dict(produced),
            "errors": errors, "short": {k: v for k, v in short.items()}}


if __name__ == "__main__":
    import sys
    r = run(**{k: v == "1" for k, v in (a.split("=") for a in sys.argv[1:])})
    for p in sorted(r["total"]):
        print(f"{p:22} {r['hit'].get(p, 0):4}/{r['total'][p]:4}  produced {r['produced'].get(p, 0)}")
    print("TOTAL", sum(r["hit"].values()), "/", sum(r["total"].values()), "produced", sum(r["produced"].values()),
          "errors", len(r["errors"]))
