"""Weekly meal-plan search: diet-safe, varied, and cheapest given packs, pantry and coupons."""

import math

from .data import ceil_qty, recipe_tags

PROTEINS = ("beef", "shellfish", "fish", "pork", "chicken", "egg", "soy")
MEALS = ("breakfast", "lunch", "dinner")


def build_slots(req):
    slots = []
    roles_by_dishes = {1: ["main"], 2: ["main", "side"], 3: ["main", "side", "soup"]}
    for day in range(1, 8):
        for meal in MEALS:
            if day not in req["meal_days"][meal]:
                continue
            roles = ["breakfast"] if meal == "breakfast" else roles_by_dishes[req["dishes_per_meal"]]
            for role in roles:
                slots.append({"day": day, "meal": meal, "role": role})
    return slots


def main_protein(recipe, catalog):
    tags = recipe_tags(recipe, catalog)
    for p in PROTEINS:
        if p in tags:
            return p
    return "veg"


class Planner:
    def __init__(self, req, catalog, recipes, shopper):
        self.req, self.catalog, self.shopper = req, catalog, shopper
        self.portions = req["portions"]
        self.recipes = {r["id"]: r for r in recipes}
        self.staple = next((r for r in recipes if r["role"] == "staple"), None)
        self.slots = build_slots(req)
        self.warnings = []
        forbidden = set(req["forbidden_tags"])
        disliked_ing = set(req["disliked_ingredients"])
        disliked_rec = set(req["disliked_recipes"])
        pantry = req["pantry_map"]
        self.eligible = {}
        self.excluded = {}
        for r in recipes:
            if r["role"] == "staple":
                continue
            reason = None
            tags = recipe_tags(r, catalog)
            if tags & forbidden:
                reason = "diet: " + ",".join(sorted(tags & forbidden))
            elif r["id"] in disliked_rec:
                reason = "disliked recipe"
            elif set(r["ingredients"]) & disliked_ing:
                reason = "disliked ingredient"
            else:
                for ing in r["ingredients"]:
                    if ing not in catalog.ingredients:
                        reason = "unknown ingredient %s" % ing
                        break
                    if self._is_assumed_staple(ing) or pantry.get(ing, 0) > 0:
                        continue
                    if not catalog.stores_selling(ing) or not set(catalog.stores_selling(ing)) & set(req["stores"]["allowed"]):
                        reason = "%s not sold in allowed stores" % ing
                        break
            if reason:
                self.excluded[r["id"]] = reason
            else:
                self.eligible.setdefault(r["role"], []).append(r["id"])
        for role in self.eligible:
            self.eligible[role].sort()
        missing_roles = sorted({s["role"] for s in self.slots} - set(self.eligible))
        if missing_roles:
            self.warnings.append("No recipe satisfies the diet for: %s - those dishes were dropped." % ", ".join(missing_roles))
            self.slots = [s for s in self.slots if s["role"] in self.eligible]
        self.protein = {rid: main_protein(self.recipes[rid], catalog) for rid in self.recipes}
        self.staple_portions = sum(1 for d in range(1, 8) for m in ("lunch", "dinner") if d in req["meal_days"][m]) * self.portions
        self._cost_cache = {}

    def _is_assumed_staple(self, ing):
        return self.req["assume_staples"] and self.catalog.ingredients[ing].get("pantry_staple")

    # ------------------------------------------------------------------ quantities
    def consumption(self, plan):
        need = {}
        for rid in plan:
            if rid is None:
                continue
            for ing, q in self.recipes[rid]["ingredients"].items():
                need[ing] = need.get(ing, 0.0) + q * self.portions
        if self.staple and self.staple_portions:
            for ing, q in self.staple["ingredients"].items():
                need[ing] = need.get(ing, 0.0) + q * self.staple_portions
        return need

    def purchase_needs(self, consumption, use_pantry=True):
        out, staples = {}, []
        for ing, q in consumption.items():
            if self._is_assumed_staple(ing):
                staples.append(ing)
                continue
            if use_pantry:
                q -= self.req["pantry_map"].get(ing, 0)
            q = ceil_qty(q, self.catalog.unit(ing))
            if q > 0:
                out[ing] = q
        return out, sorted(staples)

    # ------------------------------------------------------------------ objective
    def cost(self, plan, variety_weight):
        needs, _ = self.purchase_needs(self.consumption(plan))
        money = 0.0
        for ing, q in needs.items():
            c = self.shopper.cheapest_line_cost(ing, q)
            money += c if c is not None else 1e4
        return money + self.penalty(plan, variety_weight)

    def penalty(self, plan, w):
        if w <= 0 and not self.req["favorite_recipes"]:
            return 0.0
        counts, prot = {}, {}
        for rid, slot in zip(plan, self.slots):
            if rid is None:
                continue
            counts[rid] = counts.get(rid, 0) + 1
            if slot["role"] == "main":
                p = self.protein[rid]
                prot[p] = prot.get(p, 0) + 1
        n_main = sum(1 for s in self.slots if s["role"] == "main")
        cap = max(1, math.ceil(n_main / 3.0))
        pen = 0.0
        pen += w * sum(max(0, n - cap) for n in prot.values())
        pen += 0.5 * w * sum(n - 1 for n in counts.values())
        fav_bonus = max(w, 1.0) * 1.5
        allowed_meat = [p for p in ("pork", "beef", "chicken", "fish", "shellfish")
                        if any(self.protein[r] == p for r in self.eligible.get("main", []))]
        if allowed_meat:
            ratio = float(self.req["variety"].get("min_meat_ratio", 0.4))
            want = int(math.floor(n_main * ratio + 1e-9))
            have = sum(prot.get(p, 0) for p in allowed_meat)
            pen += 2.0 * w * max(0, want - have)
        pen -= fav_bonus * sum(1 for rid in self.req["favorite_recipes"] if counts.get(rid))
        return pen

    def valid(self, plan, i, rid, max_repeats):
        slot = self.slots[i]
        count = sum(1 for j, r in enumerate(plan) if r == rid and j != i)
        if count >= max_repeats:
            return False
        for j, r in enumerate(plan):
            if j == i or r != rid:
                continue
            other = self.slots[j]
            if other["role"] == slot["role"] and abs(other["day"] - slot["day"]) <= 1:
                return False
        return True

    # ------------------------------------------------------------------ search
    def solve(self, variety_weight=None, max_repeats=None):
        w = self.req["variety"].get("variety_weight", 3.0) if variety_weight is None else variety_weight
        mr = int(self.req["variety"].get("max_repeats", 2) if max_repeats is None else max_repeats)
        for role, ids in self.eligible.items():
            n = sum(1 for s in self.slots if s["role"] == role)
            need = int(math.ceil(n / float(max(1, len(ids)))))
            if need > mr:
                self.warnings.append("Only %d %s recipes fit your diet; allowing up to %d repeats." % (len(ids), role, need))
                mr = need
        plan = [None] * len(self.slots)
        relaxed = False
        for i, slot in enumerate(self.slots):
            cands = [r for r in self.eligible[slot["role"]] if self.valid(plan, i, r, mr)]
            if not cands:
                cands, relaxed = list(self.eligible[slot["role"]]), True
            plan[i] = min(cands, key=lambda r: (self._try(plan, i, r, w), r))
        if relaxed:
            self.warnings.append("Variety rules were relaxed because too few recipes fit your diet.")
        cur = self.cost(plan, w)
        for _ in range(8):
            improved = False
            for i, slot in enumerate(self.slots):
                for r in self.eligible[slot["role"]]:
                    if r == plan[i] or not self.valid(plan, i, r, mr):
                        continue
                    c = self._try(plan, i, r, w)
                    if c < cur - 1e-9:
                        plan[i], cur, improved = r, c, True
            if not improved:
                break
        return plan

    def _try(self, plan, i, rid, w):
        trial = list(plan)
        trial[i] = rid
        return self.cost(trial, w)
