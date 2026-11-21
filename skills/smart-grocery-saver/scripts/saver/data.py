"""Loading, validation and normalisation of catalog, recipes and user input."""

import copy
import csv
import datetime as _dt
import io
import json
import math
import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = SKILL_DIR / "data"
SCHEMA_PATH = SKILL_DIR / "schemas" / "input.schema.json"

DIET_RESTRICTIONS = {
    "vegetarian": {"pork", "beef", "chicken", "fish", "shellfish"},
    "vegan": {"pork", "beef", "chicken", "fish", "shellfish", "egg", "dairy"},
    "pescatarian": {"pork", "beef", "chicken"},
    "no_pork": {"pork"},
    "no_beef": {"beef"},
    "no_seafood": {"fish", "shellfish"},
    "shellfish_free": {"shellfish"},
    "gluten_free": {"gluten"},
    "lactose_free": {"dairy"},
    "nut_free": {"peanut"},
    "egg_free": {"egg"},
    "soy_free": {"soy"},
    "no_spicy": {"spicy"},
}
ALLERGEN_TAGS = {"egg", "dairy", "gluten", "peanut", "soy", "shellfish", "fish", "spicy",
                 "pork", "beef", "chicken"}
ALLERGEN_ALIASES = {
    "鸡蛋": "egg", "蛋": "egg", "牛奶": "dairy", "乳制品": "dairy", "奶": "dairy",
    "麸质": "gluten", "小麦": "gluten", "花生": "peanut", "坚果": "peanut", "nuts": "peanut",
    "nut": "peanut", "大豆": "soy", "黄豆": "soy", "虾": "shellfish", "贝类": "shellfish",
    "甲壳类": "shellfish", "shrimp": "shellfish", "鱼": "fish", "辣": "spicy",
    "猪肉": "pork", "牛肉": "beef", "鸡肉": "chicken", "milk": "dairy", "wheat": "gluten",
}
COUPON_TYPES = ("percent_off", "fixed_off_item", "buy_x_get_y", "nth_percent", "threshold_off")


class InputError(ValueError):
    """Raised when user input is invalid. ``errors`` holds all problems found."""

    def __init__(self, errors):
        if isinstance(errors, str):
            errors = [errors]
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- schema
_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool, "null": type(None),
}


def _type_ok(value, expected):
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[expected])


