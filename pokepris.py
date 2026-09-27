"""Find the cheapest Pokémon booster packs, booster boxes and Elite Trainer Boxes
in online shops that sell to Norway.

Usage examples:
  python pokepris.py                         # top 10 cheapest in stock per category + HTML report
  python pokepris.py --search "ascended"     # only products whose name contains "ascended"
  python pokepris.py --lang en               # English only (en / jp / cn / kr / all)
  python pokepris.py --type etb --top 25     # only ETBs, show 25
  python pokepris.py --all                   # also show sold-out products
  python pokepris.py --norway-only           # skip shops outside Norway
  python pokepris.py --check                 # show how many products each shop category gave

The HTML report (pokepris_rapport.html) opens in your browser and has search and filters.
Prices from shops abroad are converted to NOK with Norges Bank's exchange rates.
"""
import argparse
import concurrent.futures as cf
import html
import http.cookiejar
import json
import re
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

REPORT = Path(__file__).with_name("pokepris_rapport.html")
UA = "Mozilla/5.0 (pokepris price checker; personal use)"

TYPE_NAMES = {"pack": "Booster pack", "box": "Booster box", "etb": "Elite Trainer Box"}
LANG_NAMES = {"en": "English", "jp": "Japanese", "cn": "Chinese", "kr": "Korean", "other": "Other language"}
COUNTRY_NAMES = {"NO": "Norway", "SE": "Sweden", "NL": "Netherlands"}

# Used only if Norges Bank can't be reached (NOK per 1 unit).
FALLBACK_RATES = {"NOK": 1.0, "EUR": 10.84, "SEK": 0.9601, "DKK": 1.4501, "USD": 9.3}

# Each shop: platform, base URL, country, note, and categories as
# (collection handle or category path, product type, language or None = detect from title).
#   shopify     - Shopify shops (read from /collections/<handle>/products.json)
#   nb_classic  - 24Nettbutikk shops with the classic theme (schema.org product markup)
#   nb_new      - 24Nettbutikk shops with the newer theme (product cards)
SHOPS = {
    "Cardcenter": dict(kind="shopify", base="https://cardcenter.no", country="NO", cats=[
        ("pokemon-booster-pakker", "pack", None),
        ("japanske-pokemonpakker", "pack", "jp"),
        ("booster-boxer", "box", None),
        ("japansk-booster-box", "box", "jp"),
        ("elite-trainer-boxer", "etb", None),
    ]),
    "PokéNordic": dict(kind="shopify", base="https://www.pokenordic.no", country="NO", cats=[
        ("booster-pakker", "pack", None),
        ("japanske-booster-pakker", "pack", "jp"),
        ("booster-bokser", "box", None),
        ("booster-box", "box", None),
        ("japanske-booster-bokser", "box", "jp"),
        ("elite-trainer-box", "etb", None),
    ]),
    "Pokestore": dict(kind="shopify", base="https://pokestore.no", country="NO", cats=[
        ("pokemon-booster-pakker", "pack", None),
        ("japanske-pokemon-booster-pakker", "pack", "jp"),
        ("kinesiske-pokemon-booster-pakker", "pack", "cn"),
        ("engelske-pokemon-booster-bokser", "box", "en"),
        ("japanske-pokemon-booster-bokser", "box", "jp"),
        ("kinesiske-pokemon-booster-bokser", "box", "cn"),
        ("pokemon-elite-trainer-box", "etb", None),
    ]),
    "Pokelageret": dict(kind="shopify", base="https://pokelageret.no", country="NO", cats=[
        ("booster-pakker", "pack", None),
        ("booster-box-en", "box", "en"),
        ("booster-box-jp", "box", "jp"),
        ("pokemon-elite-trainer-box", "etb", None),
    ]),
    "EpiCards": dict(kind="shopify", base="https://epicards.no", country="NO", cats=[
        ("booster-pakker", "pack", None),
        ("booster-display", "box", None),
        ("pokemon-elite-trainer-box", "etb", None),
    ]),
    "Card Kings": dict(kind="nb_classic", base="https://www.cardkings.no", country="NO", cats=[
        ("/butikk/pokemon/booster-pakker", "pack", None),
        ("/butikk/pokemon/elite-trainer-bokser", "etb", None),
        ("/butikk/japansk-pokemon/booster-pakker", "pack", "jp"),
        ("/butikk/japansk-pokemon/booster-bokser", "box", "jp"),
    ]),
    "Boosterpakker.no": dict(kind="nb_classic", base="https://boosterpakker.no", country="NO", cats=[
        ("/butikk/boosterpakker/engelske-pakker", "pack", "en"),
        ("/butikk/boosterpakker/japanske-pakker", "pack", "jp"),
        ("/butikk/booster-bokser/engelske-booster-bokser", "box", "en"),
        ("/butikk/booster-bokser/japanske-booster-bokser", "box", "jp"),
        ("/butikk/booster-bokser/kinesiske-booster-bokser", "box", "cn"),
        ("/butikk/booster-bokser/koreanske-booster-bokser", "box", "kr"),
    ]),
    "PokeWorld": dict(kind="nb_new", base="https://pokeworld.no", country="NO", cats=[
        ("/category/booster-pakker-049TmW31Pr", "pack", None),
        ("/category/booster-bokser-HMEVrLO0Ul", "box", None),
        ("/category/elite-trainer-bokser-gZuHfBrNkd", "etb", None),
    ]),
    "LCGCards": dict(kind="nb_new", base="https://www.lcgcards.no", country="NO", cats=[
        ("/category/japansk-booster-box-lzeiKfR8B8", "box", "jp"),
        ("/category/kinesisk-booster-box-M0rnHmjX1X", "box", "cn"),
    ]),
    "Aquitaz": dict(kind="shopify", base="https://aquitaz.se", country="SE",
                    note="Ships from Sweden. Shipping, and possibly Norwegian VAT/customs, come on top.", cats=[
        ("pokemon-booster-packs", "pack", None),
        ("pokemon-booster-box", "box", None),
        ("pokemon-booster-display", "box", None),
        ("pokemon-elite-trainer-boxes-etbs", "etb", None),
    ]),
    "Bescards": dict(kind="shopify", base="https://www.bescards.com", country="NL",
                     note="Ships from the Netherlands. Customs/VAT are paid at checkout (DDP); shipping comes on top.", cats=[
        ("pokemon-booster-packs", "pack", None),
        ("pokemon-booster-boxes", "box", None),
        ("pokemon-elite-trainer-box", "etb", None),
    ]),
}

