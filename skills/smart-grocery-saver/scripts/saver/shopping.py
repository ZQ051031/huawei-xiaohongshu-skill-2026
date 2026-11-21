"""Pack-size optimisation, coupon application and multi-store basket optimisation."""

import itertools
import math

from . import coupons as cp


def _r2(x):
    return round(x + 1e-9, 2)


class Shopper:
    """Prices a set of ingredient needs across stores, pack sizes and coupons."""

    def __init__(self, catalog, coupons, allowed_stores, extra_store_cost=0.0):
        self.catalog = catalog
        self.coupons = list(coupons)
        self.allowed = list(allowed_stores)
        self.extra_store_cost = float(extra_store_cost or 0)
        self._line_cache = {}

    # ------------------------------------------------------------------ one line
    def line(self, ing, store, qty, use_item_coupons=True):
        """Cheapest way to buy >= qty of ``ing`` at ``store``. None if not sold there."""
        key = (ing, store, qty, use_item_coupons)
        if key in self._line_cache:
            return self._line_cache[key]
        products = sorted(self.catalog.products_for(ing, store), key=lambda p: p["pack_qty"])
        if not products:
            self._line_cache[key] = None
            return None
        applicable = [c for c in self.coupons if cp.applies(c, store, ing, self.catalog)] if use_item_coupons else []
        sets = cp.item_coupon_sets(applicable)
        extra = max([c["get"] for c in applicable if c["type"] == "buy_x_get_y"] or [0])
        best = None
        for cset in sets:
            for counts in self._count_vectors(products, qty, extra):
                cost, list_cost, free_total, packs, bought = 0.0, 0.0, 0, [], 0.0
                for prod, n in zip(products, counts):
                    if n == 0:
                        continue
                    c, free = cp.product_cost(prod["price"], n, cset)
                    cost += c
                    list_cost += n * prod["price"]
                    bought += n * prod["pack_qty"]
                    packs.append({"label": prod["label"], "pack_qty": prod["pack_qty"], "count": n,
                                  "unit_price": prod["price"], "free": free})
                    free_total += free
                cand = (_r2(cost), bought, len(cset), packs, list_cost, cset)
                if best is None or (cand[0], cand[1], cand[2]) < (best[0], best[1], best[2]):
                    best = cand
        cost, bought, _, packs, list_cost, cset = best
        used = [c for c in cset if _uses(c, packs)]
        result = {
            "ingredient": ing, "store": store, "need": qty, "bought_qty": bought,
            "packs": packs, "list_cost": _r2(list_cost), "cost": cost,
            "coupons": [{"id": c["id"], "label": c["label"]} for c in used],
        }
        if not used:
            result["cost"] = _r2(list_cost) if cost >= list_cost - 1e-9 else cost
        self._line_cache[key] = result
        return result

    @staticmethod
    def _count_vectors(products, qty, extra):
        if qty <= 0:
            yield tuple(0 for _ in products)
            return
        bounds = [int(math.ceil(qty / p["pack_qty"])) + extra for p in products]
        seen = set()

        def rec(i, remaining, acc):
            if i == len(products) - 1:
                n = max(0, int(math.ceil(remaining / products[i]["pack_qty"] - 1e-9)))
                for k in range(n, min(bounds[i], n + extra) + 1):
                    vec = tuple(acc + [k])
                    if vec not in seen:
                        seen.add(vec)
                        yield vec
                return
            for k in range(0, bounds[i] + 1):
                yield from rec(i + 1, remaining - k * products[i]["pack_qty"], acc + [k])

        yield from rec(0, qty, [])

    def cheapest_line_cost(self, ing, qty, use_item_coupons=True, stores=None):
        best = None
        for s in stores or self.allowed:
            ln = self.line(ing, s, qty, use_item_coupons)
            if ln is not None and (best is None or ln["cost"] < best):
                best = ln["cost"]
        return best

    # ------------------------------------------------------------------ basket
    def evaluate(self, needs, assignment, use_item_coupons=True, use_order_coupons=True):
        stores = {}
        for ing, store in assignment.items():
            ln = self.line(ing, store, needs[ing], use_item_coupons)
            stores.setdefault(store, []).append(ln)
        summaries = {}
        for store, lines in stores.items():
            subtotal = sum(l["cost"] for l in lines)
            undiscounted = sum(l["cost"] for l in lines if not l["coupons"])
            summaries[store] = {"store": store, "lines": sorted(lines, key=lambda l: l["ingredient"]),
                                "list_subtotal": _r2(sum(l["list_cost"] for l in lines)),
                                "subtotal": _r2(subtotal), "undiscounted": undiscounted,
                                "order_discounts": [], "order_discount": 0.0}
        if use_order_coupons:
            thr = [c for c in self.coupons if c["type"] == "threshold_off"]
            wild = [c for c in thr if c["store"] == "*"]
            for store, s in summaries.items():
                own = [c for c in thr if c["store"] == store]
                d, used = cp.best_order_discount(s["subtotal"], s["undiscounted"], own)
                s["order_discount"], s["_own"], s["_used"] = d, own, used
            for wc in sorted(wild, key=lambda c: -(c.get("amount") or 0)):
                best_gain, best_store, best_res = 0.0, None, None
                for store, s in summaries.items():
                    d, used = cp.best_order_discount(s["subtotal"], s["undiscounted"], s["_own"] + [wc])
                    if d - s["order_discount"] > best_gain + 1e-9:
                        best_gain, best_store, best_res = d - s["order_discount"], store, (d, used)
                if best_store:
                    s = summaries[best_store]
                    s["_own"] = s["_own"] + [wc]
                    s["order_discount"], s["_used"] = best_res
            for s in summaries.values():
                s["order_discounts"] = [{"id": c["id"], "label": c["label"], "amount": a}
                                        for c, a in s.pop("_used", [])]
                s.pop("_own", None)
        total = 0.0
        for store, s in summaries.items():
            meta = self.catalog.stores[store]
            after = s["subtotal"] - s["order_discount"]
            fee = float(meta.get("delivery_fee") or 0)
            free_thr = float(meta.get("free_delivery_threshold") or 0)
            s["delivery_fee"] = _r2(fee) if fee and after > 0 and after + 1e-9 < free_thr else 0.0
            s["order_discount"] = _r2(s["order_discount"])
            s["total"] = _r2(after + s["delivery_fee"])
            s.pop("undiscounted", None)
            total += s["total"]
        total = _r2(total)
        objective = total + self.extra_store_cost * max(0, len(summaries) - 1)
        return {"assignment": dict(assignment), "stores": [summaries[k] for k in sorted(summaries, key=self.allowed.index)],
                "total": total, "objective": _r2(objective),
                "list_total": _r2(sum(s["list_subtotal"] for s in summaries.values()))}

    def optimize(self, needs, max_stores=2, use_item_coupons=True, use_order_coupons=True,
                 stores=None, initial=None):
        """Choose stores and per-ingredient store assignment minimising total spend."""
        needs = {i: q for i, q in needs.items() if q > 0}
        allowed = list(stores or self.allowed)
        sellers = {i: [s for s in allowed if self.line(i, s, needs[i], use_item_coupons) is not None] for i in needs}
        unavailable = sorted(i for i, s in sellers.items() if not s)
        avail = {i: q for i, q in needs.items() if sellers[i]}
        candidates = []
        max_k = max(1, min(max_stores, len(allowed)))
        for k in range(1, max_k + 1):
            for subset in itertools.combinations(allowed, k):
                covered = [i for i in avail if any(s in subset for s in sellers[i])]
                candidates.append((len(avail) - len(covered), subset))
        min_missing = min(c[0] for c in candidates) if candidates else 0
        best = None
        for missing, subset in candidates:
            if missing != min_missing:
                continue
            sub_needs = {i: q for i, q in avail.items() if any(s in subset for s in sellers[i])}
            res = self._local_search(sub_needs, subset, use_item_coupons, use_order_coupons)
            res["_missing"] = sorted(set(avail) - set(sub_needs))
            if best is None or res["objective"] < best["objective"] - 1e-9:
                best = res
        if initial is not None:
            init = {i: s for i, s in initial.items() if i in avail}
            used = set(init.values())
            if (set(init) == set(avail) and len(used) <= max_k and used <= set(allowed)
                    and all(self.line(i, s, avail[i], use_item_coupons) for i, s in init.items())):
                init_res = self._local_search(avail, tuple(sorted(used, key=allowed.index)),
                                              use_item_coupons, use_order_coupons, start=init)
                init_res["_missing"] = []
                if best is None or init_res["objective"] < best["objective"] - 1e-9:
                    best = init_res
        if best is None:
            best = {"assignment": {}, "stores": [], "total": 0.0, "objective": 0.0, "list_total": 0.0, "_missing": []}
        best["unavailable"] = unavailable
        best["not_in_chosen_stores"] = best.pop("_missing")
        return best

    def _local_search(self, needs, subset, use_item, use_order, start=None):
        if start is None:
            start = {}
            for i, q in needs.items():
                opts = [(self.line(i, s, q, use_item), s) for s in subset]
                opts = [(l["cost"], subset.index(s), s) for l, s in opts if l is not None]
                start[i] = min(opts)[2]
        assign = dict(start)
        cur = self.evaluate(needs, assign, use_item, use_order)
        for _ in range(60):
            improved = False
            for i in sorted(needs):
                for s in subset:
                    if s == assign[i] or self.line(i, s, needs[i], use_item) is None:
                        continue
                    trial = dict(assign)
                    trial[i] = s
                    res = self.evaluate(needs, trial, use_item, use_order)
                    if res["objective"] < cur["objective"] - 1e-9:
                        assign, cur, improved = trial, res, True
            if not improved:
                break
        return cur


def _uses(coupon, packs):
    """Whether a coupon actually changed the price of these packs."""
    for p in packs:
        if cp.product_cost(p["unit_price"], p["count"], [coupon])[0] < p["unit_price"] * p["count"] - 1e-9:
            return True
    return False
