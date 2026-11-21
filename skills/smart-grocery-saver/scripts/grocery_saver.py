#!/usr/bin/env python3
"""Smart Grocery Saver CLI (Python 3.10+, standard library only).

Examples:
  python3 grocery_saver.py plan --input ../examples/family_of_three.json --format markdown
  python3 grocery_saver.py plan --input req.json --prices my_prices.csv --out ./out
  echo '{"household":{"adults":2},"budget":{"weekly":150}}' | python3 grocery_saver.py plan --format note
  python3 grocery_saver.py parse-coupons "示例超市A：满100减15" "鸡蛋 买2送1"
  python3 grocery_saver.py export-prices > my_prices.csv
  python3 grocery_saver.py validate --input req.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from saver import __version__  # noqa: E402
from saver.coupons import describe, parse_coupon_text  # noqa: E402
from saver.data import InputError, load_catalog, load_recipes, normalize_input, overrides_from_csv  # noqa: E402
from saver.engine import run  # noqa: E402


def _read_input(path):
    if path in (None, "-"):
        text = sys.stdin.read()
    else:
        text = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as e:
        raise InputError("input is not valid JSON: %s" % e)


def _catalog(args):
    return load_catalog(args.catalog) if getattr(args, "catalog", None) else load_catalog()


def _with_prices(raw, args):
    if getattr(args, "prices", None):
        rows = overrides_from_csv(Path(args.prices).read_text(encoding="utf-8"))
        raw = dict(raw)
        raw["price_overrides"] = list(raw.get("price_overrides", [])) + rows
    return raw


def cmd_plan(args):
    raw = _with_prices(_read_input(args.input), args)
    if args.lang:
        raw.setdefault("output", {})["lang"] = args.lang
    res = run(raw, catalog=_catalog(args))
    note = res["outputs"]["xiaohongshu_note"]
    note_text = note["title"] + "\n\n" + note["body"] + "\n"
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "plan.json").write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (out / "plan.md").write_text(res["outputs"]["markdown"], encoding="utf-8")
        (out / "xiaohongshu_note.txt").write_text(note_text, encoding="utf-8")
        print("wrote %s/plan.json, plan.md, xiaohongshu_note.txt — total ¥%.2f, saved ¥%.2f"
              % (out, res["summary"]["total"], res["summary"]["savings_total"]), file=sys.stderr)
    if args.format == "json":
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.format == "markdown":
        print(res["outputs"]["markdown"], end="")
    elif args.format == "note":
        print(note_text, end="")
    elif args.format == "summary":
        s = res["summary"]
        print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0 if res["summary"]["status"] == "within_budget" or not args.strict_budget else 3


def cmd_parse(args):
    cat = _catalog(args)
    texts = args.texts or [l for l in sys.stdin.read().splitlines() if l.strip()]
    out = []
    for i, t in enumerate(texts):
        c = parse_coupon_text(t, cat, i)
        c["label"] = c.get("label") or describe(c, cat, args.lang)
        out.append(c)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_export(args):
    sys.stdout.write(_catalog(args).to_csv())
    return 0


def cmd_validate(args):
    cat = _catalog(args)
    raw = _with_prices(_read_input(args.input), args)
    req = normalize_input(raw, cat, load_recipes())
    print(json.dumps({"ok": True, "portions_per_meal": req["portions"],
                      "coupons": [c["label"] for c in req["coupon_list"]]}, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="grocery_saver", description="Smart Grocery Saver — weekly meal plan, "
                                "optimised shopping list, coupon stacking and a Xiaohongshu-style note.")
    p.add_argument("--version", action="version", version="smart-grocery-saver " + __version__)
    p.add_argument("--catalog", help="custom catalog JSON (default: data/sample_catalog.json, SAMPLE prices)")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("plan", help="build the weekly plan")
    a.add_argument("--input", "-i", help="request JSON file ('-' or omitted = stdin)")
    a.add_argument("--prices", help="price CSV (from export-prices) with your real prices")
    a.add_argument("--format", "-f", choices=["json", "markdown", "note", "summary"], default="markdown")
    a.add_argument("--out", "-o", help="directory to write plan.json / plan.md / xiaohongshu_note.txt")
    a.add_argument("--lang", choices=["zh", "en"])
    a.add_argument("--strict-budget", action="store_true", help="exit code 3 when over budget")
    a.set_defaults(func=cmd_plan)
    b = sub.add_parser("parse-coupons", help="turn pasted coupon text into structured coupons")
    b.add_argument("texts", nargs="*")
    b.add_argument("--lang", choices=["zh", "en"], default="zh")
    b.set_defaults(func=cmd_parse)
    c = sub.add_parser("export-prices", help="print the price table as CSV for editing")
    c.set_defaults(func=cmd_export)
    d = sub.add_parser("validate", help="validate a request without planning")
    d.add_argument("--input", "-i")
    d.add_argument("--prices")
    d.set_defaults(func=cmd_validate)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except InputError as e:
        print("Input error:\n  " + "\n  ".join(e.errors), file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print("Error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
