"""Coupon model, free-text coupon parser (Chinese + English) and discount maths.

Coupon types
------------
percent_off     item-level  e.g. "鸡蛋8折" / "20% off eggs"          -> percent (off)
fixed_off_item  item-level  e.g. "豆腐立减1元"                      -> amount off per pack, max_uses packs
buy_x_get_y     item-level  e.g. "牛奶买2送1" / "buy 2 get 1"       -> buy, get
nth_percent     item-level  e.g. "第二件半价" / "第2件7折"           -> n, percent (off on every n-th pack)
threshold_off   order-level e.g. "满100减15" / "每满50减5" / "满99打9折"
                -> threshold, amount or percent, repeatable, max_discount, excludes_discounted

Stacking (see references/coupon-rules.md): on one product line, all ``stackable`` item
coupons combine, while a non-stackable item coupon is used alone; per store, all stackable
threshold coupons combine, while a non-stackable one is used alone. Item-level and
order-level coupons always combine (threshold checked on the price after item discounts).
"""

import datetime as _dt
import math
import re

from .data import COUPON_TYPES, InputError

ITEM_TYPES = ("percent_off", "fixed_off_item", "buy_x_get_y", "nth_percent")
CATEGORY_WORDS = {
    "蔬菜": "vegetable", "青菜类": "vegetable", "vegetables": "vegetable", "veggies": "vegetable",
    "肉类": "meat", "鲜肉": "meat", "meat": "meat", "海鲜": "seafood", "水产": "seafood",
    "seafood": "seafood", "乳品": "dairy", "奶制品": "dairy", "dairy": "dairy",
    "粮油": "grain", "米面": "grain", "主食": "grain", "grains": "grain", "调味": "staple",
    "调料": "staple", "蛋白": "protein",
}
CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10}
NUM = r"(\d+(?:\.\d+)?)"


def _cn_to_digits(text):
    def repl(m):
        s = m.group(0)
        if s == "十":
            return "10"
        if s.startswith("十"):
            return str(10 + CN_NUM[s[1]])
        if s.endswith("十"):
            return str(CN_NUM[s[0]] * 10)
        if "十" in s:
            return str(CN_NUM[s[0]] * 10 + CN_NUM[s[2]])
        return str(CN_NUM[s])
    return re.sub(r"[一二两三四五六七八九]?十[一二三四五六七八九]?|[零一二两三四五六七八九]", repl, text)


def _num(x):
    v = float(x)
    return int(v) if v.is_integer() else v


def describe(c, catalog=None, lang="zh"):
    scope = ""
    if c.get("ingredients") and catalog is not None:
        scope = "/".join(catalog.name(i, lang) for i in c["ingredients"]) + " "
    elif c.get("categories"):
        scope = "/".join(c["categories"]) + " "
    t = c["type"]
    if lang == "zh":
        if t == "percent_off":
            return "%s%g折" % (scope, (100 - c["percent"]) / 10)
        if t == "fixed_off_item":
            return "%s每件立减%g元" % (scope, c["amount"])
        if t == "buy_x_get_y":
            return "%s买%d送%d" % (scope, c["buy"], c["get"])
        if t == "nth_percent":
            return "%s第%d件%s" % (scope, c["n"], "半价" if c["percent"] == 50 else "%g折" % ((100 - c["percent"]) / 10))
        rep = "每" if c.get("repeatable") else ""
        if c.get("percent"):
            return "%s%s满%g打%g折" % (scope, rep, c["threshold"], (100 - c["percent"]) / 10)
        return "%s%s满%g减%g" % (scope, rep, c["threshold"], c["amount"])
    if t == "percent_off":
        return "%s%g%% off" % (scope, c["percent"])
    if t == "fixed_off_item":
        return "%s¥%g off each" % (scope, c["amount"])
    if t == "buy_x_get_y":
        return "%sbuy %d get %d free" % (scope, c["buy"], c["get"])
    if t == "nth_percent":
        return "%s%g%% off every %d%s item" % (scope, c["percent"], c["n"], "nd" if c["n"] == 2 else "th")
    rep = "every " if c.get("repeatable") else ""
    if c.get("percent"):
        return "%s%g%% off %sorders over ¥%g" % (scope, c["percent"], rep, c["threshold"])
    return "%s¥%g off %s¥%g spent" % (scope, c["amount"], rep, c["threshold"])


