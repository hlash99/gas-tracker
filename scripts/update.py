"""Rebuild data.json: refresh EIA weekly San Francisco gas prices, convert each fill to
estimated gallons, and compute the Mini-vs-Volvo summary. Fills come from fills.json.

Gallons for a fill = amount / price, where price is, in order of preference:
  1. the pump price, if the fill has "ppg" (or "gallons"), or its note says "at $6.40/gal";
  2. EIA's San Francisco weekly average for the car's grade (premium, regular for the
     Civic), interpolated between the Monday readings either side of the fill date.
"""
import json, re, sys, time, datetime as dt, bisect, urllib.request, pathlib
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERIES = {"premium": "EMM_EPMP_PTE_Y05SF_DPG", "regular": "EMM_EPMR_PTE_Y05SF_DPG"}
FUEL_CARS = {"Mini", "Volvo", "Porsche", "Civic", "Assumed Porsche", "Weekend: Mini or Porsche"}
OTHER_LABELS = {"Excluded", "EV charging"}
GRADE = {"Civic": "regular"}                     # everything else takes premium
# Usable tank size in gallons. An estimate above this can't be one fill of that car at the
# average price: the pump price was higher, or the charge included something else.
TANK = {"Mini": 13.2, "Civic": 13.2, "Volvo": 15.9, "Porsche": 17.7, "Assumed Porsche": 17.7}
START = "2024-01-01"
PORSCHE_BOUGHT = "2026-02-21"
VOLVO_ARRIVED = "2026-08-24"
MINI_PORSCHE_ERA = ("2026-02-24", "2026-08-23")  # Mini weekdays, Mini-or-Porsche weekends
D = dt.date.fromisoformat


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
        raise ValueError(f"EIA parse failed for {sid}: {len(out)} rows")
    return dict(sorted(out.items()))


def load_prices(cached):
    """Fetch both EIA series (3 tries). Weeks already in data.json are kept if a fetch
    comes back without them. If EIA can't be reached, fall back to the cached prices so a
    new fill still gets built; returns (prices, failed)."""
    eia, failed = {}, False
    for grade, sid in SERIES.items():
        old = (cached or {}).get(grade) or {}
        got, err = None, None
        for attempt in range(3):
            try:
                got = fetch_series(sid)
                break
            except Exception as e:      # network error or a page layout we can't parse
                err = e
                time.sleep(5 * (attempt + 1))
        if got is not None and old and max(got) < max(old):
            got, err = None, f"latest week {max(got)} is older than cached {max(old)}"
        if got is None:
            if len(old) < 50:
                raise SystemExit(f"EIA fetch failed for {grade} and no cached prices: {err}")
            print(f"::warning::EIA fetch failed for {grade} ({err}); using cached prices")
            failed, got = True, {}
        eia[grade] = dict(sorted({**old, **got}.items()))
    return eia, failed


def pump_price(f):
    """Price actually paid per gallon, when the fill records it. None otherwise."""
    if f.get("gallons"):
        return f["amount"] / f["gallons"]
    if f.get("ppg"):
        return float(f["ppg"])
    m = re.search(r"\bat \$(\d{1,2}\.\d{2,3})\s*/\s*gal", f.get("note") or "")
    return float(m.group(1)) if m else None


def validate(src):
    bad = []
    for i, f in enumerate(src["fills"]):
        try:
            D(f["date"])
            ok = f["car"] in FUEL_CARS | OTHER_LABELS and float(f["amount"]) > 0 and "station" in f
        except Exception:
            ok = False
        if not ok:
            bad.append(f"  fills[{i}]: {json.dumps(f)}")
    if bad:
        raise SystemExit("fills.json has entries with a bad date, amount or car label "
                         f"(allowed: {sorted(FUEL_CARS | OTHER_LABELS)}):\n" + "\n".join(bad))


def gal_per_30(fills, car, a, b):
    days = (D(b) - D(a)).days + 1
    g = sum(f["gal"] for f in fills if f["car"] == car and a <= f["date"] <= b and f["gal"])
    return g / days * 30


def all_cars(fills, a, b):
    """Every fuel fill in [a, b], whatever the car: needs no tagging assumptions."""
    days = (D(b) - D(a)).days + 1
    fs = [f for f in fills if f["gal"] and a <= f["date"] <= b]
    return {"window": [a, b], "days": days, "fills": len(fs),
            "gal30": round(sum(f["gal"] for f in fs) / days * 30, 2),
            "usd30": round(sum(f["amount"] for f in fs) / days * 30, 2)}


