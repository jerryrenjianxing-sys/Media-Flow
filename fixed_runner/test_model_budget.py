import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from model_budget import ModelBudget, ModelBudgetError, budgeted_post


class ModelBudgetTests(unittest.TestCase):
    def test_concurrent_reservations_and_restart_do_not_expand_cap(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "budget.db"
            budget = ModelBudget(path)
            def reserve(_):
                try:
                    return budget.reserve("test", 1)
                except ModelBudgetError:
                    return None
            with ThreadPoolExecutor(max_workers=8) as pool:
                tokens = [token for token in pool.map(reserve, range(12)) if token]
            self.assertEqual(len(tokens), 5)
            self.assertEqual(ModelBudget(path).summary()["committed_usd"], 5)
            budget.settle(tokens[0], .1)
            self.assertAlmostEqual(budget.summary()["committed_usd"], 4.1)
            with self.assertRaises(ModelBudgetError):
                budget.reserve("test", 1)

    def test_pricing_deadline_prevents_late_paid_request(self):
        with tempfile.TemporaryDirectory() as root:
            budget = ModelBudget(Path(root) / "budget.db")
            token = budget.reserve("test", 1)
            with patch("model_budget._reservation", return_value=(budget, token)), patch("model_budget.requests.post") as post:
                with self.assertRaisesRegex(ModelBudgetError, "deadline"):
                    with budgeted_post("https://openrouter.ai/api/v1/chat/completions", json={}, request_deadline=time.monotonic() - 1):
                        pass
                post.assert_not_called()
                self.assertEqual(budget.summary()["committed_usd"], 0)

    def test_unknown_charge_retained_and_final_cost_only_settled_after_read(self):
        with tempfile.TemporaryDirectory() as root:
            budget = ModelBudget(Path(root) / "budget.db")
            token = budget.reserve("test", 1)
            response = MagicMock()
            response.__enter__.return_value = response
            response.iter_lines.return_value = [b'data: {"usage":{"cost":0}}', b'data: {"usage":{"cost":0.12}}']
            with patch("model_budget._reservation", return_value=(budget, token)), patch("model_budget.requests.post", return_value=response):
                with budgeted_post("https://openrouter.ai/api/v1/chat/completions", json={}) as stream:
                    list(stream.iter_lines())
                    self.assertEqual(budget.summary()["committed_usd"], 1)
            self.assertAlmostEqual(budget.summary()["settled_usd"], .12)

    def test_upstream_price_ceiling_is_sent(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"MEDIAFLOW_MODEL_BUDGET_PATH": str(Path(root) / 'budget.db')}):
            catalogue = MagicMock()
            catalogue.json.return_value = {"data": [{"id": "test", "context_length": 1000,
                "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]}
            response = MagicMock()
            response.__enter__.return_value = response
            with patch("model_budget.requests.get", return_value=catalogue), patch("model_budget.requests.post", return_value=response) as post:
                with budgeted_post("https://openrouter.ai/api/v1/chat/completions", data=json.dumps({"model": "test", "messages": []})):
                    pass
            payload = json.loads(post.call_args.kwargs["data"])
            self.assertEqual(payload["provider"]["max_price"]["completion"], 2)
            self.assertFalse(payload["provider"]["allow_fallbacks"])


if __name__ == "__main__":
    unittest.main()