def normalize_coupon(c, catalog, idx=0):
    """Validate a structured coupon dict and fill defaults."""
    errs, where = [], "coupons[%d]" % idx
    if not isinstance(c, dict):
        raise InputError("%s must be an object" % where)
    t = c.get("type")
    if t not in COUPON_TYPES:
        raise InputError("%s: type must be one of %s" % (where, ", ".join(COUPON_TYPES)))
    out = {"id": str(c.get("id") or "c%d" % (idx + 1)), "type": t,
           "stackable": bool(c.get("stackable", False)),
           "source_text": c.get("source_text")}
    store = c.get("store", "*") or "*"
    if store != "*":
        sid = catalog.resolve_store(store)
        if sid is None:
            errs.append("%s: unknown store %r" % (where, store))
        store = sid
    out["store"] = store
    ings = []
    for name in c.get("ingredients", []) or []:
        ing = catalog.resolve_ingredient(name)
        if ing is None:
            errs.append("%s: unknown ingredient %r" % (where, name))
        else:
            ings.append(ing)
    out["ingredients"] = ings
    out["categories"] = list(c.get("categories", []) or [])

    def need(key, lo=0.0, hi=None, integer=False, default=None):
        v = c.get(key, default)
        try:
            v = float(v)
        except (TypeError, ValueError):
            errs.append("%s: '%s' must be a number" % (where, key))
            return None
        if v < lo or (hi is not None and v > hi) or (integer and not v.is_integer()):
            errs.append("%s: '%s'=%s out of range" % (where, key, c.get(key)))
            return None
        return int(v) if integer else v

    if t == "percent_off":
        out["percent"] = need("percent", 0.01, 100)
    elif t == "fixed_off_item":
        out["amount"] = need("amount", 0.01)
        out["max_uses"] = need("max_uses", 1, integer=True, default=c.get("max_uses", 1))
    elif t == "buy_x_get_y":
        out["buy"] = need("buy", 1, integer=True)
        out["get"] = need("get", 1, integer=True)
    elif t == "nth_percent":
        out["n"] = need("n", 2, integer=True, default=c.get("n", 2))
        out["percent"] = need("percent", 0.01, 100, default=c.get("percent", 50))
    elif t == "threshold_off":
        out["threshold"] = need("threshold", 0)
        if c.get("percent") is not None:
            out["percent"] = need("percent", 0.01, 100)
            out["amount"] = 0
        else:
            out["amount"] = need("amount", 0.01)
            out["percent"] = None
        out["repeatable"] = bool(c.get("repeatable", False))
        out["max_discount"] = need("max_discount", 0) if c.get("max_discount") is not None else None
        out["excludes_discounted"] = bool(c.get("excludes_discounted", False))
    if t in ITEM_TYPES and not ings and not out["categories"]:
        out["applies_to_all"] = True
    vu = c.get("valid_until")
    if vu:
        try:
            out["valid_until"] = _dt.date.fromisoformat(str(vu)).isoformat()
        except ValueError:
            errs.append("%s: valid_until must be YYYY-MM-DD" % where)
    if errs:
        raise InputError(errs)
    out["label"] = c.get("label") or describe(out, catalog)
    return out


