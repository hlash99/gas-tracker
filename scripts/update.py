"""Rebuild data.json: refresh EIA weekly San Francisco gas prices, convert each fill to
estimated gallons, and compute the Mini-vs-Volvo summary. Fills come from fills.json."""
import json, re, datetime as dt, bisect, urllib.request, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERIES = {"premium": "EMM_EPMP_PTE_Y05SF_DPG", "regular": "EMM_EPMR_PTE_Y05SF_DPG"}
FUEL_CARS = {"Mini", "Volvo", "Porsche", "Civic", "Assumed Porsche", "Weekend: Mini or Porsche"}
START = "2024-01-01"
VOLVO_ARRIVED = "2026-08-24"


def fetch_series(sid):
    url = f"https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s={sid}&f=W"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    out = {}
    for row in re.findall(r"<tr>(.*?)</tr>", html, re.S):
        m = re.search(r"(\d{4})-(\w{3})", row)
        if not m:
            continue
        yr, mon = int(m.group(1)), dt.datetime.strptime(m.group(2), "%b").month
        for md, v in re.findall(r"class='B5'>(\d\d/\d\d)&nbsp;</td>\s*<td class='B3'>([\d.]+)", row):
            mm, dd = map(int, md.split("/"))
            y = yr - 1 if (mon == 1 and mm == 12) else yr
            d = dt.date(y, mm, dd).isoformat()
            if d >= START:
                out[d] = float(v)
    if len(out) < 50:
        raise SystemExit(f"EIA parse failed for {sid}: {len(out)} rows")
    return dict(sorted(out.items()))


def gal_per_30(fills, car, a, b):
    days = (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days + 1
    g = sum(f["gal"] for f in fills if f["car"] == car and a <= f["date"] <= b and f["gal"])
    return g / days * 30


def main():
    src = json.loads((ROOT / "fills.json").read_text())
    eia = {k: fetch_series(s) for k, s in SERIES.items()}
    keys = list(eia["premium"])

    def price(d):
        return eia["premium"][keys[max(0, bisect.bisect_right(keys, d) - 1)]]

    fills = []
    for f in sorted(src["fills"], key=lambda x: x["date"]):
        p = price(f["date"])
        fuel = f["car"] in FUEL_CARS
        fills.append({**f, "day": dt.date.fromisoformat(f["date"]).strftime("%a"),
                      "price": p, "gal": round(f["amount"] / p, 2) if fuel else None})

    mini12 = gal_per_30(fills, "Mini", "2025-02-01", "2026-01-31")
    mini_season = gal_per_30(fills, "Mini", "2025-09-01", "2025-11-30")
    vf = [f for f in fills if f["car"] == "Volvo"]
    volvo = None
    if len(vf) >= 2:
        span = (dt.date.fromisoformat(vf[-1]["date"]) - dt.date.fromisoformat(vf[0]["date"])).days
        volvo = sum(f["gal"] for f in vf[1:]) / span * 30
    today = eia["premium"][keys[-1]]

    # Porsche (bought Feb 21 2026, filled only on weekends). Since the Volvo arrived there is
    # no Mini, so Porsche fills are tagged directly: gallons bought / days elapsed. Before
    # that, weekend fills can't be split between Mini and Porsche, so only an upper bound.
    as_of = max(dt.date.today().isoformat(), fills[-1]["date"])
    p_since = sum(f["gal"] for f in fills if f["car"] == "Porsche" and f["date"] >= VOLVO_ARRIVED)
    p_days = (dt.date.fromisoformat(as_of) - dt.date.fromisoformat(VOLVO_ARRIVED)).days + 1
    pre = [f for f in fills if "2026-02-24" <= f["date"] < VOLVO_ARRIVED]
    pre_days = (dt.date.fromisoformat(VOLVO_ARRIVED) - dt.date(2026, 2, 24)).days
    porsche = {
        "as_of": as_of,
        "since_volvo_gal": round(p_since, 2), "since_volvo_days": p_days,
        "since_volvo_fills": sum(1 for f in fills if f["car"] == "Porsche" and f["date"] >= VOLVO_ARRIVED),
        "since_volvo_gal30": round(p_since / p_days * 30, 2),
        "pre_high_gal30": round(sum(f["gal"] for f in pre if f["car"] == "Weekend: Mini or Porsche") / pre_days * 30, 2),
        "purchase_weekend_gal": round(sum(f["gal"] for f in fills if f["car"] == "Assumed Porsche"), 2),
    }
    summary = {
        "mini12_gal30": round(mini12, 2), "mini_season_gal30": round(mini_season, 2),
        "volvo_gal30": round(volvo, 2) if volvo else None,
        "volvo_fills": len(vf), "volvo_window": [vf[0]["date"], vf[-1]["date"]] if vf else None,
        "today_premium": today, "today_premium_week": keys[-1],
        "change_vs_mini12": round(volvo / mini12 - 1, 4) if volvo else None,
        "change_vs_season": round(volvo / mini_season - 1, 4) if volvo else None,
        "saving30_today": round((mini12 - volvo) * today, 2) if volvo else None,
        "porsche": porsche,
    }
    data = {"volvo_arrived": VOLVO_ARRIVED, "porsche_bought": "2026-02-21",
            "summary": summary, "fills": fills, "eia": eia}
    # keep the old timestamp when nothing changed, so a quiet week makes no commit
    out = ROOT / "data.json"
    old = json.loads(out.read_text()) if out.exists() else {}
    stamp = old.pop("updated", None)
    if old != data or not stamp:
        stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out.write_text(json.dumps({"updated": stamp, **data}, separators=(",", ":")))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