OTHER_GAMES = ("one piece", "magic the gathering", "mtg", "yu-gi-oh", "yugioh", "lorcana", "hololive",
               "weiss", "dragon ball", "digimon", "flesh and blood", "star wars", "union arena", "basketball",
               "riftbound", "gundam", "naruto", "topps", "final fantasy")


# ---------- helpers ----------
def opener_for(country):
    """URL opener with cookies; for shops abroad it first selects Norway as the shipping market."""
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = [("User-Agent", UA)]
    return op


def fetch(op, url):
    with op.open(url, timeout=25) as r:
        return r.read().decode("utf-8", "ignore")


def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_kr(s):
    s = re.sub(r"[^\d,.]", "", s)
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s) if s else None


def detect_lang(text):
    """Language from words like 'Japansk'/'Korean' or codes like (JP), [CN], (CH), SV6-JP."""
    t = text.lower()
    if re.search(r"\[(?:frans|fr|duits|de|it|es)\]|\((?:fr|de|it|es)\)|french|francais|german|deutsch|italian|spanish", t):
        return "other"
    if re.search(r"japansk|japanese|japan\b|\bjpn?\b", t):
        return "jp"
    if re.search(r"kinesisk|chinese|simplified|\bcn\b|[(\[]ch[)\]]|\bs-chn?\b|\bt-chn?\b", t):
        return "cn"
    if re.search(r"koreansk|korean|\bkr\b|\bkor\b", t):
        return "kr"
    return "en"


def classify(title, default):
    t = f" {title.lower()}"
    if "elite trainer" in t or " etb" in t:
        return "etb"
    if default == "etb":
        return None  # gift boxes, Build & Battle etc. listed alongside ETBs
    if default == "box" and ("bundle" in t or " case" in t):
        return None  # bundles and multi-box cases aren't a single booster box
    is_pack = any(w in t for w in ("boosterpakke", "booster pakke", "booster pack", "boosterpack"))
    if default == "box" and is_pack and not any(w in t for w in (" box", " boks", "display")):
        return "pack"  # single pack filed under a box collection
    if default == "pack" and ("booster box" in t or "display" in t or "bundle" in t):
        return None
    return default


def variant_kind(variant_title, default):
    """Some shops sell a single pack as a variant of the booster box product."""
    t = (variant_title or "").lower()
    if "pack" in t or "pakke" in t:
        return "pack"
    if "box" in t or "boks" in t or "display" in t:
        return "box"
    return default


GROUP_DROP = set("""pokemon tcg the trading card game booster boosters boosterpakke boosterpakker pakke pakker pack
packs box boks bokser boxes display elite trainer etb engelsk engelske english eng en japansk japanske japanese jp jpn
kinesisk kinesiske chinese cn ch koreansk koreanske korean kr kor sett set utgave edition maks per pers stk kunde kunder
ny new preorder forhandsbestilling forhandssalg og and of i""".split())
SERIES = ("mega evolution", "scarlet violet", "scarlet and violet", "sword shield", "sword and shield", "sun moon",
          "sun and moon", "mega ")


def group_key(title, kind, lang):
    """Key that is the same for one product across shops, e.g. 'etb|en|chaos rising'."""
    t = title.lower().replace("é", "e").replace("&", " ").replace("’", "").replace("'", "")
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)  # (9 Pack), [JP], (ENG) ...
    t = re.sub(r"\bmaks?\.?\s*\d+\s*(?:per|pr)?\.?\s*(?:pers|person|kunde)?\.?", " ", t)  # "Maks 1 per pers."
    t = re.sub(r"\b\d+\s*(?:x\s*)?(?:pack|packs|pakker|kort|cards|stk)\b", " ", t)  # 36 pakker, 10 kort
    t = re.sub(r"\b(?:me|sv|swsh|sm|s|m|cbb|csv|cs)\d+[a-z]?(?:-[a-z]+)?\b", " ", t)  # set codes: ME05, SV6-JP, CSV10C
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = f" {re.sub(r'\s+', ' ', t)} "
    full = t
    for s in SERIES:
        t = t.replace(f" {s}", " ")

    def norm(text):  # word order and plural -s don't matter
        return sorted({w[:-1] if len(w) > 3 and w.endswith("s") else w for w in text.split() if w not in GROUP_DROP})
    words = norm(t) or norm(full)  # e.g. the plain "Mega Evolution" ETB is named only by its series
    return f"{kind}|{lang}|{' '.join(words)}"


MIN_PRICE = {"pack": 5, "box": 100, "etb": 100}  # below this it's a placeholder price, not a real one


def is_other_game(text):
    t = text.lower()
    return any(g in t for g in OTHER_GAMES)


def get_rates():
    url = "https://data.norges-bank.no/api/data/EXR/B.EUR+SEK+DKK+USD.NOK.SP?format=sdmx-json&lastNObservations=1"
    try:
        op = opener_for("NO")
        j = json.loads(fetch(op, url))
        st = j["data"]["structure"]
        curs = [v["id"] for v in st["dimensions"]["series"][1]["values"]]
        attrs = st["attributes"]["series"]
        mult_pos = next(i for i, a in enumerate(attrs) if a["id"] == "UNIT_MULT")
        rates = {"NOK": 1.0}
        for key, s in j["data"]["dataSets"][0]["series"].items():
            cur = curs[int(key.split(":")[1])]
            mult = int(attrs[mult_pos]["values"][s["attributes"][mult_pos]]["id"])
            rates[cur] = float(list(s["observations"].values())[-1][0]) / 10 ** mult
        return rates, "Norges Bank"
    except Exception:
        return dict(FALLBACK_RATES), "fallback (Norges Bank unavailable)"