def parse_coupon_text(text, catalog, idx=0, default_store=None, today=None):
    """Parse a pasted coupon / promo text such as "示例超市A：满100减15" into a coupon."""
    where = "coupon_texts[%d]" % idx
    if not isinstance(text, str) or not text.strip():
        raise InputError("%s: empty coupon text" % where)
    raw = text.strip()
    body = raw
    store = default_store or "*"
    m = re.match(r"^\s*@?([^:：@\s]+)\s*[:：]\s*(.+)$", body) or re.match(r"^\s*@(\S+)\s+(.+)$", body)
    if m and catalog.resolve_store(m.group(1)):
        store, body = catalog.resolve_store(m.group(1)), m.group(2)
    else:
        for sid, s in catalog.stores.items():
            for name in (s.get("name_zh"), s.get("name_en"), sid):
                if name and name.lower() in body.lower():
                    store = sid
                    body = re.sub(re.escape(name), " ", body, flags=re.I)
                    break
    norm = _cn_to_digits(body).replace("＋", "+").replace("¥", "").replace("￥", "")
    norm = norm.replace("元", "").replace("块", "")
    low = norm.lower()
    c = {"store": store, "source_text": raw}

    if re.search(r"不可叠加|不与其他优惠|不同享|not stackable|non-stackable|cannot be combined", low):
        c["stackable"] = False
    elif re.search(r"可叠加|可同享|stackable|can be combined", low):
        c["stackable"] = True
    if re.search(r"特价.{0,3}(除外|不参与)|不含特价|excludes? (sale|discounted)", low):
        c["excludes_discounted"] = True
    mm = re.search(r"(?:最多减|上限|封顶|max(?:imum)? discount)\s*" + NUM, low)
    if mm:
        c["max_discount"] = _num(mm.group(1))
    mm = re.search(r"(?:限|limit\s*)" + r"(\d+)\s*(?:件|份|个|packs?|items?)", low)
    if mm:
        c["max_uses"] = int(mm.group(1))
    mm = re.search(r"(?:至|到|until|截止|valid till|expires?)\s*(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", low)
    if mm:
        c["valid_until"] = "%04d-%02d-%02d" % tuple(int(x) for x in mm.groups())
    else:
        mm = re.search(r"(?:至|到|截止)\s*(\d{1,2})月(\d{1,2})日?", low)
        if mm:
            year = (today or _dt.date.today()).year
            c["valid_until"] = "%04d-%02d-%02d" % (year, int(mm.group(1)), int(mm.group(2)))

    pats = [
        (r"每满\s*" + NUM + r"\s*减\s*" + NUM, "rep_thr"),
        (r"满\s*" + NUM + r"\s*减\s*" + NUM, "thr"),
        (r"满\s*" + NUM + r"\s*(?:打|享)?\s*" + NUM + r"\s*折", "thr_pct_zhe"),
        (r"(?:spend|orders? over|over)\s*" + NUM + r"\s*(?:get|save|,)?\s*" + NUM + r"\s*off", "thr_en_rev"),
        (NUM + r"\s*off\s*(?:every|each)\s*" + NUM, "rep_thr_en"),
        (NUM + r"\s*off\s*(?:orders?\s*)?(?:over|above|on|with|when you spend|min\.?)\s*" + NUM, "thr_en"),
        (NUM + r"\s*%\s*off\s*(?:orders?\s*)?(?:over|above)\s*" + NUM, "thr_pct_en"),
        (r"买\s*(\d+)\s*[送赠]\s*(\d+)", "bxgy"),
        (r"buy\s*(\d+)\s*get\s*(\d+)", "bxgy"),
        (r"第\s*(\d+)\s*件\s*半价", "nth_half"),
        (r"第\s*(\d+)\s*件\s*" + NUM + r"\s*折", "nth_zhe"),
        (r"(?:(\d+)(?:nd|rd|th)\s*(?:item|pack)\s*half\s*price|second\s*(?:item|pack)?\s*half\s*price)", "nth_half_en"),
        (NUM + r"\s*%\s*off", "pct"),
        (r"(?:打)?\s*" + NUM + r"\s*折", "zhe"),
        (r"(?:立减|直降|减)\s*" + NUM, "fixed"),
        (NUM + r"\s*(?:yuan\s*|rmb\s*|cny\s*)?off", "fixed"),
    ]
    kind = None
    for pat, k in pats:
        mm = re.search(pat, low)
        if mm:
            kind = k
            break
    if kind is None:
        raise InputError("%s: could not understand coupon %r (try e.g. '满100减15', '鸡蛋8折', '牛奶买2送1', '第二件半价', '豆腐立减1元')" % (where, raw))
    g = mm.groups()
    rest = low[:mm.start()] + " " + low[mm.end():]

    def zhe_to_off(z):
        z = float(z)
        pay = z / 100 if z >= 10 else z / 10
        return round((1 - pay) * 100, 4)

    if kind in ("rep_thr", "thr", "thr_en", "rep_thr_en"):
        c.update(type="threshold_off", threshold=_num(g[0]) if kind in ("rep_thr", "thr") else _num(g[1]),
                 amount=_num(g[1]) if kind in ("rep_thr", "thr") else _num(g[0]),
                 repeatable=kind in ("rep_thr", "rep_thr_en"))
    elif kind == "thr_en_rev":
        c.update(type="threshold_off", threshold=_num(g[0]), amount=_num(g[1]), repeatable=False)
    elif kind == "thr_pct_zhe":
        c.update(type="threshold_off", threshold=_num(g[0]), percent=zhe_to_off(g[1]))
    elif kind == "thr_pct_en":
        c.update(type="threshold_off", threshold=_num(g[1]), percent=_num(g[0]))
    elif kind == "bxgy":
        c.update(type="buy_x_get_y", buy=int(g[0]), get=int(g[1]))
    elif kind == "nth_half":
        c.update(type="nth_percent", n=int(g[0]), percent=50)
    elif kind == "nth_zhe":
        c.update(type="nth_percent", n=int(g[0]), percent=zhe_to_off(g[1]))
    elif kind == "nth_half_en":
        c.update(type="nth_percent", n=int(g[0]) if g[0] else 2, percent=50)
    elif kind == "pct":
        c.update(type="percent_off", percent=_num(g[0]))
    elif kind == "zhe":
        c.update(type="percent_off", percent=zhe_to_off(g[0]))
    elif kind == "fixed":
        c.update(type="fixed_off_item", amount=_num(g[0]))
    c.pop("max_uses", None) if c["type"] != "fixed_off_item" else None
    if c["type"] != "threshold_off":
        c.pop("max_discount", None)
        c.pop("excludes_discounted", None)

    ings = catalog.find_ingredients_in_text(rest)
    cats = sorted({v for k, v in CATEGORY_WORDS.items() if k in rest})
    if ings:
        c["ingredients"] = ings
    elif cats:
        c["categories"] = cats
    if c["type"] in ITEM_TYPES and not ings and not cats and c["type"] != "percent_off":
        raise InputError("%s: item coupon %r must name an ingredient (e.g. '鸡蛋 买2送1')" % (where, raw))
    return normalize_coupon(c, catalog, idx=idx)


