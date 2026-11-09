# ⛽ Gas Tracker — Mini → Volvo V60

Dashboard: https://hlash99.github.io/gas-tracker/

Did switching the daily driver from the 2013 Mini Cooper S to the 2023 Volvo V60 T8 (arrived Aug 24 2026) cut gas use?
Fill-ups come from Monarch; each is converted to estimated gallons using EIA's weekly San Francisco premium
price, which removes the 2026 price spike and tank-size differences.

- `fills.json` — source of truth. Add new fills here (date, station, amount, car).
  Optional per fill: `ppg` (pump price per gallon) or `gallons` from the receipt. When present (or when the
  note says e.g. "at $6.40/gal") the real figure is used instead of the EIA estimate, and the page reports
  how the prices paid compare with EIA's average. An unknown `car` label fails the build instead of being
  silently dropped.
- `scripts/update.py` — fetches EIA prices, computes gallons and the summary, and writes `data.json`.
- `.github/workflows/refresh.yml` — runs weekly and whenever `fills.json` changes.

The same data is on the "Gas: Mini vs V60" tab of the Car Stuff Google Sheet.

## How the numbers are made
- Gallons = amount ÷ EIA San Francisco weekly price for the car's grade (premium; regular for the Civic),
  interpolated between the Monday readings either side of the fill date.
- Checks on the headline: the Mini's last six months before the Volvo as a range (weekend fills then could be
  either car), every similar-length Mini stretch measured the same fill-to-fill way, and all cars together
  (no tagging assumptions, actual dollars).
- Fills estimated above the car's tank size are flagged in the log.
- If EIA can't be reached the build falls back to the prices already in `data.json`, marks `eia_stale`,
  still publishes, and the workflow run shows as failed.