def validate_schema(instance, schema, path="$"):
    """Validate against the JSON-Schema subset used by this skill. Returns error strings."""
    errors = []
    types = schema.get("type")
    if types is not None:
        types = types if isinstance(types, list) else [types]
        if not any(_type_ok(instance, t) for t in types):
            return ["%s: expected %s, got %s" % (path, "/".join(types), type(instance).__name__)]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append("%s: %r is not one of %s" % (path, instance, schema["enum"]))
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append("%s: %s < minimum %s" % (path, instance, schema["minimum"]))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append("%s: %s > maximum %s" % (path, instance, schema["maximum"]))
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append("%s: string shorter than %d" % (path, schema["minLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append("%s: %r does not match %s" % (path, instance, schema["pattern"]))
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append("%s: missing required property '%s'" % (path, key))
        props = schema.get("properties", {})
        for key, value in instance.items():
            if key in props:
                errors.extend(validate_schema(value, props[key], "%s.%s" % (path, key)))
            elif schema.get("additionalProperties") is False and not key.startswith("_"):
                errors.append("%s: unknown property '%s'" % (path, key))
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append("%s: needs at least %d items" % (path, schema["minItems"]))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append("%s: at most %d items" % (path, schema["maxItems"]))
        if "items" in schema:
            for i, item in enumerate(instance):
                errors.extend(validate_schema(item, schema["items"], "%s[%d]" % (path, i)))
    if "anyOf" in schema:
        if not any(not validate_schema(instance, sub, path) for sub in schema["anyOf"]):
            errors.append("%s: does not match any allowed form" % path)
    return errors


def load_schema():
    return load_json(SCHEMA_PATH)


# --------------------------------------------------------------------------- catalog
class Catalog:
    """Ingredients, stores and purchasable products (pack size + price)."""

    def __init__(self, raw):
        self.raw = raw
        self.currency = raw.get("currency", "CNY")
        self.is_sample = bool(raw.get("is_sample", False))
        self.notice = raw.get("_notice", "")
        self.notice_zh = raw.get("_notice_zh", "")
        self.ingredients = raw["ingredients"]
        self.stores = {s["id"]: s for s in raw["stores"]}
        self.products = []
        for p in raw["products"]:
            self._add_product(p)
        self._alias = {}
        for ing_id, meta in self.ingredients.items():
            for name in [ing_id, meta.get("name_zh"), meta.get("name_en")] + list(meta.get("aliases", [])):
                if name:
                    self._alias.setdefault(name.lower(), ing_id)

    def _add_product(self, p):
        errs = []
        if p.get("ingredient") not in self.ingredients:
            errs.append("product references unknown ingredient %r" % p.get("ingredient"))
        if p.get("store") not in self.stores:
            errs.append("product references unknown store %r" % p.get("store"))
        try:
            qty, price = float(p["pack_qty"]), float(p["price"])
        except (KeyError, TypeError, ValueError):
            errs.append("product %r needs numeric pack_qty and price" % p)
            qty = price = 0
        if not errs and (qty <= 0 or price < 0):
            errs.append("product %r: pack_qty must be > 0 and price >= 0" % p)
        if errs:
            raise InputError(errs)
        prod = dict(p)
        prod["pack_qty"], prod["price"] = qty, round(price, 2)
        prod.setdefault("label", "%s %g%s" % (self.name(p["ingredient"], "zh"), qty,
                                              self.ingredients[p["ingredient"]]["unit"]))
        self.products.append(prod)

    def name(self, ing_id, lang="zh"):
        meta = self.ingredients.get(ing_id, {})
        return meta.get("name_" + lang) or meta.get("name_en") or ing_id

    def store_name(self, store_id, lang="zh"):
        s = self.stores.get(store_id, {})
        return s.get("name_" + lang) or s.get("name_en") or store_id

    def unit(self, ing_id):
        return self.ingredients[ing_id]["unit"]

    def resolve_ingredient(self, name):
        if name is None:
            return None
        key = str(name).strip().lower()
        if key in self._alias:
            return self._alias[key]
        return None

    def find_ingredients_in_text(self, text):
        """Return ingredient ids mentioned in free text (longest alias match first)."""
        found, low = [], text.lower()
        for alias in sorted(self._alias, key=len, reverse=True):
            if len(alias) < 1 or alias not in low:
                continue
            if re.fullmatch(r"[a-z_ ]+", alias) and not re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(alias), low):
                continue
            ing = self._alias[alias]
            if ing not in found:
                found.append(ing)
            low = low.replace(alias, " ")
        return found

    def resolve_store(self, name):
        if name is None:
            return None
        key = str(name).strip().lower()
        for sid, s in self.stores.items():
            names = [sid, s.get("name_zh", ""), s.get("name_en", "")] + list(s.get("aliases", []))
            if key in [n.lower() for n in names if n]:
                return sid
        return None

    def products_for(self, ing_id, store_id):
        return [p for p in self.products if p["ingredient"] == ing_id and p["store"] == store_id]

    def stores_selling(self, ing_id):
        return sorted({p["store"] for p in self.products if p["ingredient"] == ing_id})

    def apply_overrides(self, overrides):
        """Replace or add products. Each override: store, ingredient, pack_qty, price."""
        errors = []
        for i, ov in enumerate(overrides or []):
            store = self.resolve_store(ov.get("store")) or ov.get("store")
            ing = self.resolve_ingredient(ov.get("ingredient"))
            if store not in self.stores:
                errors.append("price_overrides[%d]: unknown store %r" % (i, ov.get("store")))
                continue
            if ing is None:
                errors.append("price_overrides[%d]: unknown ingredient %r" % (i, ov.get("ingredient")))
                continue
            try:
                qty = float(ov["pack_qty"])
            except (KeyError, TypeError, ValueError):
                errors.append("price_overrides[%d]: pack_qty must be a number" % i)
                continue
            if ov.get("remove"):
                self.products = [p for p in self.products if not (
                    p["store"] == store and p["ingredient"] == ing and p["pack_qty"] == qty)]
                continue
            self.products = [p for p in self.products if not (
                p["store"] == store and p["ingredient"] == ing and p["pack_qty"] == qty)]
            try:
                self._add_product({"store": store, "ingredient": ing, "pack_qty": qty,
                                   "price": ov.get("price"), "label": ov.get("label")
                                   or "%s %g%s" % (self.name(ing), qty, self.unit(ing))})
            except InputError as exc:
                errors.extend("price_overrides[%d]: %s" % (i, e) for e in exc.errors)
        if errors:
            raise InputError(errors)

    def to_csv(self):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["store", "ingredient", "name_zh", "name_en", "unit", "pack_qty", "price", "label"])
        for p in sorted(self.products, key=lambda p: (p["ingredient"], p["store"], p["pack_qty"])):
            ing = p["ingredient"]
            w.writerow([p["store"], ing, self.name(ing, "zh"), self.name(ing, "en"), self.unit(ing),
                        "%g" % p["pack_qty"], "%.2f" % p["price"], p.get("label", "")])
        return buf.getvalue()


def overrides_from_csv(text):
    """Parse a price CSV (same columns as ``Catalog.to_csv``) into price overrides."""
    rows, errors = [], []
    for n, row in enumerate(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))), start=2):
        if not row.get("store") or not row.get("ingredient"):
            continue
        try:
            qty, price = float(row.get("pack_qty") or ""), float(row.get("price") or "")
        except ValueError:
            errors.append("prices CSV line %d: pack_qty and price must be numbers" % n)
            continue
        rows.append({"store": row["store"].strip(), "ingredient": row["ingredient"].strip(),
                     "pack_qty": qty, "price": price, "label": (row.get("label") or "").strip() or None})
    if errors:
        raise InputError(errors)
    return rows