# ---------- shop readers ----------
def read_shopify(shop, cfg, op, out):
    base = cfg["base"]
    currency = "NOK"
    if cfg["country"] != "NO":
        page = fetch(op, f"{base}/?country=NO")  # switch the shop to its Norway market (sets a cookie)
        m = re.search(r'Shopify\.currency\s*=\s*\{"active":"(\w+)"', page)
        currency = m.group(1) if m else "EUR"
    counts = {}
    for handle, ptype, lang in cfg["cats"]:
        n = 0
        for page_no in range(1, 11):
            products = json.loads(fetch(op, f"{base}/collections/{handle}/products.json?limit=250&page={page_no}")).get("products", [])
            for p in products:
                title = p["title"].strip()
                meta = f"{title} {p.get('vendor', '')} {p.get('product_type', '')}"
                if is_other_game(meta):
                    continue
                kind = classify(title, ptype)
                if kind is None:
                    continue
                groups = {}
                for v in p.get("variants") or []:
                    groups.setdefault(variant_kind(v.get("title"), kind), []).append(v)
                for vkind, variants in groups.items():
                    avail = [v for v in variants if v.get("available")]
                    v = min(avail or variants, key=lambda v: float(v["price"]))
                    suffix = "" if len(groups) == 1 or v.get("title") in (None, "Default Title") else f" ({v['title']})"
                    out.append(dict(
                        key=(p["handle"], vkind), title=title + suffix, type=vkind, lang=lang or detect_lang(meta),
                        price=float(v["price"]), currency=currency,
                        was=float(v["compare_at_price"]) if v.get("compare_at_price") else None,
                        in_stock=bool(avail),
                        url=f"{base}/products/{p['handle']}" + (f"?variant={v['id']}" if len(groups) > 1 else "")))
                    n += 1
            if len(products) < 250:
                break
            time.sleep(0.3)
        counts[handle] = n
        time.sleep(0.3)
    return counts


def next_page_url(page, base, page_no):
    m = re.search(r'<link[^>]+rel="next"[^>]+href="([^"]+)"', page) or \
        re.search(rf'href="([^"]*[?&](?:page|side)={page_no + 1}(?:&[^"]*)?)"', page)
    if not m:
        return None
    u = html.unescape(m.group(1))
    return u if u.startswith("http") else base + u


def read_nettbutikk(shop, cfg, op, out):
    base = cfg["base"]
    counts = {}
    for path, ptype, lang in cfg["cats"]:
        url, n, seen = base + path, 0, set()
        for page_no in range(1, 16):
            page = fetch(op, url)
            items = parse_nb_classic(page) if cfg["kind"] == "nb_classic" else parse_nb_new(page)
            new = [it for it in items if it["url"] not in seen]
            for it in new:
                seen.add(it["url"])
                if is_other_game(it["title"]):
                    continue
                kind = classify(it["title"], ptype)
                if kind is None:
                    continue
                out.append(dict(key=(it["url"], kind), title=it["title"], type=kind,
                                lang=lang or detect_lang(it["title"]), price=it["price"], currency="NOK",
                                was=None, in_stock=it["in_stock"], url=it["url"]))
                n += 1
            url = next_page_url(page, base, page_no)
            if not url or not new:
                break
            time.sleep(0.3)
        counts[path] = n
        time.sleep(0.3)
    return counts


def parse_nb_classic(page):
    items = []
    for a in re.findall(r"<article\b(.*?)</article>", page, re.S):
        if "schema.org/Product" not in a:
            continue
        url = re.search(r'itemprop="url" content="([^"]+)"', a)
        name = re.search(r'itemprop="name"[^>]*>(.*?)</', a, re.S)
        price = re.search(r'itemprop="price" content="([\d.]+)"', a)
        av = re.search(r'itemprop="availability" href="[^"]*/(\w+)"', a)
        if url and name and price:
            items.append(dict(title=strip_tags(name.group(1)), url=url.group(1), price=float(price.group(1)),
                              in_stock=bool(av) and av.group(1) in ("InStock", "LimitedAvailability", "OnlineOnly")))
    return items


def parse_nb_new(page):
    items = []
    for c in page.split("product-card group")[1:]:
        name = next((html.unescape(n) for n in re.findall(r'aria-label="([^"]+)"', c[:4000])
                     if "favoritt" not in n.lower()), None)
        url = re.search(r'href="(https?://[^"]+/product/[^"?]+)', c)
        price = re.search(r'font-bold whitespace-nowrap"[^>]*>\s*([^<]+?)\s*<', c)
        if not (name and url and price):
            continue
        value = parse_kr(price.group(1))
        if value is None:
            continue
        text = strip_tags(c[:20000]).lower()
        in_stock = not any(w in text for w in ("ikke på lager", "utsolgt", "tomt på lager"))
        items.append(dict(title=name.strip(), url=url.group(1), price=value, in_stock=in_stock))
    return items


def scrape_shop(shop):
    cfg = SHOPS[shop]
    op = opener_for(cfg["country"])
    raw, error = [], None
    try:
        reader = read_shopify if cfg["kind"] == "shopify" else read_nettbutikk
        counts = reader(shop, cfg, op, raw)
    except Exception as e:  # network errors, changed websites, etc.
        counts, error = {}, f"{shop}: {e}"
    rows = {}
    for r in raw:  # the same product can be listed in several categories
        key = r.pop("key")
        if r["price"] <= 0:  # "price on request" / not yet priced
            continue
        r["group"] = group_key(r["title"], r["type"], r["lang"])
        prev = rows.get(key)
        if prev:
            if prev["lang"] == "en" and r["lang"] != "en":
                prev["lang"] = r["lang"]
            continue
        r["shop"], r["country"] = shop, cfg["country"]
        r["note"] = cfg.get("note", "")
        rows[key] = r
    return list(rows.values()), counts, error


def scrape_all(shops, check=False):
    rates, rate_source = get_rates()
    rows = []
    with cf.ThreadPoolExecutor(max_workers=len(shops)) as ex:
        for shop, (r, counts, err) in zip(shops, ex.map(scrape_shop, shops)):
            if err:
                print(f"  Warning: could not read {err}")
            if check:
                print(f"  {shop}: {len(r)} products  " + ", ".join(f"{k}={v}" for k, v in counts.items()))
            rows += r
    for r in rows:
        r["orig_price"] = r["price"]
        rate = rates.get(r["currency"])
        if rate is None:
            rate = FALLBACK_RATES.get(r["currency"], 1.0)
        r["price"] = round(r["price"] * rate, 2)
        if r["was"]:
            r["was"] = round(r["was"] * rate, 2)
    rows = [r for r in rows if r["price"] >= MIN_PRICE[r["type"]]]
    groups = {}
    for r in rows:
        groups.setdefault(r["group"], []).append(r)
    for offers in groups.values():
        name = display_name(offers)
        for r in offers:
            r["gname"] = name
    return rows, rates, rate_source


