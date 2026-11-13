"""Markets (countries), their shops, and a rough landed-cost estimate.

The shop lists are written from general knowledge, not verified against live
sites: use them as a starting point and test each shop in the dashboard.
The cost estimate is orientation only (exchange rates, shipping and duty are
assumptions you can override), not a quote.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, Optional
from urllib.parse import quote, urlparse

D = Decimal
# code -> names, currency, standard VAT % (0 = prices treated as VAT-free), customs area
COUNTRIES = {
    "PL": dict(pl="Polska", en="Poland", cur="PLN", vat=D("23"), area="EU"),
    "DE": dict(pl="Niemcy", en="Germany", cur="EUR", vat=D("19"), area="EU"),
    "NL": dict(pl="Holandia", en="Netherlands", cur="EUR", vat=D("21"), area="EU"),
    "CZ": dict(pl="Czechy", en="Czechia", cur="CZK", vat=D("21"), area="EU"),
    "AT": dict(pl="Austria", en="Austria", cur="EUR", vat=D("20"), area="EU"),
    "US": dict(pl="USA", en="USA", cur="USD", vat=D("0"), area="US"),
    "CN": dict(pl="Chiny", en="China", cur="CNY", vat=D("0"), area="CN"),
    "GB": dict(pl="Wielka Brytania", en="United Kingdom", cur="GBP", vat=D("20"), area="GB"),
    "CH": dict(pl="Szwajcaria", en="Switzerland", cur="CHF", vat=D("8.1"), area="CH"),
}
DESTINATIONS = ("DE", "PL", "NL", "CZ", "AT", "GB", "CH")  # where the parcel can be sent to
# EUR per 1 unit of currency; rough, override with --rates "USD=0.9,PLN=0.23"
DEFAULT_RATES = {"EUR": D("1"), "PLN": D("0.235"), "CZK": D("0.041"), "USD": D("0.86"),
                 "CNY": D("0.12"), "GBP": D("1.15"), "CHF": D("1.07")}

# (name, domain, note)
SHOPS = {
    "DE": [("Cyberport", "cyberport.de", ""), ("notebooksbilliger.de", "notebooksbilliger.de", ""),
           ("Alternate", "alternate.de", ""), ("Otto", "otto.de", ""), ("MediaMarkt", "mediamarkt.de", "może blokować boty"),
           ("Saturn", "saturn.de", "może blokować boty"), ("Mindfactory", "mindfactory.de", ""), ("Decathlon", "decathlon.de", "")],
    "PL": [("x-kom", "x-kom.pl", "może blokować boty"), ("morele.net", "morele.net", ""), ("RTV Euro AGD", "euro.com.pl", ""),
           ("Media Expert", "mediaexpert.pl", ""), ("Komputronik", "komputronik.pl", ""), ("Decathlon", "decathlon.pl", ""),
           ("eobuwie", "eobuwie.com.pl", "")],
    "NL": [("Coolblue", "coolblue.nl", ""), ("bol.com", "bol.com", ""), ("MediaMarkt", "mediamarkt.nl", ""),
           ("Alternate", "alternate.nl", ""), ("Azerty", "azerty.nl", "")],
    "CZ": [("Alza", "alza.cz", ""), ("CZC", "czc.cz", ""), ("Datart", "datart.cz", ""), ("Mironet", "mironet.cz", "")],
    "AT": [("MediaMarkt", "mediamarkt.at", ""), ("Cyberport", "cyberport.at", ""), ("Electronic4You", "electronic4you.at", ""),
           ("E-Tec", "e-tec.at", "")],
    "US": [("B&H Photo", "bhphotovideo.com", "wysyła za granicę"), ("Newegg", "newegg.com", "część produktów"),
           ("Micro Center", "microcenter.com", "ceny zależne od sklepu"), ("Best Buy", "bestbuy.com", "zwykle tylko USA, mocne zabezpieczenia"),
           ("Walmart", "walmart.com", "zwykle tylko USA, mocne zabezpieczenia"), ("Target", "target.com", "zwykle tylko USA")],
    "CN": [("Banggood", "banggood.com", ""), ("Geekbuying", "geekbuying.com", ""),
           ("AliExpress", "aliexpress.com", "cena zależna od konta, skrypty")],
    "GB": [("Currys", "currys.co.uk", ""), ("Argos", "argos.co.uk", ""), ("Scan", "scan.co.uk", ""),
           ("Overclockers UK", "overclockers.co.uk", "")],
    "CH": [("Digitec", "digitec.ch", ""), ("Galaxus", "galaxus.ch", ""), ("Interdiscount", "interdiscount.ch", ""),
           ("Brack", "brack.ch", "")],
}
EXTRA_HOSTS: dict = {}  # host -> country, for shops not in SHOPS (and for tests)
_TLD = {".de": "DE", ".pl": "PL", ".nl": "NL", ".cz": "CZ", ".at": "AT", ".ch": "CH", ".uk": "GB", ".cn": "CN"}


def search_url(domain: str, query: str) -> str:
    """A web search restricted to the shop, so the user can find the product page to paste."""
    return "https://www.google.com/search?q=" + quote(f"{query} site:{domain}")


def country_of_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host in EXTRA_HOSTS:
        return EXTRA_HOSTS[host]
    for code, shops in SHOPS.items():
        if any(host == d or host.endswith("." + d) for _, d, _ in shops):
            return code
    for tld, code in _TLD.items():
        if host.endswith(tld):
            return code
    return ""


def parse_rates(text: str) -> dict:
    rates = dict(DEFAULT_RATES)
    for part in filter(None, (p.strip() for p in (text or "").split(","))):
        cur, _, val = part.partition("=")
        try:
            rates[cur.strip().upper()] = D(val.strip())
        except Exception:
            raise ValueError(f"bad rate: {part}")
    return rates


def default_shipping_eur(src: str, dest: str) -> Decimal:
    if src == dest:
        return D("5")
    return D("9") if COUNTRIES[src]["area"] == COUNTRIES[dest]["area"] else D("25")


def make_landed(src: str, dest: str, rates: dict, shipping_eur: Optional[Decimal] = None,
                duty_pct: Optional[Decimal] = None) -> Callable[[Decimal], Decimal]:
    """price in the shop's currency -> estimated total in the destination currency.

    same country: price + shipping
    inside the EU: price without the shop's VAT, plus destination VAT (OSS), plus shipping
    outside: goods + shipping, plus duty, plus import VAT of the destination country
    """
    s, d = COUNTRIES[src], COUNTRIES[dest]
    ship = shipping_eur if shipping_eur is not None else default_shipping_eur(src, dest)
    duty = (duty_pct or D(0)) / 100

    def landed(price: Decimal) -> Decimal:
        eur = price * rates[s["cur"]]
        if src == dest:
            total = eur + ship
        elif s["area"] == d["area"]:
            total = eur / (1 + s["vat"] / 100) * (1 + d["vat"] / 100) + ship
        else:
            customs = eur / (1 + s["vat"] / 100) + ship
            total = (customs + customs * duty) * (1 + d["vat"] / 100)
        return (total / rates[d["cur"]]).quantize(D("0.01"), rounding=ROUND_HALF_UP)

    return landed