def load_catalog(path=None):
    return Catalog(load_json(path or DATA_DIR / "sample_catalog.json"))


def load_recipes(path=None):
    raw = load_json(path or DATA_DIR / "recipes.json")
    return raw["recipes"] if isinstance(raw, dict) else raw


def recipe_tags(recipe, catalog):
    tags = set(recipe.get("tags", []))
    for ing in recipe["ingredients"]:
        tags.update(catalog.ingredients.get(ing, {}).get("tags", []))
    return tags


# --------------------------------------------------------------------------- input
DEFAULTS = {
    "household": {"adults": 2, "children": 0, "child_portion": 0.6},
    "budget": {"weekly": 300},
    "meals": {"breakfast": 0, "lunch": 0, "dinner": 7},
    "dishes_per_meal": 2,
    "diet": {"restrictions": [], "allergies": [], "dislikes": [], "favorites": []},
    "pantry": [],
    "assume_staples": True,
    "stores": {"allowed": None, "max_stores": 2, "default_store": None, "extra_store_cost": 0},
    "coupons": [],
    "coupon_texts": [],
    "price_overrides": [],
    "start_date": None,
    "variety": {"max_repeats": 2, "variety_weight": 3.0, "min_meat_ratio": 0.4},
    "output": {"lang": "zh", "include_contest_tags": False, "extra_hashtags": []},
}