def display_name(offers):
    """A readable name for a product group: the shortest clean title, preferring Norwegian shops."""
    def clean(t):
        t = re.sub(r"^\s*maks?\.?\s*\d+\s*(?:per|pr)?\.?\s*(?:pers|person|kunde)?\.?\s*", "", t, flags=re.I)
        t = re.sub(r"\s*\((?:[^)]*(?:max|maks|b-vare|willekeurig)[^)]*)\)", "", t, flags=re.I)
        return t.strip(" -")
    local = [clean(r["title"]) for r in offers if r["country"] == "NO"]
    return min(local or [clean(r["title"]) for r in offers], key=len)


def group_offers(rows, kind):
    groups = {}
    for r in rows:
        if r["type"] == kind:
            groups.setdefault(r["group"], []).append(r)
    for g in groups.values():
        g.sort(key=lambda r: r["price"])
    return sorted(groups.values(), key=lambda g: g[0]["price"])


# ---------- best cards to pull (TCGdex, Cardmarket prices) ----------
CARD_CACHE = Path(__file__).with_name("pokepris_cards_cache.json")
TCGDEX = "https://api.tcgdex.net/v2/"
CACHE_HOURS = 24
BIG_CARD = re.compile(r"\b(?:ex|EX|GX|V|VMAX|VSTAR|Mega|M)\b|(?:ex|EX|GX|VMAX|VSTAR)$")

# Japanese sets: TCGdex id -> the English names shops use for them.
JA_SETS = {
    "M6a": ["30th celebration"], "M6": ["storm emeralda", "storm emerald"], "M5": ["abyss eye"],
    "M4": ["ninja spinner"], "M3": ["nihil zero", "munikis zero", "munikiss zero"],
    "M2a": ["mega dream ex", "mega dream"], "M2": ["inferno x"], "M1S": ["mega symphonia"], "M1L": ["mega brave"],
    "SV11W": ["white flare"], "SV11B": ["black bolt"],
    "SV10": ["glory of team rocket", "team rocket glory", "team rocket s glory", "rocket gang glory"],
    "SV9a": ["heat wave arena", "hot wind arena"], "SV9": ["battle partner"],
    "SV8a": ["terastal festival ex", "terastal festival", "terastal fest ex", "terastal fest"],
    "SV8": ["super electric breaker", "supercharged breaker"], "SV7a": ["paradise dragona"],
    "SV7": ["stellar miracle"], "SV6a": ["night wanderer"], "SV6": ["mask of change", "transformation mask"],
    "SV5a": ["crimson haze"], "SV5M": ["cyber judge", "cyber jugde"], "SV5K": ["wild force"],
    "SV4a": ["shiny treasure ex", "shiny treasure"], "SV4M": ["future flash"], "SV4K": ["ancient roar"],
    "SV3a": ["raging surf"], "SV3": ["ruler of the black flame", "black flame ruler", "ruler of black flame"],
    "SV2a": ["pokemon card 151", "151"], "SV2D": ["clay burst"], "SV2P": ["snow hazard"],
    "SV1a": ["triplet beat"], "SV1S": ["scarlet ex"], "SV1V": ["violet ex"],
    "S12a": ["vstar universe"], "S12": ["paradigm trigger"], "S11a": ["incandescent arcana"], "S11": ["lost abyss"],
    "S10b": ["pokemon go"], "S10a": ["dark phantasma"], "S10P": ["space juggler"], "S10D": ["time gazer"],
    "S9a": ["battle region"], "S9": ["star birth"], "S8b": ["vmax climax"],
    "S8a": ["25th anniversary collection"], "S8": ["fusion art"], "S7R": ["blue sky stream"],
    "S7D": ["skyscraping perfection", "skyscraping perfect"], "S6a": ["eevee heroe"],
    "S6K": ["jet black spirit", "jet black geist"], "S6H": ["silver lance"],
    "S5a": ["peerless fighter", "matchless fighter"], "S5R": ["rapid strike master"],
    "S5I": ["single strike master"], "S4a": ["shiny star v"], "S4": ["amazing volt tackle"],
    "S3a": ["legendary heartbeat"], "S2a": ["explosive walker"], "SM12a": ["tag all star"],
    "SM11b": ["dream league"], "SM8b": ["gx ultra shiny"], "SM7b": ["fairy rise"],
    "SM12": ["alter genesis"], "SM11": ["miracle twin"], "SM11a": ["remix bout"], "SM10": ["double blaze"],
    "SM10a": ["gg end"], "SM10b": ["sky legend"], "SM9": ["tag bolt"], "SM9a": ["night unison"],
    "SM9b": ["full metal wall"], "SM8": ["explosive impact"], "SM8a": ["dark order"],
    "SM7": ["sky splitting charisma", "charisma of the wrecked sky"], "SM7a": ["thunderclap spark"],
    "SM6a": ["dragon storm"], "SM6b": ["champion road"], "SM6": ["forbidden light"],
    "SM5M": ["ultra moon"], "SM5S": ["ultra sun"], "SM5+": ["ultra force"], "SMP2": ["detective pikachu"],
    "S1a": ["vmax rising"], "S1H": ["shield expansion"], "S1W": ["sword expansion"],
    "S2": ["rebellion crash", "rebel clash"], "S3": ["infinity zone", "mugen zone"],
}
# Other translations shops use for the same Japanese sets.
for _sid, _names in {
    "M3": ["nullifying zero"], "SV7": ["stella miracle"], "SV6a": ["night wander"],
    "SV10": ["glory of the rocket squad", "rocket squad"], "SV9a": ["hot air arena", "heat wave"],
    "S6K": ["jet black"], "S7D": ["towering perfect", "towering perfection"], "S10a": ["dark fantasma"],
    "S4": ["astonishing volt tackle", "astonishing voltecker"], "S2a": ["explosion walker"],
    "S4a": ["shiny star"], "S8a": ["25th anniversary"],
}.items():
    JA_SETS[_sid] += _names
# English aliases for sets the shops name differently from TCGdex.
EN_ALIASES = {"25th anniversary celebration": "cel25", "30th anniversary celebration": "30th"}
BASE_SETS = {"sword shield": "swsh1", "scarlet violet": "sv01", "sun moon": "sm1", "xy": "xy1"}


def tcgdex(op, path):
    for attempt in range(3):
        try:
            return json.loads(fetch(op, TCGDEX + path))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))