# --------------------------------------------------------------------------- maths
def applies(coupon, store, ing, catalog):
    if coupon["type"] not in ITEM_TYPES:
        return False
    if coupon["store"] not in ("*", store):
        return False
    if coupon.get("applies_to_all"):
        return True
    if ing in coupon.get("ingredients", []):
        return True
    return catalog.ingredients[ing]["category"] in coupon.get("categories", [])


def item_coupon_sets(coupons):
    """Allowed combinations of item coupons on one line (see stacking rules)."""
    stackable = [c for c in coupons if c["stackable"]]
    sets = [stackable] if stackable else [[]]
    if stackable:
        sets.append([])
    for c in coupons:
        if not c["stackable"]:
            sets.append([c])
    return sets


def product_cost(price, count, coupon_set):
    """Cost of ``count`` packs at ``price`` with the coupon set applied; returns (cost, free_packs)."""
    if count <= 0:
        return 0.0, 0
    paid, free = count, 0
    for c in coupon_set:
        if c["type"] == "buy_x_get_y":
            groups = count // (c["buy"] + c["get"])
            f = groups * c["get"]
            if f > free:
                free = f
    paid = count - free
    cost = paid * price
    for c in coupon_set:
        if c["type"] == "nth_percent":
            cost -= (paid // c["n"]) * price * c["percent"] / 100.0
    for c in coupon_set:
        if c["type"] == "percent_off":
            cost *= 1 - c["percent"] / 100.0
    for c in coupon_set:
        if c["type"] == "fixed_off_item":
            cost -= min(paid, c["max_uses"]) * c["amount"]
    return max(0.0, cost), free


def threshold_discount(c, eligible):
    if eligible + 1e-9 < c["threshold"] or eligible <= 0:
        return 0.0
    if c.get("percent"):
        d = eligible * c["percent"] / 100.0
    else:
        times = int(math.floor((eligible + 1e-9) / c["threshold"])) if c.get("repeatable") and c["threshold"] > 0 else 1
        d = c["amount"] * times
    if c.get("max_discount") is not None:
        d = min(d, c["max_discount"])
    return min(d, eligible)


def best_order_discount(subtotal, undiscounted_subtotal, coupons):
    """Best allowed combination of threshold coupons for one store's basket."""
    if not coupons or subtotal <= 0:
        return 0.0, []

    def value(cset):
        total, used = 0.0, []
        for c in cset:
            elig = undiscounted_subtotal if c.get("excludes_discounted") else subtotal
            d = threshold_discount(c, elig)
            if d > 0:
                total += d
                used.append((c, round(d, 2)))
        return min(total, subtotal), used

    options = [[c for c in coupons if c["stackable"]]] + [[c] for c in coupons if not c["stackable"]]
    best = (0.0, [])
    for opt in options:
        v = value(opt)
        if v[0] > best[0] + 1e-9:
            best = v
    return best
