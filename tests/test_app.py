import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["PAIRSETTLE_DB"] = _tmp.name

import app as app_module  # noqa: E402


class PairSettleTest(unittest.TestCase):
    def setUp(self):
        app_module.init_db()
        db = app_module.app.test_client()
        self.client = db
        with app_module.app.app_context():
            conn = app_module.get_db()
            conn.execute("DELETE FROM expenses")
            conn.execute("DELETE FROM settings")
            conn.commit()

    def test_healthz(self):
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["status"], "ok")

    def test_index_ok(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("ふたり精算ノート", res.get_data(as_text=True))

    def test_default_names(self):
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("Aさん", body)
        self.assertIn("Bさん", body)

    def test_half_split_settlement(self):
        self.client.post("/expenses", data={"payer": "1", "amount": "1000", "memo": "買い物", "split": "half"})
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("Bさん → Aさん へ ¥500", body)

    def test_ratio_split(self):
        # 7:3 で払った人(1)が7割負担 -> 相手の負担は 1000*0.3=300
        self.client.post("/expenses", data={"payer": "1", "amount": "1000", "memo": "外食", "split": "7:3"})
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("¥300", body)

    def test_other_full(self):
        self.client.post("/expenses", data={"payer": "2", "amount": "800", "memo": "立替", "split": "other_full"})
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("Aさん → Bさん へ ¥800", body)

    def test_offset_to_zero(self):
        self.client.post("/expenses", data={"payer": "1", "amount": "1000", "memo": "a", "split": "half"})
        self.client.post("/expenses", data={"payer": "2", "amount": "1000", "memo": "b", "split": "half"})
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("精算は不要です", body)

    def test_delete_expense(self):
        self.client.post("/expenses", data={"payer": "1", "amount": "500", "memo": "削除対象", "split": "half"})
        with app_module.app.app_context():
            conn = app_module.get_db()
            eid = conn.execute("SELECT id FROM expenses").fetchone()["id"]
        self.client.post(f"/expenses/{eid}/delete")
        body = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("削除対象", body)

    def test_save_names(self):
        self.client.post("/settings", data={"member1": "太郎", "member2": "花子"})
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("太郎", body)
        self.assertIn("花子", body)


if __name__ == "__main__":
    unittest.main()
