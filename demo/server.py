#!/usr/bin/env python3
"""Local web demo for Smart Grocery Saver (standard library only).

    python3 demo/server.py            # http://127.0.0.1:8765
    python3 demo/server.py --port 9000 --host 0.0.0.0
"""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL = ROOT.parent / "skills" / "smart-grocery-saver"
sys.path.insert(0, str(SKILL / "scripts"))

from saver.coupons import describe, parse_coupon_text  # noqa: E402
from saver.data import InputError, load_catalog, overrides_from_csv  # noqa: E402
from saver.engine import run  # noqa: E402

MAX_BODY = 512 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "SmartGrocerySaver/1.0"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def log_message(self, fmt, *args):
        sys.stderr.write("[demo] " + fmt % args + "\n")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/health":
            return self._json(200, {"ok": True})
        if path == "/api/example":
            return self._send(200, (SKILL / "examples" / "family_of_three.json").read_bytes())
        if path == "/api/prices.csv":
            return self._send(200, load_catalog().to_csv(), "text/csv; charset=utf-8")
        if path == "/api/catalog":
            cat = load_catalog()
            return self._json(200, {"notice": cat.raw.get("_notice_zh"), "stores": cat.raw["stores"],
                                    "ingredients": [{"id": k, "name_zh": v["name_zh"], "name_en": v["name_en"],
                                                     "unit": v["unit"]} for k, v in cat.ingredients.items()]})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json(413, {"error": "request too large"})
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError) as e:
            return self._json(400, {"errors": ["invalid JSON: %s" % e]})
        try:
            if path == "/api/plan":
                raw = body.get("request", body) if isinstance(body, dict) else body
                if isinstance(body, dict) and body.get("prices_csv"):
                    raw = dict(raw)
                    raw["price_overrides"] = list(raw.get("price_overrides", [])) + overrides_from_csv(body["prices_csv"])
                return self._json(200, run(raw))
            if path == "/api/parse-coupons":
                cat = load_catalog()
                out = []
                for i, t in enumerate(body.get("texts", [])):
                    c = parse_coupon_text(t, cat, i)
                    c["label"] = describe(c, cat)
                    out.append(c)
                return self._json(200, out)
        except InputError as e:
            return self._json(400, {"errors": e.errors})
        return self._json(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    httpd = ThreadingHTTPServer((a.host, a.port), Handler)
    print("Smart Grocery Saver demo: http://%s:%d" % (a.host, a.port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