def load_cache():
    try:
        return json.loads(CARD_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def fresh(entry):
    return entry and time.time() - entry.get("ts", 0) < CACHE_HOURS * 3600


def norm_name(s):
    s = s.lower().replace("é", "e").replace("&", " ").replace("’", "").replace("'", "")
    words = re.sub(r"[^a-z0-9 ]", " ", s).split()
    return " " + " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words) + " "


JA_CODES = {k.lower(): k for k in JA_SETS}


def match_set(title, sets, lang="en"):
    """Find which card set a product belongs to, e.g. 'Pokémon Chaos Rising ETB' -> me04,
    'Ninja Spinner Booster Box' (Japanese) -> ja:M4."""
    if lang == "jp":  # a set code in the title is the surest sign: (SV11B), M2a ...
        for code in re.findall(r"(?i)\b((?:sv|sm|s|m)\d+[a-z]?)\b", title):
            if code.lower() in JA_CODES:
                return "ja:" + JA_CODES[code.lower()]
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", title)  # (10 Kort), [JP] ...
    t = re.sub(r"(?i)\bmaks?\.?\s*\d+\s*(?:per|pr)?\.?\s*(?:pers|person|kunde)?\.?", " ", t)
    t = re.sub(r"(?i)\b\d+\s*[-x]?\s*(?:pack|packs|pakker|kort|cards|stk)\b", " ", t)  # 36 pakker, 3-Pack
    full = norm_name(t)
    numbers = set(re.findall(r" (\d+(?:st|nd|rd|th)?) ", full.replace(" ", "  ")))  # 151, 30th ...
    if lang == "en":
        for alias, set_id in EN_ALIASES.items():
            if norm_name(alias) in full:
                return set_id
        if " base " in full:  # "Sword & Shield Base Set" is that era's first set, not the 1999 Base Set
            for series, set_id in BASE_SETS.items():
                if norm_name(series) in full:
                    return set_id
    short = full
    for s in SERIES:
        if " " in s.strip():
            short = short.replace(norm_name(s), " ")
    for text in (short, full):  # first without "Mega Evolution:"/"Scarlet & Violet:" prefixes
        for name, set_id in sets:
            # a number in the title (151, 30th) must also be in the set name
            if name in text and numbers <= set(name.split()):
                return set_id
    return None


def card_price_eur(card):
    cm = ((card.get("pricing") or {}).get("cardmarket")) or {}
    trend = max(cm.get("trend") or 0, cm.get("trend-holo") or 0)
    return trend or max(cm.get("avg30") or 0, cm.get("avg30-holo") or 0)


def species_names(op, cache):
    """Pokédex number -> English Pokémon name (from PokéAPI), kept in the cache for 30 days."""
    entry = cache.get("species")
    if entry and time.time() - entry.get("ts", 0) < 30 * 86400:
        return {int(k): v for k, v in entry["names"].items()}
    try:
        lst = json.loads(fetch(op, "https://pokeapi.co/api/v2/pokemon-species?limit=2000"))["results"]
        names = {int(x["url"].rstrip("/").split("/")[-1]): x["name"].replace("-", " ").title() for x in lst}
        cache["species"] = dict(ts=time.time(), names=names)
        return names
    except Exception:
        return {}


def english_card_name(card, species):
    """'メガゲッコウガex' (dexId 658) -> 'Mega Greninja ex'. Trainer cards keep their Japanese name."""
    name, dex = card.get("name") or "", card.get("dexId") or []
    if not dex or not all(d in species for d in dex):
        return name
    en = " & ".join(species[d] for d in dex)
    if name.startswith("メガ") or name.startswith("M"):
        en = "Mega " + en
    suffix = re.search(r"(ex|EX|GX|VMAX|VSTAR|BREAK|V)$", name)
    return f"{en} {suffix.group(1)}" if suffix else en


def top_cards(op, key, species=None):
    """5 most valuable cards of a set. key is an English set id ('me04') or 'ja:<id>' for Japanese sets."""
    lang, set_id = key.split(":", 1) if key.startswith("ja:") else ("en", key)
    s = tcgdex(op, f"{lang}/sets/{set_id}")
    official = (s.get("cardCount") or {}).get("official") or 0
    cands = []
    for c in s.get("cards", []):
        num = c.get("localId", "")
        secret = not num.isdigit() or (official and int(num) > official)
        if secret or BIG_CARD.search(c.get("name", "")):
            cands.append(c)
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        details = list(ex.map(lambda c: tcgdex(op, f"{lang}/cards/{c['id']}"), cands))
    cards = [dict(name=d.get("name") if lang == "en" else english_card_name(d, species or {}),
                  number=f"{d.get('localId')}/{official}" if official else d.get("localId"),
                  rarity="" if d.get("rarity") in (None, "None") else d["rarity"], eur=round(card_price_eur(d), 2),
                  image=(d.get("image") + "/low.webp") if d.get("image") else "")
             for d in details]
    cards = sorted((c for c in cards if c["eur"] > 0), key=lambda c: c["eur"], reverse=True)[:5]
    name = s.get("name", set_id) if lang == "en" else JA_SETS[set_id][0].title() + " (Japanese)"
    return dict(name=name, cards=cards)


def add_best_pulls(rows, rates, refresh=False):
    """Attach the 5 most valuable cards of each English/Japanese product's set. Returns {set_key: {...}}."""
    cache = load_cache()
    op = opener_for("NO")
    try:
        if refresh or not fresh(cache.get("sets")):
            lst = tcgdex(op, "en/sets")
            cache["sets"] = dict(ts=time.time(), list=[[x["id"], x["name"]] for x in lst])
        order = cache["sets"]["list"]
        sets = [(norm_name(name), sid) for i, (sid, name) in enumerate(order)
                if "promo" not in name.lower() and len(name) > 2]
        pos = {sid: i for i, (sid, _) in enumerate(order)}
        sets.sort(key=lambda x: (-len(x[0]), -pos[x[1]]))  # longest name first, then newest
    except Exception as e:
        print(f"  Warning: could not get card sets ({e}); skipping best pulls")
        return {}
    ja_sets = sorted(((norm_name(alias), "ja:" + sid) for sid, aliases in JA_SETS.items() for alias in aliases),
                     key=lambda x: -len(x[0]))

    for r in rows:
        if r["lang"] == "en":
            r["set"] = match_set(r["gname"], sets)
        elif r["lang"] == "jp":
            r["set"] = match_set(r["title"], ja_sets, "jp") or match_set(r["gname"], ja_sets, "jp")
        else:
            r["set"] = None  # no card prices exist for Chinese/Korean-only sets
    needed = sorted({r["set"] for r in rows if r["set"]})
    top = cache.get("top", {})
    todo = [sid for sid in needed if refresh or not fresh(top.get(sid)) or top[sid].get("v") != 2]
    species = species_names(op, cache) if any(s.startswith("ja:") for s in todo) else {}
    if todo:
        print(f"Fetching best cards for {len(todo)} sets (cached for {CACHE_HOURS} h afterwards) ...")
    for sid in todo:
        try:
            cache.setdefault("top", {})[sid] = dict(ts=time.time(), v=2, **top_cards(op, sid, species))
        except Exception as e:
            print(f"  Warning: could not get cards for set {sid}: {e}")
    try:
        CARD_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    eur = rates.get("EUR", FALLBACK_RATES["EUR"])
    pulls = {}
    for sid in needed:
        entry = cache.get("top", {}).get(sid)
        if entry and entry.get("cards"):
            pulls[sid] = dict(name=entry["name"],
                              cards=[dict(c, nok=round(c["eur"] * eur)) for c in entry["cards"]])
    for r in rows:
        if r["set"] not in pulls:
            r["set"] = None
    return pulls


# ---------- output ----------
def kr(x):
    return f"{x:,.0f} kr".replace(",", " ")


def print_table(rows, top, kinds=("pack", "box", "etb"), pulls=None):
    pulls = pulls or {}
    for kind in kinds:
        groups = group_offers(rows, kind)[:top]
        print(f"\n=== {TYPE_NAMES[kind]} - {len(groups)} cheapest ===")
        if not groups:
            print("  (nothing found)")
            continue
        for g in groups:
            r = g[0]
            flags = ("" if r["in_stock"] else "  [sold out]") + \
                    ("" if r["country"] == "NO" else f"  [from {COUNTRY_NAMES[r['country']]}, {r['orig_price']:.2f} {r['currency']}]")
            n_shops = len({x["shop"] for x in g})
            others = f"  ({n_shops} shops, up to {kr(g[-1]['price'])})" if n_shops > 1 else ""
            print(f"  {kr(r['price']):>9}  {r['lang'].upper():<2}  {r['gname'][:55]:<55} at {r['shop']}{flags}{others}")
            print(f"             {r['url']}")
            p = pulls.get(r.get("set"))
            if p:
                print("             Best pulls: " + ", ".join(f"{c['name']} {kr(c['nok'])}" for c in p["cards"]))


def oslo_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Oslo"))
    except Exception:  # Windows without tzdata: local time is Norwegian time anyway
        return datetime.now()


def write_html(rows, shops, rates, rate_source, pulls=None, site=False, out=None):
    data = json.dumps(rows, ensure_ascii=False)
    pulls_json = json.dumps(pulls or {}, ensure_ascii=False)
    stamp = oslo_now().strftime("%d.%m.%Y %H:%M")
    shop_list = ", ".join(shops)
    rate_text = ", ".join(f"1 {c} = {v:.2f} kr" for c, v in rates.items() if c in ("EUR", "SEK"))
    name = "Pokémonpris" if site else "Pokepris"
    meta = ('<meta name="description" content="Compare prices on Pokémon booster packs, booster boxes and Elite '
            'Trainer Boxes from shops that sell to Norway, and see the most valuable cards in each set.">') if site else ""
    footer = ("""<footer>Pokémonpris is an independent fan site and is not affiliated with Nintendo, The Pokémon Company
or any of the shops. Prices are collected automatically once a day and can be wrong or out of date - always check the price
in the shop before you buy. Card values: Cardmarket trend prices via TCGdex. Exchange rates: Norges Bank.</footer>""") if site else ""
    page = f"""<!doctype html>
<html lang="no"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title>{meta}
<style>
:root {{ --bg:#f6f7fb; --card:#fff; --ink:#1d2433; --muted:#667085; --line:#e4e7ec; --accent:#e3350d; --good:#067647; --warn:#b54708; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#10131a; --card:#191d27; --ink:#e7eaf0; --muted:#98a2b3; --line:#2a3040; --accent:#ff6b4a; --good:#47cd89; --warn:#fdb022; }} }}
body {{ margin:0; font:15px/1.45 system-ui,Segoe UI,sans-serif; background:var(--bg); color:var(--ink); }}
.wrap {{ max-width:1150px; margin:0 auto; padding:24px 16px 60px; }}
h1 {{ margin:0 0 4px; font-size:26px; }} .sub {{ color:var(--muted); margin-bottom:18px; }}
.controls {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px; }}
input, select {{ font:inherit; padding:8px 10px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--ink); }}
input[type=search] {{ flex:1 1 240px; }}
label.chk {{ display:flex; align-items:center; gap:6px; color:var(--muted); }}
.tabs {{ display:flex; gap:6px; margin-bottom:12px; flex-wrap:wrap; }}
.tab {{ border:1px solid var(--line); background:var(--card); color:var(--ink); padding:7px 14px; border-radius:999px; cursor:pointer; font:inherit; }}
.tab.on {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
.table {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:12px; }}
table {{ width:100%; border-collapse:collapse; }}
th, td {{ padding:10px 12px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); cursor:pointer; white-space:nowrap; }}
td.price {{ font-weight:700; white-space:nowrap; font-variant-numeric:tabular-nums; }}
.was {{ color:var(--muted); text-decoration:line-through; font-weight:400; font-size:13px; margin-left:6px; }}
.orig {{ display:block; color:var(--muted); font-weight:400; font-size:12px; }}
.pill {{ font-size:12px; padding:2px 8px; border-radius:999px; border:1px solid var(--line); white-space:nowrap; }}
.abroad {{ color:var(--warn); font-size:12px; display:block; }}
.out {{ color:var(--muted); }} .ok {{ color:var(--good); }}
a {{ color:inherit; }} .count {{ color:var(--muted); margin:8px 2px; }}
.info {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px 14px; margin-bottom:16px; color:var(--muted); font-size:14px; }}
.seg {{ display:inline-flex; border:1px solid var(--line); border-radius:10px; overflow:hidden; margin:0 0 12px; }}
.seg button {{ border:0; background:var(--card); color:var(--ink); padding:7px 14px; font:inherit; cursor:pointer; }}
.seg button.on {{ background:var(--ink); color:var(--bg); }}
tr.grp {{ cursor:pointer; }} tr.grp:hover td {{ background:rgba(127,127,127,.07); }}
tr.detail > td {{ padding:0 0 6px; background:var(--bg); }}
.offers {{ width:100%; border-collapse:collapse; }}
.offers td {{ padding:7px 12px; font-size:14px; border-bottom:1px solid var(--line); }}
.offers td:first-child {{ padding-left:34px; }}
.best {{ color:var(--good); font-weight:700; font-size:12px; margin-left:6px; }}
.caret {{ color:var(--muted); display:inline-block; width:16px; }}
.small {{ color:var(--muted); font-size:13px; display:block; }}
.save {{ color:var(--good); font-size:12px; display:block; }}
[hidden] {{ display:none !important; }}
.pulls {{ padding:10px 12px 12px 34px; }}
.pulls h4 {{ margin:4px 0 8px; font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:10px; }}
.cardbox {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:8px; display:flex; gap:8px; align-items:flex-start; }}
.cardbox img {{ width:52px; border-radius:4px; flex:none; }}
.cardbox b {{ display:block; font-size:13px; line-height:1.3; }}
.cardbox .p {{ color:var(--good); font-weight:700; font-size:14px; }}
.pull1 {{ color:var(--good); font-size:12px; display:block; }}
footer {{ color:var(--muted); font-size:13px; margin-top:24px; line-height:1.5; }}
</style></head><body><div class="wrap">
<h1>{name}</h1>
<div class="sub">Cheapest Pokémon products from {len(shops)} shops that sell to Norway. Updated {stamp}.</div>
<div class="info">Shops: {html.escape(shop_list)}.<br>
Norwegian shops: price incl. VAT, shipping not included. Shops abroad are marked in orange: the price is converted to NOK ({html.escape(rate_text)}, {html.escape(rate_source)}), and shipping, and for some shops VAT/customs, come on top.<br>
Best pulls: the 5 most valuable raw (ungraded) cards in each English and Japanese product's set, using Cardmarket trend prices via TCGdex. Chinese and Korean sets have no public card prices. Which cards you get is random - most packs contain none of them.</div>
<div class="seg" id="views"><button data-v="compare">Compare shops</button><button data-v="list">All offers</button></div>
<div class="tabs" id="tabs"></div>
<div class="controls">
  <input type="search" id="q" placeholder="Search, e.g. Ascended Heroes, 151, Prismatic ...">
  <select id="lang"><option value="">All languages</option><option value="en">English</option><option value="jp">Japanese</option><option value="cn">Chinese</option><option value="kr">Korean</option><option value="other">Other languages</option></select>
  <select id="shop"><option value="">All shops</option></select>
  <label class="chk"><input type="checkbox" id="stock" checked> In stock only</label>
  <label class="chk"><input type="checkbox" id="abroad" checked> Include shops abroad</label>
  <label class="chk"><input type="checkbox" id="multi"> Only products sold by 2+ shops</label>
</div>
<div class="count" id="count"></div>
<div class="table"><table><thead id="head"></thead><tbody id="rows"></tbody></table></div>
{footer}
</div>
<script>
const DATA = {data};
const PULLS = {pulls_json};
const TYPES = {{pack:"Booster packs", box:"Booster boxes", etb:"Elite Trainer Boxes"}};
const LANGS = {json.dumps(LANG_NAMES)};
const COUNTRIES = {json.dumps(COUNTRY_NAMES)};
let type = "pack", view = "compare", sortKey = "price", asc = true;
const opened = new Set();
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}})[c]);
const kr = n => n.toLocaleString("nb-NO", {{maximumFractionDigits:0}}) + " kr";
for (const [k, v] of Object.entries(TYPES)) {{
  const b = document.createElement("button"); b.className = "tab"; b.textContent = v; b.dataset.t = k;
  b.onclick = () => {{ type = k; render(); }}; $("tabs").appendChild(b);
}}
document.querySelectorAll("#views button").forEach(b => b.onclick = () => {{ view = b.dataset.v; sortKey = "price"; asc = true; render(); }});
[...new Set(DATA.map(r => r.shop))].sort().forEach(s => $("shop").add(new Option(s, s)));
["q","lang","shop","stock","abroad","multi"].forEach(id => $(id).addEventListener("input", render));
$("head").addEventListener("click", e => {{
  const th = e.target.closest("th"); if (!th) return;
  const k = th.dataset.k; asc = sortKey === k ? !asc : true; sortKey = k; render();
}});
$("rows").addEventListener("click", e => {{
  const tr = e.target.closest("tr.grp"); if (!tr || e.target.closest("a")) return;
  const g = tr.dataset.g; opened.has(g) ? opened.delete(g) : opened.add(g); render();
}});
const byKey = (a, b) => (a[sortKey] > b[sortKey] ? 1 : a[sortKey] < b[sortKey] ? -1 : 0) * (asc ? 1 : -1);
const priceHtml = r => `${{r.country === "NO" ? "" : "≈ "}}${{kr(r.price)}}${{r.was && r.was > r.price ? `<span class="was">${{kr(r.was)}}</span>` : ""}}
  ${{r.currency !== "NOK" ? `<span class="orig">${{r.orig_price.toFixed(2)}} ${{r.currency}}</span>` : ""}}`;
const shopHtml = r => `${{esc(r.shop)}}${{r.country !== "NO" ? `<span class="abroad" title="${{esc(r.note)}}">From ${{COUNTRIES[r.country]}} - ${{esc(r.note)}}</span>` : ""}}`;
const stockHtml = r => `<span class="${{r.in_stock ? "ok" : "out"}}">${{r.in_stock ? "In stock" : "Sold out"}}</span>`;
const head = cols => $("head").innerHTML = "<tr>" + cols.map(([k, label]) => `<th data-k="${{k}}">${{label}}${{sortKey === k ? (asc ? " ▲" : " ▼") : ""}}</th>`).join("") + "</tr>";

function render() {{
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("on", b.dataset.t === type));
  document.querySelectorAll("#views button").forEach(b => b.classList.toggle("on", b.dataset.v === view));
  $("multi").parentElement.hidden = view !== "compare";
  const words = $("q").value.toLowerCase().split(/\\s+/).filter(Boolean);
  const rows = DATA.filter(r => r.type === type
    && (!$("lang").value || r.lang === $("lang").value)
    && (!$("shop").value || r.shop === $("shop").value)
    && (!$("stock").checked || r.in_stock)
    && ($("abroad").checked || r.country === "NO")
    && words.every(w => (r.title + " " + r.gname).toLowerCase().includes(w)));
  view === "compare" ? renderCompare(rows) : renderList(rows);
}}

function renderList(rows) {{
  head([["price","Price"],["title","Product"],["lang","Language"],["shop","Shop"],["in_stock","Stock"]]);
  rows.sort(byKey);
  $("count").textContent = rows.length + " offers";
  $("rows").innerHTML = rows.map(r => `<tr>
    <td class="price">${{priceHtml(r)}}</td>
    <td><a href="${{esc(r.url)}}" target="_blank" rel="noopener">${{esc(r.title)}}</a></td>
    <td><span class="pill">${{LANGS[r.lang] || r.lang}}</span></td>
    <td>${{shopHtml(r)}}</td><td>${{stockHtml(r)}}</td></tr>`).join("");
}}

function renderCompare(rows) {{
  const m = new Map();
  rows.forEach(r => {{ if (!m.has(r.group)) m.set(r.group, []); m.get(r.group).push(r); }});
  let groups = [...m.values()].map(o => {{
    o.sort((a, b) => a.price - b.price);
    return {{ key: o[0].group, name: o[0].gname, lang: o[0].lang, offers: o, price: o[0].price,
             max: o[o.length - 1].price, shops: new Set(o.map(x => x.shop)).size }};
  }});
  if ($("multi").checked) groups = groups.filter(g => g.shops > 1);
  head([["price","Cheapest"],["name","Product"],["lang","Language"],["shops","Shops"],["max","Most expensive"]]);
  groups.sort(byKey);
  $("count").textContent = `${{groups.length}} products (${{rows.length}} offers) - click a product to see every shop`;
  $("rows").innerHTML = groups.map(g => {{
    const best = g.offers[0], isOpen = opened.has(g.key);
    const pull = PULLS[(g.offers.find(o => o.set) || {{}}).set];
    let h = `<tr class="grp" data-g="${{esc(g.key)}}">
      <td class="price">${{priceHtml(best)}}</td>
      <td><span class="caret">${{isOpen ? "▾" : "▸"}}</span>${{esc(g.name)}}<span class="small">Cheapest at ${{esc(best.shop)}}${{best.country !== "NO" ? " (" + COUNTRIES[best.country] + ")" : ""}}</span>
        ${{pull ? `<span class="pull1">Best pull: ${{esc(pull.cards[0].name)}} ≈ ${{kr(pull.cards[0].nok)}}</span>`
          : `<span class="small">${{g.lang === "cn" || g.lang === "kr" ? "No card values exist for Chinese/Korean sets" : "Card values not available for this product"}}</span>`}}</td>
      <td><span class="pill">${{LANGS[g.lang] || g.lang}}</span></td>
      <td>${{g.shops}} ${{g.shops === 1 ? "shop" : "shops"}}</td>
      <td>${{g.shops > 1 ? kr(g.max) + `<span class="save">Save ${{kr(g.max - g.price)}}</span>` : "-"}}</td></tr>`;
    if (isOpen) h += `<tr class="detail"><td colspan="5"><table class="offers">` + g.offers.map((r, i) => `<tr>
        <td class="price">${{priceHtml(r)}}${{i === 0 ? '<span class="best">Cheapest</span>' : ""}}</td>
        <td>${{shopHtml(r)}}</td><td>${{stockHtml(r)}}</td>
        <td><a href="${{esc(r.url)}}" target="_blank" rel="noopener">${{esc(r.title)}}</a></td></tr>`).join("") + `</table>` +
      (pull ? `<div class="pulls"><h4>5 most valuable cards in ${{esc(pull.name)}} (raw, Cardmarket trend price)</h4><div class="cards">` +
        pull.cards.map(c => `<div class="cardbox">${{c.image ? `<img src="${{esc(c.image)}}" alt="" loading="lazy">` : ""}}<div>
          <b>${{esc(c.name)}}</b><span class="small">#${{esc(c.number)}}${{c.rarity ? " · " + esc(c.rarity) : ""}}</span>
          <span class="p">≈ ${{kr(c.nok)}}</span><span class="small">${{c.eur.toFixed(2)}} EUR</span></div></div>`).join("") + `</div></div>` : "") +
      `</td></tr>`;
    return h;
  }}).join("");
}}
render();
</script></body></html>"""
    target = Path(out) if out else REPORT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    return target


def main():
    ap = argparse.ArgumentParser(description="Cheapest Pokémon sealed products in shops that sell to Norway")
    ap.add_argument("--search", default="", help="only products whose name contains these words")
    ap.add_argument("--lang", default="all", choices=["all", "en", "jp", "cn", "kr", "other"])
    ap.add_argument("--type", default="all", choices=["all", "pack", "box", "etb"])
    ap.add_argument("--top", type=int, default=10, help="how many to show per category")
    ap.add_argument("--all", action="store_true", help="include sold-out products in the list")
    ap.add_argument("--norway-only", action="store_true", help="only shops based in Norway")
    ap.add_argument("--check", action="store_true", help="show how many products each shop category gave")
    ap.add_argument("--no-open", action="store_true", help="don't open the HTML report")
    ap.add_argument("--no-cards", action="store_true", help="skip the best-cards-to-pull lookup")
    ap.add_argument("--refresh-cards", action="store_true", help="fetch card prices again even if cached")
    ap.add_argument("--site", action="store_true", help="build the public website version (Pokémonpris)")
    ap.add_argument("--out", help="where to save the HTML page (default: pokepris_rapport.html)")
    args = ap.parse_args()

    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    shops = [s for s in SHOPS if not args.norway_only or SHOPS[s]["country"] == "NO"]
    print(f"Checking {len(shops)} shops: {', '.join(shops)} ...")
    t0 = time.time()
    rows, rates, rate_source = scrape_all(shops, args.check)
    print(f"Found {len(rows)} products in {time.time() - t0:.0f} s.")
    if args.site and len(rows) < 300:
        # probably blocked or offline: don't replace the live website with a nearly empty page
        sys.exit(f"Only {len(rows)} products found - not updating the website.")
    pulls = {} if args.no_cards else add_best_pulls(rows, rates, args.refresh_cards)
    report = write_html(rows, shops, rates, rate_source, pulls, site=args.site, out=args.out)

    words = args.search.lower().split()
    sel = [r for r in rows
           if (args.all or r["in_stock"])
           and (args.lang == "all" or r["lang"] == args.lang)
           and (args.type == "all" or r["type"] == args.type)
           and all(w in r["title"].lower() for w in words)]
    print_table(sel, args.top, ("pack", "box", "etb") if args.type == "all" else (args.type,), pulls)
    print(f"\nFull list with search and filters: {report}")
    if not args.no_open:
        webbrowser.open(report.resolve().as_uri())


if __name__ == "__main__":
    main()
