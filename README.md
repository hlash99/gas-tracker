# ⛽ Gas Tracker — Mini → Volvo V60

Dashboard: https://hlash99.github.io/gas-tracker/

Did switching the daily driver from the 2013 Mini Cooper S to the 2023 Volvo V60 T8 (arrived Aug 24 2026) cut gas use?
Fill-ups come from Monarch; each is converted to estimated gallons using EIA's weekly San Francisco premium
price, which removes the 2026 price spike and tank-size differences.

- `fills.json` — source of truth. Add new fills here (date, station, amount, car).
- `scripts/update.py` — fetches EIA prices, computes gallons and the summary, and writes `data.json`.
- `.github/workflows/refresh.yml` — runs weekly and whenever `fills.json` changes.

The same data is on the "Gas: Mini vs V60" tab of the Car Stuff Google Sheet.
