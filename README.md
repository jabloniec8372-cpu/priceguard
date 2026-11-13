# PriceGuard

Watches a competitor's prices and stock for a web shop owner, and sends a
Telegram message when it matters. When it is *not sure*, it says so instead of
guessing.

![Second read: two Telegram alerts, and three rows that need a human look](docs/demo.svg)

## The problem

A shop owner checks the prices of a few dozen competitor products by hand every
day. It costs an hour a day and still misses price drops.

## What it does

1. **Product list.** The owner keeps a sheet (here a CSV; the same columns work
   in Google Sheets): own SKU, own price, currency, the variant that must match,
   and the competitor's product URL.
2. **Reads each page** every few hours: price, currency, stock, shipping if the
   page shows it. It uses the structured data shops publish for Google
   (schema.org JSON-LD, then microdata). It never guesses a price from loose page text.
3. **Compares only like with like.** A page with a different variant
   ("1 battery" instead of "2 batteries") or another currency is *not* compared.
   It is shown as `TO CHECK`.
4. **Keeps the history** in SQLite and writes a status sheet (`status.csv`) plus
   `price_history.csv`. A failed read never overwrites the last known price; the
   row keeps it and is marked `TO CHECK: <reason>`.
5. **Alerts on Telegram** when a price drops by a chosen percentage or a product
   is back in stock: *"Wkrętarka X, zestaw z 2 akumulatorami. Konkurent Shop A obniżył cenę z 219 € do 199 €. Twoja cena to 209 €."* (pl / de / en)
6. **Polite by design:** checks `robots.txt` (a URL it disallows is skipped and
   flagged, and an unreadable robots.txt means *skip*), pauses between requests
   to one host, retries 429/5xx a bounded number of times, identifies itself.
7. **Alerts survive outages.** They go into an outbox and are marked sent only
   after Telegram accepted them, so an outage re-sends later instead of losing
   them. In the rare crash between sending and marking you may get one
   duplicate; for price alerts that is the right trade.

## Run it (offline, nothing leaves your machine)

Python 3.9+, standard library only. On Windows also run `pip install tzdata`.

```
python3 demo.py                         # fake shop on localhost, watched twice
python3 -m unittest discover -s tests   # 25 tests
```

Against real pages (every few hours via cron or Windows Task Scheduler):

```
export TELEGRAM_BOT_TOKEN=...
python3 -m priceguard run --products my_products.csv --telegram-chat 123456 --threshold 5 --lang pl
```

## Layout

| Path | Purpose |
| --- | --- |
| `priceguard/parse.py` | Price, currency, stock, shipping from JSON-LD / microdata |
| `priceguard/fetch.py` | robots.txt, per-host pause, bounded retries (HTTP injectable) |
| `priceguard/monitor.py` | One pass: compare like with like, record, queue alerts, deliver |
| `priceguard/store.py` | SQLite history and alert outbox |
| `priceguard/sheet.py` | Status sheet and history export |
| `priceguard/telegram.py` | Telegram Bot API sender |
| `demo.py` | Local fake shop whose prices change between two reads |

## Honest limits (v1)

- Developed and tested against a **local fake shop**. Real shops differ; pages
  without structured data are reported as `TO CHECK`, not guessed. Adding a
  per-shop rule for such a page is the typical customisation job.
- The Telegram sender follows the Bot API but has not been run against the live
  service from here (tests use a fake sender).
- The sheet is a CSV; writing to Google Sheets needs the Sheets API and the
  client's credentials.
- Pages that need JavaScript to show the price are not rendered.
- **Check each shop's terms of use before monitoring it.** PriceGuard only reads
  public pages, slowly, and respects `robots.txt`, but that does not replace the
  shop's terms.
