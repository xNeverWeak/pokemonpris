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

TYPE_NAMES = {"pack": "Booster pack", "box": "Booster box", "etb": "Elite Trainer Box", "bundle": "Booster bundle"}
KINDS = ("pack", "box", "etb", "bundle")
LANG_NAMES = {"en": "English", "jp": "Japanese", "cn": "Chinese", "kr": "Korean", "other": "Other language"}
COUNTRY_NAMES = {"NO": "Norway", "SE": "Sweden", "NL": "Netherlands"}
# Norwegian names for the website
LANG_NAMES_NO = {"en": "Engelsk", "jp": "Japansk", "cn": "Kinesisk", "kr": "Koreansk", "other": "Annet språk"}
COUNTRY_NAMES_NO = {"NO": "Norge", "SE": "Sverige", "NL": "Nederland"}

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
        ("collection-bokser", "mixed", None),
    ]),
    "PokéNordic": dict(kind="shopify", base="https://www.pokenordic.no", country="NO", cats=[
        ("booster-pakker", "pack", None),
        ("japanske-booster-pakker", "pack", "jp"),
        ("booster-bokser", "box", None),
        ("booster-box", "box", None),
        ("japanske-booster-bokser", "box", "jp"),
        ("elite-trainer-box", "etb", None),
        ("booster-bundle", "bundle", None),
    ]),
    "Pokestore": dict(kind="shopify", base="https://pokestore.no", country="NO", cats=[
        ("pokemon-booster-pakker", "pack", None),
        ("japanske-pokemon-booster-pakker", "pack", "jp"),
        ("kinesiske-pokemon-booster-pakker", "pack", "cn"),
        ("engelske-pokemon-booster-bokser", "box", "en"),
        ("japanske-pokemon-booster-bokser", "box", "jp"),
        ("kinesiske-pokemon-booster-bokser", "box", "cn"),
        ("pokemon-elite-trainer-box", "etb", None),
        ("pokemon-booster-bundle", "bundle", None),
    ]),
    "Pokelageret": dict(kind="shopify", base="https://pokelageret.no", country="NO", cats=[
        ("booster-pakker", "pack", None),
        ("booster-box-en", "box", "en"),
        ("booster-box-jp", "box", "jp"),
        ("pokemon-elite-trainer-box", "etb", None),
        ("booster-bundle", "bundle", None),
    ]),
    "EpiCards": dict(kind="shopify", base="https://epicards.no", country="NO", cats=[
        ("booster-pakker", "pack", None),
        ("booster-display", "box", None),
        ("pokemon-elite-trainer-box", "etb", None),
        ("pokemon-special-collection-box", "mixed", None),
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
        ("/butikk/collection-bokser", "mixed", None),
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
    # --- more Norwegian shops. "auto" = the product type is read from the title, and in a whole-shop
    # listing ("all") only Pokémon products are kept.
    "Retroworld": dict(kind="shopify", base="https://retroworld.no", country="NO",
                       cats=[("pokemon-tcg", "auto", None), ("asiatisk-pokemon", "auto", None)]),
    "Laboge": dict(kind="shopify", base="https://laboge.no", country="NO", cats=[("sealed", "auto", None)]),
    "Midgard Games": dict(kind="shopify", base="https://midgardgames.no", country="NO", cats=[
        ("pokemon-booster", "auto", None), ("pokemon-display", "auto", None), ("pokemon-elite-trainer", "auto", None)]),
    "Kube Alta": dict(kind="shopify", base="https://altakube.no", country="NO", cats=[("pokemon", "auto", None)]),
    "Manaheim": dict(kind="shopify", base="https://manaheim.no", country="NO", cats=[
        ("pokemon-tcg", "auto", None), ("pokemon-booster-packs", "auto", None), ("pokemon-blister-packs", "auto", None)]),
    "Gamingsjappa": dict(kind="shopify", base="https://gamingsjappa.no", country="NO", cats=[("pokemon-tcg-kort", "auto", None)]),
    "Arctic Loot": dict(kind="shopify", base="https://arcticloot.no", country="NO",
                        cats=[("pokemon-se-alt", "auto", None), ("japansk-pokemon", "auto", "jp")]),
    "Kortkompaniet": dict(kind="shopify", base="https://kortkompaniet.no", country="NO", cats=[("sealed", "auto", None)]),
    "Lirum Larum Leg": dict(kind="shopify", base="https://www.lirumlarumleg.no", country="NO", cats=[("pokemon", "auto", None)]),
    **{name: dict(kind="shopify", base=base, country="NO", cats=[("all", "auto", None)]) for name, base in [
        ("Cardero", "https://www.cardero.no"), ("BoosterKongen", "https://boosterkongen.no"),
        ("Kortbakeren", "https://kortbakeren.no"), ("Braspill", "https://braspill.no"), ("LittleM", "https://littlemtcg.no"),
        ("Kortjungelen", "https://www.kortjungelen.no"), ("Loot Lagoon", "https://lootlagoon.no"),
        ("Pokebua", "https://pokebua.no"), ("Packs of Norway", "https://packsofnorway.no"),
        ("TCG Masters", "https://tcgmasters.no"), ("King of Breaks", "https://kingofbreaks.no"),
        ("Samleboden", "https://samleboden.no"), ("CardChase", "https://cardchase.no"), ("Spillbua", "https://spillbua.no"),
        ("PokéFriends", "https://pokefriends.no"), ("Pokélink", "https://pokelink.no"),
        ("Collectors Corner", "https://collectorscorner.no"), ("Game and Trade", "https://gameandtrade.no"),
        ("PokeButikk", "https://pokebutikk.no"), ("PokeMint", "https://pokemint.no"),
        ("Viridia Nordic", "https://viridianordic.no"), ("Spillwill", "https://spillwill.no"), ("Pokeplug", "https://pokeplug.no")]},
    # WooCommerce shops (searched through their public Store API)
    **{name: dict(kind="woo", base=base, country="NO", cats=[]) for name, base in [
        ("KanonCon", "https://www.kanoncon.no"), ("Playlot", "https://playlot.no"), ("Game Ninja", "https://www.gameninja.no"),
        ("Spillmonster", "https://spillmonster.no"), ("Ringo", "https://www.ringo.no"), ("PokéBoks", "https://pokeboks.no"),
        ("Pokechest", "https://www.pokechest.no")]},
    "Pokebud": dict(kind="nb_new", base="https://pokebud.no", country="NO", cats=[
        ("/category/pokmon-YREG0JfPg", "auto", None),
        ("/category/kinesisk-booster-box-bTZ58CiR0", "box", "cn"), ("/category/kinesisk-booster-pack-avtSrb3Ip", "pack", "cn"),
        ("/category/koreansk-booster-box-pVkMniKTt", "box", "kr"), ("/category/koreansk-booster-pack-lwiKzS6nu", "pack", "kr"),
    ]),
    "Gem Mint Collectibles": dict(kind="nb_new", base="https://gemmintcollectibles.no", country="NO", cats=[
        ("/category/booster-box-PjNNwdVXy", "box", None), ("/category/booster-packs-YkEG0iR5C", "pack", None),
        ("/category/bundles-JhazU4Dt0", "bundle", None), ("/category/elite-trainer-box-Z0M7jbMXF", "etb", None),
        ("/category/blister-Wv0E0hEND", "pack", None),
        ("/category/booster-bokser-japansk-pWkMWwqO9", "box", "jp"), ("/category/booster-packs-japansk-dz464XFvG", "pack", "jp"),
        ("/category/booster-box-chinese-Arszsbv5X", "box", "cn"), ("/category/booster-packs-chinese-HxVrjTKsw", "pack", "cn"),
    ]),
    "Emken": dict(kind="nb_classic", base="https://emken.no", country="NO", cats=[
        ("/butikk/spill-samling/pokemon/kort/boosterbokser", "box", None),
        ("/butikk/spill-samling/pokemon/kort/boosterpakker", "pack", None),
        ("/butikk/spill-samling/pokemon/kort/elite-trainer-box", "etb", None),
    ]),
    # Quickbutik shops
    "Pokecandy": dict(kind="quickbutik", base="https://pokecandy.no", country="NO", cats=[
        ("/pokemon-engelsk", "auto", None), ("/pokemon-etbupccollections", "auto", None),
        ("/pokemon-japansk", "auto", "jp"), ("/pokemon-kinesisk", "auto", "cn"),
    ]),
    "Cardhouse": dict(kind="quickbutik", base="https://cardhouse.no", country="NO", cats=[
        ("/engelsk/booster-box", "auto", None), ("/pokemon-booster-box", "auto", None),
        ("/engelsk/pokemon-booster-pakker", "auto", None), ("/pokemon-engelske-booster-pakker", "auto", None),
        ("/engelsk/pokemon-3pk-blistere", "auto", None), ("/engelsk/pokemon-bundles", "auto", None),
        ("/engelsk/pokemon-elite-trainer-box", "auto", None),
        ("/japansk/pokemon-japanske-booster-bokser", "auto", "jp"), ("/japansk/pokemon-japanske-booster-pakker", "auto", "jp"),
        ("/kinesisk/booster-bokser", "auto", "cn"), ("/kinesisk/booster-pakker", "auto", "cn"),
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
    if re.search(r"\[(?:frans|fr|duits|de|it|es)\]|\((?:fr|de|it|es)\)|french|francais|german|deutsch|italian|spanish|portugis|portugu", t):
        return "other"
    if re.search(r"japansk|japanese|japan\b|\bjpn?\b", t):
        return "jp"
    if re.search(r"kinesisk|chinese|simplified|\bcn\b|[(\[]ch[)\]]|\bs-chn?\b|\bt-chn?\b", t):
        return "cn"
    if re.search(r"koreansk|korean|\bkr\b|\bkor\b", t):
        return "kr"
    return "en"


def classify(title, default):
    """Product type from the title, with the shop's category as fallback.
    default 'mixed' = a collection-box category where only ETBs and bundles are wanted,
    'auto' = a category (or whole shop) with all kinds of products."""
    if default == "auto":
        return classify_auto(title)
    t = f" {title.lower()}"
    if ACCESSORY.search(t):
        return None  # display cases, sleeves etc. listed next to the real products
    if "elite trainer" in t or " etb" in t:
        return "etb"
    if "bundle" in t:
        # a "Booster Bundle Display"/case holds 10 bundles - not one bundle
        return None if re.search(r" case|display|\b10 ?(?:stk|stuks?|pcs)\b", t) else "bundle"
    if default in ("etb", "bundle", "mixed"):
        return None  # gift boxes, Build & Battle etc. listed alongside ETBs/bundles
    if default == "box" and " case" in t:
        return None  # multi-box cases aren't a single booster box
    is_pack = any(w in t for w in ("boosterpakke", "booster pakke", "booster pack", "boosterpack"))
    if default == "box" and is_pack and not any(w in t for w in (" box", " boks", "display")):
        return "pack"  # single pack filed under a box collection
    if default == "pack" and ("booster box" in t or "display" in t):
        return None
    return default


ACCESSORY = re.compile(r"sleeves?\b|binder|\bperm\b|playmat|deck ?box|toploader|code card|portfolio|\bmappe\b|plush|bamse|"
                       r"figur|album|akryl|acrylic|displaykasse|protector|beskytt|ultra ?pro|\bcharm\b|magnet")


def classify_auto(title):
    """Product type from the title alone; None for everything that isn't a pack, bundle, booster box or ETB."""
    t = f" {title.lower()} "
    if ACCESSORY.search(t) or re.search(r"\bcase\b", t):
        return None
    if "elite trainer" in t or re.search(r"\betb\b", t):
        return "etb"
    if "bundle" in t:
        return None if re.search(r"display|\b10 ?(?:stk|pcs|x)?\b", t) else "bundle"
    if re.search(r"\btins?\b|collection|kolleksjon|build (?:&|and|og) battle|premium|\bdeck\b|stadium|mystery|random|"
                 r"surprise|gavesett|gift|advent|julekalender|\bgraded\b|\bpsa\b|\bcgc\b", t):
        return None
    if re.search(r"booster ?-?(?:box|boks|display)|boosterbo(?:x|ks)|display ?box|\bdisplay\b", t):
        return "box"
    if re.search(r"booster ?-?(?:pack|pakke|pakker)|boosterpak|sleeved|blister|\bbooster\b", t):
        return "pack"
    return None


def is_pokemon(text):
    return ("pokemon" in text.lower() or "pokémon" in text.lower() or "pokèmon" in text.lower()) and not is_other_game(text)


def variant_kind(variant_title, default):
    """Some shops sell a single pack as a variant of the booster box product."""
    t = (variant_title or "").lower()
    if default == "bundle" and re.search(r"display|case|\b10\b", t):
        return None  # "Display (10 bundles)" sold as a variant of the bundle
    if default in ("etb", "bundle"):
        return default
    if "pack" in t or "pakke" in t:
        return "pack"
    if "box" in t or "boks" in t or "display" in t:
        return "box"
    return default


GROUP_DROP = set("""pokemon tcg the trading card game booster boosters boosterpakke boosterpakker pakke pakker pack
packs box boks bokser boxes display bundle elite trainer etb engelsk engelske english eng en japansk japanske japanese jp jpn
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


MIN_PRICE = {"pack": 5, "box": 100, "etb": 100, "bundle": 100}  # below this it's a placeholder price, not a real one


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
        for page_no in range(1, 21):
            products = json.loads(fetch(op, f"{base}/collections/{handle}/products.json?limit=250&page={page_no}")).get("products", [])
            for p in products:
                title = p["title"].strip()
                meta = f"{title} {p.get('vendor', '')} {p.get('product_type', '')}"
                if is_other_game(meta):
                    continue
                if ptype == "auto" and not is_pokemon(f"{meta} {' '.join(p.get('tags') or [])}"):
                    continue
                kind = classify(title, ptype)
                if kind is None:
                    continue
                groups = {}
                for v in p.get("variants") or []:
                    vk = variant_kind(v.get("title"), kind)
                    if vk:
                        groups.setdefault(vk, []).append(v)
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
            items = (parse_nb_classic(page) if cfg["kind"] == "nb_classic" else
                     parse_quickbutik(page, base) if cfg["kind"] == "quickbutik" else parse_nb_new(page))
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


def parse_quickbutik(page, base):
    items = []
    for c in page.split('data-qb-selector="product-item"')[1:]:
        price = re.search(r'data-price="([\d.]+)"', c[:3000])
        name = re.search(r'data-s-title="([^"]+)"', c[:3000]) or re.search(r"<h3[^>]*>\s*(.*?)\s*</h3>", c, re.S)
        url = re.search(r'<a[^>]+href="(/[^"]+)"', c)
        if not (price and name and url):
            continue
        text = strip_tags(c[:8000]).lower()
        in_stock = not any(w in text for w in ("utsolgt", "slutsåld", "tomt på lager", "ikke på lager"))
        items.append(dict(title=strip_tags(name.group(1)), url=base + url.group(1), price=float(price.group(1)),
                          in_stock=in_stock))
    return items


def read_woo(shop, cfg, op, out):
    """WooCommerce shops: search the public Store API for Pokémon products."""
    base, seen, n = cfg["base"], set(), 0
    for q in ("pokemon", "pok%C3%A9mon"):
        for page_no in range(1, 21):
            products = json.loads(fetch(op, f"{base}/wp-json/wc/store/v1/products?search={q}&per_page=100&page={page_no}"))
            for p in products:
                if p["id"] in seen:
                    continue
                seen.add(p["id"])
                title = strip_tags(p["name"])
                meta = f"{title} {' '.join(c.get('name', '') for c in p.get('categories') or [])}"
                kind = classify_auto(title) if is_pokemon(meta) else None
                if kind is None:
                    continue
                pr = p.get("prices") or {}
                unit = 10 ** int(pr.get("currency_minor_unit", 2))
                price, regular = int(pr.get("price") or 0) / unit, int(pr.get("regular_price") or 0) / unit
                out.append(dict(key=(p["id"], kind), title=title, type=kind, lang=detect_lang(meta), price=price,
                                currency=pr.get("currency_code") or "NOK", was=regular if regular > price else None,
                                in_stock=bool(p.get("is_in_stock")), url=p["permalink"]))
                n += 1
            if len(products) < 100:
                break
            time.sleep(0.3)
    return {"search": n}


def scrape_shop(shop):
    cfg = SHOPS[shop]
    op = opener_for(cfg["country"])
    raw, error = [], None
    try:
        reader = {"shopify": read_shopify, "woo": read_woo}.get(cfg["kind"], read_nettbutikk)
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


def print_table(rows, top, kinds=KINDS, pulls=None):
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


PAGE_TEMPLATE = r"""<!doctype html>
<html lang="no"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__NAME__</title>__META__
<meta name="color-scheme" content="dark">
<style>
:root { --bg:#0B1020; --top:#10172B; --panel:#141B30; --field:#1B2340; --line:#243056; --line2:#2E3A60;
  --ink:#E8ECF6; --muted:#8C97B8; --gold:#FFCB05; --gold-ink:#2A2100; --good:#5FD68F; --good-bg:#123524;
  --odds:#B9A6FF; --odds-bg:#221C45; --abroad:#F0A04B; }
* { box-sizing:border-box; }
body { margin:0; font:15px/1.45 system-ui,"Segoe UI",sans-serif; background:var(--bg); color:var(--ink); }
a { color:inherit; }
[hidden] { display:none !important; }
header.bar { background:var(--top); border-bottom:1px solid #222C4A; }
header .in { max-width:1150px; margin:0 auto; padding:12px 16px; display:flex; align-items:center; gap:10px; }
.logo { font-weight:600; font-size:20px; letter-spacing:-.01em; } .logo span { color:var(--gold); }
.ball { width:20px; height:20px; flex:none; }
.upd { margin-left:auto; color:var(--muted); font-size:13px; white-space:nowrap; }
.wrap { max-width:1150px; margin:0 auto; padding:18px 16px 60px; }
.muted { color:var(--muted); } .gold { color:var(--gold); font-weight:600; } .good { color:var(--good); } .oddc { color:var(--odds); }
.chip { display:inline-block; font-size:12px; padding:2px 8px; border-radius:5px; white-space:nowrap; }
.chip.gold { background:var(--gold); color:var(--gold-ink); font-weight:500; }
.chip.save { background:var(--good-bg); color:var(--good); }
.chip.odds { background:var(--odds-bg); color:#CFC3FF; }
.chip.lang { background:var(--field); color:#C9D1EA; }
/* A: hero */
.hero { display:flex; align-items:center; gap:20px; padding:18px 20px; border-radius:14px; background:#17162A;
  border:1px solid #3A3210; cursor:pointer; margin-bottom:12px; }
.hero:hover { border-color:var(--gold); }
.hero .txt { flex:1; min-width:0; }
.hero h2 { margin:10px 0 2px; font-size:22px; line-height:1.25; }
.hero .pr { display:flex; align-items:baseline; flex-wrap:wrap; gap:10px; margin-top:12px; }
.hero .big { font-size:30px; }
.hero .strike { color:var(--muted); text-decoration:line-through; }
.hero .oddrow { display:flex; align-items:center; gap:12px; margin-top:14px; }
.hero img { width:150px; border-radius:8px; flex:none; transform:rotate(3deg); }
.spots { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:10px; margin-bottom:22px; }
.spot { display:flex; gap:10px; align-items:center; background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:10px 12px; cursor:pointer; }
.spot:hover { border-color:var(--line2); }
.spot img { width:44px; border-radius:4px; flex:none; }
.spot .t { font-size:12px; color:var(--muted); }
.spot .n { font-size:14px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
/* filters */
.pills { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:10px; }
.pill { border:0; background:#1C2544; color:#C9D1EA; padding:6px 14px; border-radius:999px; cursor:pointer; font:inherit; font-size:14px; }
.pill.on { background:var(--gold); color:var(--gold-ink); font-weight:500; }
.controls { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:12px; }
input[type=search], select { font:inherit; font-size:14px; padding:8px 12px; border:1px solid var(--line2); border-radius:999px; background:var(--field); color:var(--ink); }
input[type=search] { flex:1 1 260px; }
label.chk { display:flex; align-items:center; gap:6px; color:var(--muted); font-size:14px; }
input[type=checkbox] { accent-color:var(--gold); }
.seg { display:inline-flex; border:1px solid var(--line2); border-radius:999px; overflow:hidden; margin-left:auto; }
.seg button { border:0; background:transparent; color:var(--muted); padding:6px 14px; font:inherit; font-size:14px; cursor:pointer; }
.seg button.on { background:var(--field); color:var(--ink); }
.count { color:var(--muted); margin:6px 2px 10px; font-size:14px; }
/* B: gallery */
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(190px, 1fr)); gap:12px; grid-auto-flow:dense; }
.tile { background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; cursor:pointer; display:flex; flex-direction:column; }
.tile:hover { border-color:var(--line2); }
.tile.open { border-color:var(--gold); }
.tile .art { height:130px; display:flex; align-items:center; justify-content:center; position:relative; }
.tile .art img { height:112px; border-radius:5px; }
.tile .art .chip { position:absolute; top:8px; right:8px; }
.tile .art .chip.lang { left:8px; right:auto; }
.tile .body { padding:10px 12px 12px; display:flex; flex-direction:column; gap:2px; flex:1; }
.tile .name { font-weight:500; line-height:1.3; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.tile .meta { font-size:12px; color:var(--muted); }
.tile .price { margin-top:auto; padding-top:6px; display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }
.tile .price .gold { font-size:17px; }
.meter { height:5px; background:var(--line); border-radius:3px; margin-top:6px; overflow:hidden; }
.meter i { display:block; height:5px; background:var(--odds); border-radius:3px; min-width:3px; }
.tile .odds { font-size:12px; color:var(--odds); margin-top:3px; }
.tile .none { font-size:12px; color:var(--muted); margin-top:6px; }
.abroad { color:var(--abroad); }
.more { display:block; margin:16px auto 0; }
button.btn { font:inherit; font-size:14px; background:var(--field); color:var(--ink); border:1px solid var(--line2); border-radius:999px; padding:8px 18px; cursor:pointer; }
/* C: detail */
.detail { grid-column:1 / -1; background:var(--top); border:1px solid var(--gold); border-radius:14px; padding:16px; }
.detail .hd { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:14px; }
.detail .hd h3 { margin:0; font-size:19px; }
.detail .x { margin-left:auto; background:transparent; border:0; color:var(--muted); font-size:22px; cursor:pointer; line-height:1; }
.cols { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1.35fr); gap:18px; }
.lbl { color:var(--muted); font-size:13px; margin:0 0 8px; display:flex; justify-content:space-between; gap:8px; }
.offer { display:flex; align-items:center; gap:10px; padding:9px 12px; background:var(--panel); border:1px solid var(--line); border-radius:10px; margin-bottom:6px; text-decoration:none; }
.offer:hover { border-color:var(--line2); }
.offer.first { border-color:var(--gold); }
.offer .s { flex:1; min-width:0; }
.offer .s small { display:block; font-size:12px; }
.offer .p { text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
.offer .p small { display:block; font-size:12px; color:var(--muted); }
.offer.out { opacity:.6; }
.was { color:var(--muted); text-decoration:line-through; font-size:12px; margin-left:4px; }
.hunt { background:#1A1530; border:1px solid #3A2F6B; border-radius:10px; padding:12px; margin-top:12px; font-size:14px; }
.hunt b { color:var(--odds); font-weight:500; display:block; margin-bottom:4px; }
.cards5 { display:grid; grid-template-columns:repeat(5, minmax(0,1fr)); gap:8px; }
.c5 { text-align:center; font-size:12px; }
.c5 img, .c5 .ph { width:100%; aspect-ratio:245 / 342; border-radius:6px; display:block; border:1px solid var(--line); background:var(--panel); }
.c5.top img { border:2px solid var(--gold); }
.c5 .nm { margin-top:4px; line-height:1.25; min-height:2.5em; }
.bars { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px; margin-top:12px; }
.bar { display:grid; grid-template-columns:150px minmax(0,1fr) 52px; gap:10px; align-items:center; font-size:13px; margin-top:6px; }
.bar .tr { height:7px; background:var(--line); border-radius:4px; overflow:hidden; }
.bar .tr i { display:block; height:7px; background:var(--odds); border-radius:4px; min-width:3px; }
.bar.me { color:var(--gold); } .bar.me .tr i { background:var(--gold); }
.bar span:last-child { text-align:right; font-variant-numeric:tabular-nums; }
/* list view */
.table { overflow-x:auto; background:var(--panel); border:1px solid var(--line); border-radius:12px; }
table { width:100%; border-collapse:collapse; }
th, td { padding:10px 12px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; font-size:14px; }
th { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); cursor:pointer; white-space:nowrap; }
td.pr { font-weight:600; color:var(--gold); white-space:nowrap; }
details.info { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:10px 14px; margin-top:26px; color:var(--muted); font-size:14px; }
details.info summary { cursor:pointer; color:var(--ink); }
details.info p { margin:8px 0 0; }
footer { color:var(--muted); font-size:13px; margin-top:18px; line-height:1.5; }
@media (max-width:760px) {
  .cols { grid-template-columns:1fr; }
  .cards5 { grid-template-columns:repeat(3, minmax(0,1fr)); }
  .bar { grid-template-columns:110px minmax(0,1fr) 46px; }
  .seg { margin-left:0; }
}
@media (max-width:520px) {
  .upd { display:none; }
  .hero { padding:14px; } .hero img { width:96px; } .hero h2 { font-size:18px; } .hero .big { font-size:24px; }
  .grid { grid-template-columns:repeat(2, minmax(0,1fr)); gap:8px; }
  .tile .art { height:110px; } .tile .art img { height:94px; }
}
</style></head><body>
<header class="bar"><div class="in">
  <svg class="ball" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8.5" fill="#10172B" stroke="#FFCB05" stroke-width="2"/><path d="M1.5 10h17" stroke="#FFCB05" stroke-width="2"/><circle cx="10" cy="10" r="2.6" fill="#10172B" stroke="#FFCB05" stroke-width="2"/></svg>
  <span class="logo">__LOGO__</span>
  <span class="upd">__SHOPCOUNT__ butikker · oppdatert __STAMP__</span>
</div></header>
<div class="wrap">
<section class="hero" id="hero" hidden></section>
<div class="spots" id="spots"></div>
<div class="pills" id="tabs"></div>
<div class="controls">
  <input type="search" id="q" placeholder="Søk etter et sett, f.eks. Chaos Rising, 151, Terastal ...">
  <select id="sort">
    <option value="popular">Populære først</option>
    <option value="price">Billigst først</option>
    <option value="save">Størst besparelse</option>
    <option value="odds">Best sjanse for topp 5</option>
    <option value="value">Mest verdifulle toppkort</option>
    <option value="shops">Flest butikker</option>
  </select>
  <select id="lang"><option value="">Alle språk</option><option value="en">Engelsk</option><option value="jp">Japansk</option><option value="cn">Kinesisk</option><option value="kr">Koreansk</option><option value="other">Andre språk</option></select>
  <select id="shop"><option value="">Alle butikker</option></select>
</div>
<div class="controls">
  <label class="chk"><input type="checkbox" id="stock" checked> Kun på lager</label>
  <label class="chk" hidden><input type="checkbox" id="abroad" checked> Ta med utenlandske butikker</label>
  <label class="chk"><input type="checkbox" id="multi"> Kun produkter fra 2+ butikker</label>
  <div class="seg" id="views"><button data-v="gallery">Galleri</button><button data-v="list">Alle tilbud</button></div>
</div>
<div class="count" id="count"></div>
<div class="grid" id="grid"></div>
<button class="btn more" id="more" hidden>Vis flere</button>
<div class="table" id="listwrap" hidden><table><thead id="head"></thead><tbody id="rows"></tbody></table></div>
<details class="info"><summary>Om prisene og sjansene</summary>
<p>Butikker: __SHOPLIST__.</p>
<p>Alle butikkene er norske. Prisene er inkl. mva., frakt kommer i tillegg.</p>
<p>Topp 5 kort: de mest verdifulle ugraderte kortene i settet til hvert engelske og japanske produkt, med trendpriser fra Cardmarket via TCGdex. Kinesiske og koreanske sett har ingen offentlige kortpriser.</p>
<p>Sjansene er grove anslag, ikke offisielle tall: vi regner med at et toppkort (special illustration rare eller sjeldnere) dukker opp i omtrent 1 av 86 pakker, og at et sett har rundt 10 slike. Gullkort og Mega hyper rare er sjeldnere enn det, og japanske sett er annerledes, så bruk prosentene bare som en pekepinn. De fleste pakker inneholder ingen av topp 5.</p>
</details>
__FOOTER__
</div>
<script>
const DATA = __DATA__;
const PULLS = __PULLS__;
const TABS = {all:"Alle", pack:"Booster-pakker", etb:"Elite Trainer Box", box:"Booster-bokser", bundle:"Booster bundles"};
const ONE = {pack:"booster-pakke", etb:"ETB", box:"booster-boks", bundle:"booster bundle"};
const THE = {pack:"denne pakken", etb:"denne ETB-en", box:"denne boksen", bundle:"denne bundlen"};
const LANGS = __LANGS__;
const COUNTRIES = __COUNTRIES__;
const TOP_TIER_PER_PACK = 1 / 86, TOP_TIER_IN_SET = 10, PAGE = 48;
const TINTS = ["#1D1A3A","#12283A","#16301F","#2A1830","#3A1E14","#33300F","#14283A","#2B1E36"];
let type = "all", view = "gallery", opened = null, limit = PAGE, listSort = "price", listAsc = true;
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]);
const kr = n => Math.round(n).toLocaleString("nb-NO") + " kr";
const pct = p => { const v = p * 100; return (v < 10 ? v.toFixed(1) : String(Math.round(v))).replace(/\.0$/, "").replace(".", ",") + " %"; };
const oneIn = p => "1 av " + Math.max(1, Math.round(1 / p)).toLocaleString("nb-NO");
const hash = s => [...String(s)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
const tint = s => TINTS[hash(s) % TINTS.length];
const bigImg = u => u.replace("/low.webp", "/high.webp");
const pic = pull => pull && pull.cards.find(c => c.image);
const BALL = `<svg width="56" height="56" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8.5" fill="none" stroke="#2E3A60" stroke-width="1.5"/><path d="M1.5 10h17" stroke="#2E3A60" stroke-width="1.5"/><circle cx="10" cy="10" r="2.6" fill="#0B1020" stroke="#2E3A60" stroke-width="1.5"/></svg>`;

function defaultPacks(t, lang) { return {pack:1, bundle:6, etb:9, box:lang === "jp" ? 30 : 36}[t]; }
function packsOf(offers, t, lang) {
  for (const o of offers) {
    const m = /(\d+)\s*(?:x\s*)?(?:booster\s*)?(?:packs?|pakker|pakke|boosters)\b/i.exec(o.title);
    if (m && +m[1] >= 1 && +m[1] <= 60 && (t !== "box" || +m[1] >= 10)) return +m[1];
  }
  return defaultPacks(t, lang);
}
const perCard = () => TOP_TIER_PER_PACK / TOP_TIER_IN_SET;
const oddsAny = (n, packs) => 1 - Math.pow(1 - n * perCard(), packs);
const oddsOne = packs => 1 - Math.pow(1 - perCard(), packs);

function build(rows) {
  const m = new Map();
  rows.forEach(r => { if (!m.has(r.group)) m.set(r.group, []); m.get(r.group).push(r); });
  return [...m.values()].map(o => {
    o.sort((a, b) => a.price - b.price);
    const f = o[0], set = (o.find(x => x.set) || {}).set, pull = PULLS[set];
    const packs = packsOf(o, f.type, f.lang), n = pull ? Math.min(5, pull.cards.length) : 0;
    return { key: f.group, name: f.gname, lang: f.lang, type: f.type, offers: o, price: f.price, best: f,
             max: o[o.length - 1].price, shops: new Set(o.map(x => x.shop)).size, pull, packs, n,
             odds: pull ? oddsAny(n, packs) : 0, value: pull ? pull.cards[0].nok : 0 };
  });
}
const priceTxt = r => (r.country === "NO" ? "" : "≈ ") + kr(r.price);

function filtered() {
  const words = $("q").value.toLowerCase().split(/\s+/).filter(Boolean);
  return DATA.filter(r => (type === "all" || r.type === type)
    && (!$("lang").value || r.lang === $("lang").value)
    && (!$("shop").value || r.shop === $("shop").value)
    && (!$("stock").checked || r.in_stock)
    && ($("abroad").checked || r.country === "NO")
    && words.every(w => (r.title + " " + r.gname).toLowerCase().includes(w)));
}

/* ---------- A: hero + spotlights ---------- */
function ring(p) {
  const c = 2 * Math.PI * 15;
  return `<svg width="44" height="44" viewBox="0 0 38 38" aria-hidden="true" style="flex:none"><circle cx="19" cy="19" r="15" fill="none" stroke="#2A2F55" stroke-width="5"/><circle cx="19" cy="19" r="15" fill="none" stroke="#B9A6FF" stroke-width="5" stroke-dasharray="${Math.max(1.5, p * c).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 19 19)"/></svg>`;
}
function renderTop() {
  const all = build(DATA.filter(r => r.in_stock));
  // a saving over 45% is usually two different products grouped together, not a real deal
  const cands = all.filter(g => g.pull && pic(g.pull) && g.value >= 300 && g.lang === "en" && g.shops > 1 && (g.type === "etb" || g.type === "box")
    && g.max > g.price && (g.max - g.price) / g.max <= 0.45);
  cands.sort((a, b) => (b.max - b.price) / b.max - (a.max - a.price) / a.max);
  const h = cands[0];
  if (h) {
    const c = h.pull.cards[0], img = pic(h.pull);
    $("hero").hidden = false;
    $("hero").dataset.g = h.key;
    $("hero").innerHTML = `<div class="txt">
      <span class="chip gold">Dagens beste kjøp</span>
      <h2>${esc(h.name)}</h2>
      <div class="muted">Billigst hos ${esc(h.best.shop)}${h.best.country !== "NO" ? " (" + COUNTRIES[h.best.country] + ")" : ""} · ${h.shops} butikker sammenlignet</div>
      <div class="pr"><span class="gold big">${priceTxt(h.best)}</span><span class="strike">${kr(h.max)}</span><span class="chip save">Spar ${kr(h.max - h.price)}</span></div>
      <div class="oddrow">${ring(h.odds)}<div><div><span class="oddc" style="font-weight:600">≈ ${pct(h.odds)}</span> sjanse for et topp 5-kort i ${THE[h.type]}</div>
        <div class="muted" style="font-size:13px">Beste: ${esc(c.name)} ≈ ${kr(c.nok)} · anslag</div></div></div>
    </div><img src="${esc(bigImg(img.image))}" alt="${esc(img.name)}">`;
  }
  const spots = [["box", "Billigste booster-boks"], ["etb", "Billigste ETB"], ["pack", "Billigste booster-pakke"]].map(([t, label]) => {
    const g = all.filter(x => x.type === t && x.pull && x.best.country === "NO").sort((a, b) => a.price - b.price)[0];
    if (!g) return "";
    const c = pic(g.pull);
    return `<div class="spot" data-g="${esc(g.key)}">${c ? `<img src="${esc(c.image)}" alt="" loading="lazy">` : ""}
      <div style="min-width:0"><div class="t">${label}</div><div class="n">${esc(g.name)}</div>
      <div><span class="gold">${kr(g.price)}</span> <span class="chip odds">topp 5 ≈ ${pct(g.odds)}</span></div></div></div>`;
  });
  $("spots").innerHTML = spots.join("");
}
function jumpTo(key) {
  const g = build(DATA.filter(r => r.group === key))[0];
  if (!g) return;
  type = "all"; $("q").value = ""; $("lang").value = ""; $("shop").value = ""; $("multi").checked = false;
  $("stock").checked = true; $("abroad").checked = true; view = "gallery"; opened = key;
  const order = sortGroups(build(filtered()));
  limit = Math.max(PAGE, order.findIndex(x => x.key === key) + 1);
  render();
  const el = document.querySelector(`.tile[data-g="${CSS.escape(key)}"]`);
  if (el) el.scrollIntoView({behavior: "smooth", block: "start"});
}

/* ---------- B: gallery ---------- */
function sortGroups(gs) {
  const s = $("sort").value;
  const f = {popular: (a, b) => !!pic(b.pull) - !!pic(a.pull) || (b.lang === "en") - (a.lang === "en") || b.shops - a.shops || a.price - b.price,
             price: (a, b) => a.price - b.price, save: (a, b) => (b.max - b.price) - (a.max - a.price),
             odds: (a, b) => b.odds - a.odds || a.price - b.price, value: (a, b) => b.value - a.value || a.price - b.price,
             shops: (a, b) => b.shops - a.shops || a.price - b.price}[s];
  return gs.sort(f);
}
function tile(g) {
  const c = pic(g.pull);
  const art = c ? `<img src="${esc(c.image)}" alt="${esc(c.name)}" loading="lazy">` : BALL;
  const odds = g.pull
    ? `<div class="meter"><i style="width:${Math.min(100, g.odds * 100)}%"></i></div><div class="odds">${oneIn(g.odds)} får et topp 5-kort</div>`
    : `<div class="none">${g.lang === "cn" || g.lang === "kr" ? "Ingen kortverdier for kinesiske/koreanske sett" : "Ingen kortverdier for dette produktet"}</div>`;
  return `<div class="tile${opened === g.key ? " open" : ""}" data-g="${esc(g.key)}">
    <div class="art" style="background:${tint(g.pull ? g.pull.name : g.key)}">${art}
      ${g.lang !== "en" ? `<span class="chip lang">${esc((LANGS[g.lang] || g.lang).slice(0, 3).toUpperCase())}</span>` : ""}
      ${g.pull ? `<span class="chip odds">≈ ${pct(g.odds)}</span>` : ""}</div>
    <div class="body"><div class="name">${esc(g.name)}</div>
      <div class="meta">${ONE[g.type] ? ONE[g.type][0].toUpperCase() + ONE[g.type].slice(1) : ""} · ${g.packs} ${g.packs === 1 ? "pakke" : "pakker"} · ${g.shops} ${g.shops === 1 ? "butikk" : "butikker"}</div>
      <div class="price"><span class="gold${g.best.country !== "NO" ? " abroad" : ""}">${priceTxt(g.best)}</span>${g.shops > 1 && g.max > g.price ? `<span class="chip save">Spar ${kr(g.max - g.price)}</span>` : ""}</div>
      ${odds}</div></div>`;
}

/* ---------- C: detail ---------- */
function detail(g) {
  const offers = g.offers.map((r, i) => `<a class="offer${i === 0 ? " first" : ""}${r.in_stock ? "" : " out"}" href="${esc(r.url)}" target="_blank" rel="noopener">
      <span class="s">${i === 0 ? "👑 " : ""}${esc(r.shop)}${r.country !== "NO" ? ` <span class="abroad">${COUNTRIES[r.country]}</span>` : ""}
        <small class="${r.in_stock ? "good" : "muted"}">${r.in_stock ? "På lager" : "Utsolgt"}${r.country !== "NO" ? ` · <span class="abroad">${esc(r.note)}</span>` : ""}</small></span>
      <span class="p"><span class="${i === 0 ? "gold" : ""}">${priceTxt(r)}</span>${r.was && r.was > r.price ? `<span class="was">${kr(r.was)}</span>` : ""}
        ${r.currency !== "NOK" ? `<small>${r.orig_price.toFixed(2).replace(".", ",")} ${r.currency}</small>` : "<small>Til butikken ↗</small>"}</span></a>`).join("");
  let right = `<p class="muted">${g.lang === "cn" || g.lang === "kr" ? "Det finnes ingen offentlige kortpriser for kinesiske og koreanske sett, så dette produktet har ingen toppkort eller sjanser." : "Vi fant ikke hvilket kortsett dette produktet hører til, så det har ingen toppkort eller sjanser."}</p>`;
  let hunt = "";
  if (g.pull) {
    const one = oddsOne(g.packs);
    const cards = g.pull.cards.slice(0, 5).map((c, i) => `<div class="c5${i === 0 ? " top" : ""}">
        ${c.image ? `<img src="${esc(c.image)}" alt="${esc(c.name)}" loading="lazy">` : `<div class="ph"></div>`}
        <div class="nm">${esc(c.name)}</div><div class="${i === 0 ? "gold" : ""}">${kr(c.nok)}</div><div class="oddc">≈ ${pct(one)}</div></div>`).join("");
    const rows = [["pack", 1], ["bundle", 6], ["etb", 9], ["box", defaultPacks("box", g.lang)]].map(([t, p]) => [ONE[t][0].toUpperCase() + ONE[t].slice(1) + ` (${p})`, p]);
    if (!rows.some(r => r[1] === g.packs)) rows.push([`Dette produktet (${g.packs})`, g.packs]);
    rows.sort((a, b) => a[1] - b[1]);
    const bars = rows.map(([label, p]) => { const o = oddsAny(g.n, p);
      return `<div class="bar${p === g.packs ? " me" : ""}"><span>${label}</span><div class="tr"><i style="width:${Math.min(100, o * 100)}%"></i></div><span>${pct(o)}</span></div>`; }).join("");
    right = `<div class="lbl"><span>Topp ${g.n} kort i ${esc(g.pull.name)} · sjanse per ${ONE[g.type]}</span><span class="oddc">Minst ett ≈ ${pct(g.odds)}</span></div>
      <div class="cards5">${cards}</div>
      <div class="bars"><div class="lbl" style="margin:0"><span>Sjanse for minst ett topp ${g.n}-kort</span><span>anslag</span></div>${bars}</div>`;
    const packsNeeded = 1 / (g.n * perCard()), perPack = g.price / g.packs, c = g.pull.cards[0];
    hunt = `<div class="hunt"><b>Jakte eller kjøpe?</b>
      I snitt må du åpne rundt ${Math.round(packsNeeded).toLocaleString("nb-NO")} pakker for å få ett av topp ${g.n}-kortene ≈ <span class="muted">${kr(packsNeeded * perPack)}</span> med denne prisen.<br>
      Kjøpe ${esc(c.name)} direkte: <span class="gold">${kr(c.nok)}</span>.</div>`;
  }
  return `<div class="detail" id="detail"><div class="hd"><h3>${esc(g.name)}</h3>
      <span class="chip lang">${LANGS[g.lang] || g.lang}</span><span class="chip odds">${g.packs} ${g.packs === 1 ? "pakke" : "pakker"}</span>
      <button class="x" id="close" aria-label="Lukk">×</button></div>
    <div class="cols"><div><div class="lbl"><span>Priser i butikkene</span><span>${g.shops} ${g.shops === 1 ? "butikk" : "butikker"}</span></div>${offers}${hunt}</div>
      <div>${right}</div></div></div>`;
}

function renderGallery(rows) {
  let gs = build(rows);
  if ($("multi").checked) gs = gs.filter(g => g.shops > 1);
  sortGroups(gs);
  $("count").textContent = `${gs.length} produkter (${rows.length} tilbud) · klikk på et produkt for å se alle butikkene og toppkortene`;
  const shown = gs.slice(0, limit);
  let h = "";
  shown.forEach(g => { h += tile(g); if (g.key === opened) h += detail(g); });
  $("grid").innerHTML = h;
  $("more").hidden = gs.length <= limit;
  const x = $("close"); if (x) x.onclick = e => { e.stopPropagation(); opened = null; render(); };
}

/* ---------- list view ---------- */
function renderList(rows) {
  const cols = [["price","Pris"],["title","Produkt"],["lang","Språk"],["shop","Butikk"],["in_stock","Lager"]];
  $("head").innerHTML = "<tr>" + cols.map(([k, l]) => `<th data-k="${k}">${l}${listSort === k ? (listAsc ? " ▲" : " ▼") : ""}</th>`).join("") + "</tr>";
  rows.sort((a, b) => (a[listSort] > b[listSort] ? 1 : a[listSort] < b[listSort] ? -1 : 0) * (listAsc ? 1 : -1));
  $("count").textContent = rows.length + " tilbud";
  $("rows").innerHTML = rows.map(r => `<tr><td class="pr${r.country !== "NO" ? " abroad" : ""}">${priceTxt(r)}</td>
    <td><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a></td>
    <td>${LANGS[r.lang] || r.lang}</td><td>${esc(r.shop)}${r.country !== "NO" ? ` <span class="abroad">(${COUNTRIES[r.country]})</span>` : ""}</td>
    <td class="${r.in_stock ? "good" : "muted"}">${r.in_stock ? "På lager" : "Utsolgt"}</td></tr>`).join("");
}

function render() {
  document.querySelectorAll("#tabs .pill").forEach(b => b.classList.toggle("on", b.dataset.t === type));
  document.querySelectorAll("#views button").forEach(b => b.classList.toggle("on", b.dataset.v === view));
  const rows = filtered();
  $("grid").hidden = view !== "gallery"; $("listwrap").hidden = view !== "list";
  $("multi").parentElement.hidden = $("sort").hidden = view !== "gallery";
  if (view === "gallery") renderGallery(rows); else { $("more").hidden = true; renderList(rows); }
}

for (const [k, v] of Object.entries(TABS)) {
  const b = document.createElement("button"); b.className = "pill"; b.textContent = v; b.dataset.t = k;
  b.onclick = () => { type = k; limit = PAGE; render(); }; $("tabs").appendChild(b);
}
[...new Set(DATA.map(r => r.shop))].sort().forEach(s => $("shop").add(new Option(s, s)));
["q","lang","shop","stock","abroad","multi","sort"].forEach(id => $(id).addEventListener("input", () => { limit = PAGE; render(); }));
document.querySelectorAll("#views button").forEach(b => b.onclick = () => { view = b.dataset.v; render(); });
$("more").onclick = () => { limit += PAGE; render(); };
$("grid").addEventListener("click", e => {
  const t = e.target.closest(".tile"); if (!t) return;
  opened = opened === t.dataset.g ? null : t.dataset.g; render();
  const d = $("detail"); if (d) d.scrollIntoView({behavior: "smooth", block: "nearest"});
});
$("head").addEventListener("click", e => {
  const th = e.target.closest("th"); if (!th) return;
  listAsc = listSort === th.dataset.k ? !listAsc : true; listSort = th.dataset.k; render();
});
$("hero").onclick = () => jumpTo($("hero").dataset.g);
$("spots").addEventListener("click", e => { const s = e.target.closest(".spot"); if (s) jumpTo(s.dataset.g); });
renderTop();
render();
</script></body></html>"""


def write_html(rows, shops, rates, rate_source, pulls=None, site=False, out=None):
    stamp = oslo_now().strftime("%d.%m.%Y %H:%M")
    name = "Pokémonpris" if site else "Pokepris"
    logo = "Pokémon<span>pris</span>" if site else "Poke<span>pris</span>"
    meta = ('<meta name="description" content="Sammenlign priser på Pokémon booster-pakker, booster-bokser og Elite '
            'Trainer Boxes fra butikker som selger til Norge, og se de mest verdifulle kortene i hvert sett.">') if site else ""
    footer = ("""<footer>Pokémonpris er en uavhengig fanside og har ingen tilknytning til Nintendo, The Pokémon Company
eller noen av butikkene. Prisene hentes automatisk to ganger om dagen og kan være feil eller utdaterte - sjekk alltid prisen
i butikken før du kjøper. Kortverdier: trendpriser fra Cardmarket via TCGdex. Valutakurser: Norges Bank.
Sjansene for å trekke kort er grove anslag, ikke offisielle tall.</footer>""") if site else ""
    rate_text = ", ".join(f"1 {c} = {v:.2f} kr" for c, v in rates.items() if c in ("EUR", "SEK"))
    parts = {
        "__NAME__": name, "__LOGO__": logo, "__META__": meta, "__FOOTER__": footer, "__STAMP__": stamp,
        "__SHOPCOUNT__": str(len(shops)), "__SHOPLIST__": html.escape(", ".join(shops)),
        "__RATES__": html.escape(rate_text), "__RATESRC__": html.escape(rate_source),
        "__LANGS__": json.dumps(LANG_NAMES_NO, ensure_ascii=False), "__COUNTRIES__": json.dumps(COUNTRY_NAMES_NO, ensure_ascii=False),
        # "</" inside the JSON would end the <script> block early
        "__PULLS__": json.dumps(pulls or {}, ensure_ascii=False).replace("</", "<\\/"),
        "__DATA__": json.dumps(rows, ensure_ascii=False).replace("</", "<\\/"),
    }
    page = re.sub("|".join(parts), lambda m: parts[m.group(0)], PAGE_TEMPLATE)
    target = Path(out) if out else REPORT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    return target


def main():
    ap = argparse.ArgumentParser(description="Cheapest Pokémon sealed products in shops that sell to Norway")
    ap.add_argument("--search", default="", help="only products whose name contains these words")
    ap.add_argument("--lang", default="all", choices=["all", "en", "jp", "cn", "kr", "other"])
    ap.add_argument("--type", default="all", choices=["all", *KINDS])
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
    print_table(sel, args.top, KINDS if args.type == "all" else (args.type,), pulls)
    print(f"\nFull list with search and filters: {report}")
    if not args.no_open:
        webbrowser.open(report.resolve().as_uri())


if __name__ == "__main__":
    main()
