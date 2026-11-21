import json
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import _path

CLI = str(_path.SKILL / "scripts" / "grocery_saver.py")
EX = _path.SKILL / "examples"


def cli(*args, stdin=None):
    return subprocess.run([sys.executable, CLI, *args], input=stdin, capture_output=True, text=True, timeout=120)


class CliTests(unittest.TestCase):
    def test_plan_all_examples(self):
        for f in sorted(EX.glob("*.json")):
            p = cli("plan", "-i", str(f), "-f", "json")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("summary", json.loads(p.stdout))

    def test_stdin_and_note(self):
        p = cli("plan", "-f", "note", stdin='{"household":{"adults":1},"budget":{"weekly":80},"start_date":"2026-11-23"}')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("#省钱攻略", p.stdout)

    def test_bad_input_exit_2(self):
        p = cli("plan", stdin='{"budget":{"weekly":0}}')
        self.assertEqual(p.returncode, 2)
        self.assertIn("budget", p.stderr)
        self.assertEqual(cli("plan", stdin="{not json").returncode, 2)

    def test_strict_budget(self):
        p = cli("plan", "--strict-budget", "-f", "summary", stdin='{"budget":{"weekly":5},"start_date":"2026-11-23"}')
        self.assertEqual(p.returncode, 3)

    def test_price_csv_roundtrip(self):
        import tempfile
        csv_text = cli("export-prices").stdout
        self.assertTrue(csv_text.startswith("store,ingredient"))
        lines = csv_text.splitlines()
        edited = [lines[0]] + [l for l in lines[1:] if not l.startswith("market_a,egg,")] + \
                 ["market_a,egg,鸡蛋,Eggs,pcs,10,0.10,cheap eggs"]
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as fh:
            fh.write("\n".join(edited) + "\n")
        p = cli("plan", "-i", str(EX / "family_of_three.json"), "--prices", fh.name, "-f", "json")
        self.assertEqual(p.returncode, 0, p.stderr)
        res = json.loads(p.stdout)
        eggs = [i for s in res["shopping_list"]["stores"] for i in s["items"] if i["ingredient"] == "egg"]
        self.assertTrue(eggs and eggs[0]["packs"][0]["unit_price"] == 0.1)

    def test_parse_coupons(self):
        p = cli("parse-coupons", "示例超市A：满100减15", "鸡蛋 买2送1")
        self.assertEqual([c["type"] for c in json.loads(p.stdout)], ["threshold_off", "buy_x_get_y"])

    def test_validate(self):
        p = cli("validate", "-i", str(EX / "family_of_three.json"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(json.loads(p.stdout)["ok"])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(_path.ROOT / "demo"))
        import server
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data), timeout=60) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_index_and_health(self):
        self.assertEqual(self.req("/")[0], 200)
        self.assertEqual(json.loads(self.req("/api/health")[1]), {"ok": True})

    def test_plan(self):
        ex = json.loads(self.req("/api/example")[1])
        code, body = self.req("/api/plan", {"request": ex})
        self.assertEqual(code, 200)
        self.assertIn("xiaohongshu_note", json.loads(body)["outputs"])

    def test_plan_error(self):
        code, body = self.req("/api/plan", {"request": {"budget": {"weekly": -1}}})
        self.assertEqual(code, 400)
        self.assertTrue(json.loads(body)["errors"])

    def test_parse_coupons(self):
        code, body = self.req("/api/parse-coupons", {"texts": ["满59减8"]})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)[0]["type"], "threshold_off")

    def test_catalog(self):
        code, body = self.req("/api/catalog")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(len(data["stores"]), 3)
        self.assertTrue(any(i["id"] == "egg" for i in data["ingredients"]))

    def test_404(self):
        self.assertEqual(self.req("/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