def _merge(defaults, given):
    out = copy.deepcopy(defaults)
    for key, value in (given or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _meal_days(value, meal):
    if isinstance(value, list):
        return sorted({int(d) for d in value})
    n = int(value or 0)
    if meal == "lunch":
        return list(range(8 - n, 8)) if n else []
    return list(range(1, n + 1))


def parse_pantry_item(item, catalog):
    """Accept {"ingredient": "鸡蛋", "qty": 6} or the string "鸡蛋:6" / "rice 2000"."""
    if isinstance(item, str):
        m = re.match(r"^\s*(.+?)\s*[:：x×\s]\s*([\d.]+)\s*([a-zA-Z枚个克g毫升ml]*)\s*$", item)
        if not m:
            return None, "pantry item %r: use 'name:qty'" % item
        item = {"ingredient": m.group(1), "qty": float(m.group(2))}
    ing = catalog.resolve_ingredient(item.get("ingredient"))
    if ing is None:
        return None, "pantry: unknown ingredient %r" % item.get("ingredient")
    try:
        qty = float(item.get("qty", 0))
    except (TypeError, ValueError):
        return None, "pantry: qty for %r must be a number" % item.get("ingredient")
    if qty < 0:
        return None, "pantry: qty for %r must be >= 0" % item.get("ingredient")
    return (ing, qty), None


def normalize_input(raw, catalog, recipes, validate=True):
    """Return a fully-defaulted, resolved request dict. Raises InputError."""
    from . import coupons as coupon_mod  # local import to avoid cycle

    if not isinstance(raw, dict):
        raise InputError("input must be a JSON object")
    errors = validate_schema(raw, load_schema()) if validate else []
    if errors:
        raise InputError(errors)
    req = _merge(DEFAULTS, raw)
    hh = req["household"]
    portions = float(hh.get("adults", 0)) + float(hh.get("children", 0)) * float(hh.get("child_portion", 0.6))
    if portions <= 0:
        errors.append("household: needs at least one adult or child")
    req["portions"] = round(portions, 2)

    meals = {}
    for meal in ("breakfast", "lunch", "dinner"):
        try:
            days = _meal_days(req["meals"].get(meal, 0), meal)
        except (TypeError, ValueError):
            errors.append("meals.%s must be a count 0-7 or a list of day numbers" % meal)
            days = []
        if any(d < 1 or d > 7 for d in days):
            errors.append("meals.%s: day numbers must be within 1-7" % meal)
        meals[meal] = days
    if not any(meals.values()):
        errors.append("meals: plan at least one meal")
    req["meal_days"] = meals

    start = req.get("start_date")
    if start:
        try:
            req["start_date"] = _dt.date.fromisoformat(start)
        except ValueError:
            errors.append("start_date must be YYYY-MM-DD")
            req["start_date"] = _dt.date.today()
    else:
        req["start_date"] = _dt.date.today()

    try:
        catalog.apply_overrides(req.get("price_overrides"))
    except InputError as exc:
        errors.extend(exc.errors)

    allowed = req["stores"].get("allowed") or list(catalog.stores)
    resolved = []
    for s in allowed:
        sid = catalog.resolve_store(s)
        if sid is None:
            errors.append("stores.allowed: unknown store %r (known: %s)" % (s, ", ".join(catalog.stores)))
        elif sid not in resolved:
            resolved.append(sid)
    req["stores"]["allowed"] = resolved
    default_store = req["stores"].get("default_store")
    if default_store:
        sid = catalog.resolve_store(default_store)
        if sid is None:
            errors.append("stores.default_store: unknown store %r" % default_store)
        req["stores"]["default_store"] = sid
    elif resolved:
        req["stores"]["default_store"] = resolved[0]
    req["stores"]["max_stores"] = max(1, int(req["stores"].get("max_stores") or 1))

    diet = req["diet"]
    forbidden = set()
    for r in diet.get("restrictions", []):
        if r not in DIET_RESTRICTIONS:
            errors.append("diet.restrictions: unknown %r (known: %s)" % (r, ", ".join(sorted(DIET_RESTRICTIONS))))
        else:
            forbidden |= DIET_RESTRICTIONS[r]
    for a in diet.get("allergies", []):
        tag = ALLERGEN_ALIASES.get(str(a).lower(), str(a).lower())
        if tag in ALLERGEN_TAGS:
            forbidden.add(tag)
        else:
            ing = catalog.resolve_ingredient(a)
            if ing is None:
                errors.append("diet.allergies: unknown allergen %r" % a)
            else:
                diet.setdefault("dislikes", []).append(a)
    req["forbidden_tags"] = sorted(forbidden)

    recipe_ids = {r["id"] for r in recipes}
    recipe_by_name = {}
    for r in recipes:
        recipe_by_name[r["name_zh"]] = r["id"]
        recipe_by_name[r["name_en"].lower()] = r["id"]
    dis_ing, dis_rec = set(), set()
    req["input_warnings"] = []
    for d in diet.get("dislikes", []):
        key = str(d)
        if key in recipe_ids or key.lower() in recipe_by_name or key in recipe_by_name:
            dis_rec.add(key if key in recipe_ids else recipe_by_name.get(key, recipe_by_name.get(key.lower())))
            continue
        ing = catalog.resolve_ingredient(key)
        if ing is None:
            req["input_warnings"].append(
                ("“%s”不在食材库中，现有菜谱都不使用它。" % d) if req["output"].get("lang", "zh") == "zh"
                else "'%s' is not in the ingredient catalog; no recipe uses it." % d)
        else:
            dis_ing.add(ing)
    fav = set()
    for f in diet.get("favorites", []):
        rid = f if f in recipe_ids else recipe_by_name.get(f, recipe_by_name.get(str(f).lower()))
        if rid is None:
            errors.append("diet.favorites: unknown recipe %r" % f)
        else:
            fav.add(rid)
    req["disliked_ingredients"], req["disliked_recipes"], req["favorite_recipes"] = (
        sorted(dis_ing), sorted(dis_rec), sorted(fav))

    pantry = {}
    for item in req.get("pantry", []):
        parsed, err = parse_pantry_item(item, catalog)
        if err:
            errors.append(err)
        else:
            pantry[parsed[0]] = pantry.get(parsed[0], 0) + parsed[1]
    req["pantry_map"] = pantry

    coupon_list = []
    for i, c in enumerate(req.get("coupons", [])):
        try:
            coupon_list.append(coupon_mod.normalize_coupon(c, catalog, idx=i))
        except InputError as exc:
            errors.extend(exc.errors)
    for i, text in enumerate(req.get("coupon_texts", [])):
        try:
            coupon_list.append(coupon_mod.parse_coupon_text(text, catalog, idx=i))
        except InputError as exc:
            errors.extend(exc.errors)
    active, expired = [], []
    for c in coupon_list:
        if c.get("valid_until") and c["valid_until"] < req["start_date"].isoformat():
            expired.append(c)
        elif c["store"] != "*" and c["store"] not in resolved:
            expired.append(dict(c, _reason="store not allowed"))
        else:
            active.append(c)
    req["coupon_list"], req["inactive_coupons"] = active, expired

    if req["dishes_per_meal"] not in (1, 2, 3):
        errors.append("dishes_per_meal must be 1, 2 or 3")
    if req["output"].get("lang") not in ("zh", "en"):
        errors.append("output.lang must be 'zh' or 'en'")
    budget = req["budget"].get("weekly")
    if not isinstance(budget, (int, float)) or budget <= 0:
        errors.append("budget.weekly must be a positive number")
    if errors:
        raise InputError(errors)
    return req


def ceil_qty(qty, unit):
    if unit == "pcs":
        return int(math.ceil(qty - 1e-9))
    return int(math.ceil(qty - 1e-6))