def year_before(d):
    d = D(d)
    try:
        return d.replace(year=d.year - 1).isoformat()
    except ValueError:                   # Feb 29
        return d.replace(year=d.year - 1, day=28).isoformat()


def main():
    src = json.loads((ROOT / "fills.json").read_text())
    validate(src)
    out = ROOT / "data.json"
    old = json.loads(out.read_text()) if out.exists() else {}
    eia, eia_failed = load_prices(old.get("eia"))
    keys = {g: list(s) for g, s in eia.items()}

    def price(d, grade="premium"):
        """EIA price on day d: straight line between the Monday readings either side.
        After the newest reading (or before the oldest) the nearest one is used."""
        k, s = keys[grade], eia[grade]
        i = bisect.bisect_right(k, d) - 1
        if i < 0:
            return s[k[0]]
        if i >= len(k) - 1:
            return s[k[-1]]
        t = (D(d) - D(k[i])).days / 7
        return round(s[k[i]] + (s[k[i + 1]] - s[k[i]]) * t, 3)

    fills = []
    for f in sorted(src["fills"], key=lambda x: x["date"]):
        fuel = f["car"] in FUEL_CARS
        grade = GRADE.get(f["car"], "premium")
        p = price(f["date"], grade)
        row = {**f, "day": D(f["date"]).strftime("%a"), "price": p,
               "gal": round(f["amount"] / p, 2) if fuel else None}
        if fuel:
            row["grade"] = grade
            paid = pump_price(f)
            tank = TANK.get(f["car"])
            if paid and 2 < paid < 15:
                row.update(paid_ppg=round(paid, 3), gal=round(f["amount"] / paid, 2), gal_src="pump")
            elif tank and row["gal"] > tank:
                row["over_tank"] = tank
        fills.append(row)

    mini12 = gal_per_30(fills, "Mini", "2025-02-01", "2026-01-31")
    mini_season = gal_per_30(fills, "Mini", "2025-09-01", "2025-11-30")
    vf = [f for f in fills if f["car"] == "Volvo"]
    volvo = span = None
    if len(vf) >= 2:
        span = (D(vf[-1]["date"]) - D(vf[0]["date"])).days
        volvo = sum(f["gal"] for f in vf[1:]) / span * 30
    today = eia["premium"][keys["premium"][-1]]

    # Porsche (bought Feb 21 2026, filled only on weekends). Since the Volvo arrived there is
    # no Mini, so Porsche fills are tagged directly: gallons bought / days elapsed. Before
    # that, weekend fills can't be split between Mini and Porsche, so only an upper bound.
    as_of = max(dt.datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat(), fills[-1]["date"])
    p_since = sum(f["gal"] for f in fills if f["car"] == "Porsche" and f["date"] >= VOLVO_ARRIVED)
    p_days = (D(as_of) - D(VOLVO_ARRIVED)).days + 1
    pre = [f for f in fills if MINI_PORSCHE_ERA[0] <= f["date"] < VOLVO_ARRIVED]
    pre_days = (D(VOLVO_ARRIVED) - D(MINI_PORSCHE_ERA[0])).days
    porsche = {
        "as_of": as_of,
        "since_volvo_gal": round(p_since, 2), "since_volvo_days": p_days,
        "since_volvo_fills": sum(1 for f in fills if f["car"] == "Porsche" and f["date"] >= VOLVO_ARRIVED),
        "since_volvo_gal30": round(p_since / p_days * 30, 2),
        "pre_high_gal30": round(sum(f["gal"] for f in pre if f["car"] == "Weekend: Mini or Porsche") / pre_days * 30, 2),
        "purchase_weekend_gal": round(sum(f["gal"] for f in fills if f["car"] == "Assumed Porsche"), 2),
    }

    # --- checks on the headline comparison -------------------------------------------
    # 1. The Mini in the six months right before the Volvo. Weekday fills are the Mini;
    #    weekend fills were the Mini or the Porsche, so the Mini's rate is a range.
    lo = sum(f["gal"] for f in pre if f["car"] == "Mini") / pre_days * 30
    hi = lo + porsche["pre_high_gal30"]
    mini_recent = {"window": list(MINI_PORSCHE_ERA), "lo_gal30": round(lo, 2), "hi_gal30": round(hi, 2),
                   "volvo_change_lo": round(volvo / lo - 1, 4) if volvo and lo else None,
                   "volvo_change_hi": round(volvo / hi - 1, 4) if volvo and hi else None}

    # 2. How unusual is the Volvo's rate for a stretch that short? Measure the Mini the same
    #    way (fill to fill) over every stretch of similar length (+-15%) before the Porsche.
    rank = None
    if volvo:
        mini = [f for f in fills if f["car"] == "Mini" and f["date"] < PORSCHE_BOUGHT]
        rates = []
        for i in range(len(mini)):
            g = 0.0
            for j in range(i + 1, len(mini)):
                g += mini[j]["gal"]
                s = (D(mini[j]["date"]) - D(mini[i]["date"])).days
                if s > span * 1.15:
                    break
                if s >= span * 0.85:
                    rates.append(g / s * 30)
        rates.sort()
        if len(rates) >= 20:
            rank = {"span_days": span, "windows": len(rates),
                    "below_volvo": sum(1 for r in rates if r < volvo),
                    "min": round(rates[0], 2), "p5": round(rates[int(0.05 * len(rates))], 2),
                    "median": round(rates[len(rates) // 2], 2), "max": round(rates[-1], 2)}

    # 3. Mini fills estimated above its tank size (likely a car wash on the same charge, or
    #    a station priced above the average). Capping them shows how much that could matter.
    capped = [{**f, "gal": min(f["gal"], TANK["Mini"])} if f["car"] == "Mini" else f for f in fills]
    mini12_capped = gal_per_30(capped, "Mini", "2025-02-01", "2026-01-31")

    # 4. Fills with a known pump price, against EIA's average for that day and grade.
    pumps = [f for f in fills if f.get("gal_src") == "pump"]
    pump_check = None
    if pumps:
        pump_check = {"fills": len(pumps),
                      "avg_paid": round(sum(f["paid_ppg"] for f in pumps) / len(pumps), 3),
                      "avg_eia": round(sum(f["price"] for f in pumps) / len(pumps), 3),
                      "avg_diff_pct": round(sum(f["paid_ppg"] / f["price"] - 1 for f in pumps) / len(pumps), 4)}

    # All cars together, actual dollars and gallons: no tagging assumptions at all.
    household = {
        "before_porsche": all_cars(fills, "2025-02-01", "2026-01-31"),
        "mini_porsche": all_cars(fills, *MINI_PORSCHE_ERA),
        "since_volvo": all_cars(fills, VOLVO_ARRIVED, as_of),
        "year_ago": all_cars(fills, year_before(VOLVO_ARRIVED), year_before(as_of)),
    }
    ya = household["year_ago"]["gal30"]
    household["change_vs_year_ago"] = round(household["since_volvo"]["gal30"] / ya - 1, 4) if ya else None

    summary = {
        "mini12_gal30": round(mini12, 2), "mini_season_gal30": round(mini_season, 2),
        "volvo_gal30": round(volvo, 2) if volvo else None,
        "volvo_fills": len(vf), "volvo_window": [vf[0]["date"], vf[-1]["date"]] if vf else None,
        "today_premium": today, "today_premium_week": keys["premium"][-1],
        "change_vs_mini12": round(volvo / mini12 - 1, 4) if volvo else None,
        "change_vs_season": round(volvo / mini_season - 1, 4) if volvo else None,
        "saving30_today": round((mini12 - volvo) * today, 2) if volvo else None,
        "porsche": porsche,
        "mini_recent": mini_recent, "volvo_vs_mini_stretches": rank,
        "mini12_gal30_tank_capped": round(mini12_capped, 2),
        "over_tank_fills": sum(1 for f in fills if f.get("over_tank")),
        "pump_check": pump_check, "household": household,
    }
    data = {"volvo_arrived": VOLVO_ARRIVED, "porsche_bought": PORSCHE_BOUGHT,
            "summary": summary, "fills": fills, "eia": eia}
    if eia_failed:
        data["eia_stale"] = True
    # keep the old timestamp when nothing changed, so a quiet week makes no commit
    stamp = old.pop("updated", None)
    if old != data or not stamp:
        stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out.write_text(json.dumps({"updated": stamp, **data}, separators=(",", ":")))
    print(json.dumps(summary, indent=1))
    if eia_failed:
        sys.exit("EIA prices could not be refreshed; data.json was rebuilt from cached prices.")


if __name__ == "__main__":
    main()
