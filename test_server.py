import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import server


MODEL = "deepseek/deepseek-v4-flash"


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.originals = {
            "DB_PATH": server.DB_PATH,
            "AI_REQUEST_LOG_PATH": server.AI_REQUEST_LOG_PATH,
            "PROVIDER_BLOCKLIST_PATH": server.PROVIDER_BLOCKLIST_PATH,
            "SCORING_WORKER_LOG_PATH": server.SCORING_WORKER_LOG_PATH,
            "call_openrouter": server.call_openrouter,
            "scoring_concurrency": server.scoring_concurrency,
            "start_scoring_worker_process": server.start_scoring_worker_process,
            "OPENROUTER_KEY": os.environ.get("OPENROUTER_KEY"),
        }
        server.DB_PATH = self.root / "test_companies.db"
        server.AI_REQUEST_LOG_PATH = self.root / "ai_requests.json"
        server.PROVIDER_BLOCKLIST_PATH = self.root / "provider_blocklist.json"
        server.SCORING_WORKER_LOG_PATH = self.root / "scoring_worker.log"
        os.environ["OPENROUTER_KEY"] = "test-key"
        self.create_companies_table()
        server.ensure_scoring_schema()

    def tearDown(self):
        server.DB_PATH = self.originals["DB_PATH"]
        server.AI_REQUEST_LOG_PATH = self.originals["AI_REQUEST_LOG_PATH"]
        server.PROVIDER_BLOCKLIST_PATH = self.originals["PROVIDER_BLOCKLIST_PATH"]
        server.SCORING_WORKER_LOG_PATH = self.originals["SCORING_WORKER_LOG_PATH"]
        server.call_openrouter = self.originals["call_openrouter"]
        server.scoring_concurrency = self.originals["scoring_concurrency"]
        server.start_scoring_worker_process = self.originals["start_scoring_worker_process"]
        if self.originals["OPENROUTER_KEY"] is None:
            os.environ.pop("OPENROUTER_KEY", None)
        else:
            os.environ["OPENROUTER_KEY"] = self.originals["OPENROUTER_KEY"]
        self.temp_dir.cleanup()

    def connect(self):
        connection = sqlite3.connect(server.DB_PATH)
        connection.row_factory = sqlite3.Row
        return connection

    def create_companies_table(self):
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE companies (
                    ticker TEXT PRIMARY KEY,
                    rank INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    market_cap TEXT NOT NULL,
                    market_cap_value INTEGER NOT NULL,
                    price TEXT NOT NULL,
                    today TEXT NOT NULL,
                    country TEXT NOT NULL,
                    logo TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            now = int(time.time())
            rows = [
                ("AAA", 1, "Alpha", "$ 3.00 T", 3_000_000_000_000, "$300", "1.0%", "USA", "https://logos/aaa.png"),
                ("BBB", 2, "Beta", "$ 2.00 T", 2_000_000_000_000, "$200", "0.0%", "USA", "https://logos/bbb.png"),
                ("CCC", 3, "Gamma", "$ 1.00 T", 1_000_000_000_000, "$100", "-1.0%", "UK", "https://logos/ccc.png"),
            ]
            connection.executemany(
                """
                INSERT INTO companies (
                    ticker, rank, name, market_cap, market_cap_value, price, today,
                    country, logo, source_url, fetched_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [row + (str(server.SOURCE_URL), now, now) for row in rows],
            )

    def create_run(self, company_count=3):
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO scoring_runs (name, prompt, model, status, company_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("Test Run", "Score COMPANY", MODEL, "queued", company_count, int(time.time())),
            )
            connection.commit()
            return cursor.lastrowid


class PromptAndParsingTests(ServerTestCase):
    def test_http_response_deadline_interrupts_a_stalled_read(self):
        class StalledResponse:
            def __init__(self):
                self.closed = threading.Event()

            def read(self):
                self.closed.wait(1)
                raise OSError("response closed")

            def close(self):
                self.closed.set()

        started_at = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "attempt limit"):
            server.read_http_response_with_deadline(StalledResponse(), 0.02)
        self.assertLess(time.monotonic() - started_at, 0.5)

    def test_run_stats_include_average_response_tokens_without_reasoning(self):
        entries = [
            {
                "run_id": 7,
                "response": {"success": True},
                "token_stats": {
                    "completion_tokens": 120,
                    "completion_tokens_details": {"reasoning_tokens": 20},
                },
            },
            {
                "run_id": 7,
                "response": {"success": False},
                "token_stats": {
                    "completion_tokens": 80,
                    "completion_tokens_details": {"reasoning_tokens": 0},
                },
            },
            {
                "run_id": 8,
                "response": {"success": True},
                "token_stats": {"completion_tokens": 999},
            },
        ]

        with mock.patch.object(server, "ai_request_entries", return_value=entries):
            stats = server.ai_request_stats_for_run(7)

        self.assertEqual(stats["response_tokens"], 180)
        self.assertEqual(stats["average_response_tokens"], 90.0)

    def test_request_stats_by_ticker_include_duration(self):
        entries = [
            {
                "run_id": 7,
                "company": {"ticker": "AAA"},
                "response": {"success": True},
                "timing": {"duration_ms": 18425},
                "token_stats": {"prompt_tokens": 10, "completion_tokens": 20},
            }
        ]

        with mock.patch.object(server, "ai_request_entries", return_value=entries):
            stats = server.ai_request_stats_by_ticker(7)

        self.assertEqual(stats["AAA"]["duration_ms"], 18425)

    def test_active_company_universe_uses_latest_fetch(self):
        with self.connect() as connection:
            latest_fetch = connection.execute("SELECT MAX(fetched_at) FROM companies").fetchone()[0]
            connection.execute(
                """
                INSERT INTO companies (
                    ticker, rank, name, market_cap, market_cap_value, price, today,
                    country, logo, source_url, fetched_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "OLD",
                    1,
                    "Old Company",
                    "$ 4.00 T",
                    4_000_000_000_000,
                    "$400",
                    "0.0%",
                    "USA",
                    "https://logos/old.png",
                    str(server.SOURCE_URL),
                    latest_fetch - 1,
                    latest_fetch - 1,
                ),
            )
            connection.commit()

        self.assertEqual([company["ticker"] for company in server.db_companies()], ["AAA", "BBB", "CCC"])
        self.assertEqual(server.companies_for_tickers(["OLD"])[0]["name"], "Old Company")

    def test_prompt_for_company_includes_name_and_ticker(self):
        prompt = server.prompt_for_company(
            "Rate COMPANY from 0 to 100",
            {"name": "NVIDIA", "ticker": "NVDA"},
        )
        self.assertEqual(prompt, "Rate NVIDIA (ticker: NVDA) from 0 to 100")

    def test_parse_companies_extracts_absolute_logo_url(self):
        html = """
        <table>
          <tr>
            <td class="rank-td">1</td>
            <td>
              <img class="company-logo" src="/img/company-logos/64/NVDA.png">
              <div class="company-name">NVIDIA</div>
              <div class="company-code">NVDA</div>
            </td>
            <td></td>
            <td>$ 5.358 T</td>
            <td>$221.24</td>
            <td><span>1.23%</span></td>
            <td>USA</td>
          </tr>
        </table>
        """
        companies = server._parse_companies(html)
        self.assertEqual(companies[0]["ticker"], "NVDA")
        self.assertEqual(companies[0]["logo"], "https://companiesmarketcap.com/img/company-logos/64/NVDA.png")

    def test_parse_numeric_score_uses_last_number(self):
        score = server.parse_numeric_score(
            "The company has 3 strong product lines and roughly 20 major partners.\nScore: 87"
        )
        self.assertEqual(score, 87)

    def test_parse_numeric_score_supports_decimal_at_end(self):
        score = server.parse_numeric_score("Moat looks above average. Final score: 82.5")
        self.assertEqual(score, 82.5)

    def test_parse_numeric_score_raises_when_no_number(self):
        with self.assertRaises(ValueError):
            server.parse_numeric_score("No numeric score provided")

    def test_response_token_limit_validation(self):
        self.assertEqual(server.normalize_max_tokens("750"), 750)
        with self.assertRaisesRegex(ValueError, "1 to 32,768"):
            server.normalize_max_tokens(0)
        with self.assertRaisesRegex(ValueError, "whole number"):
            server.normalize_max_tokens("many")

    def test_call_openrouter_surfaces_embedded_provider_error(self):
        payload = {
            "error": {
                "code": 502,
                "message": "Upstream error from Nvidia: ResourceExhausted",
            }
        }

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        company = server.scoring_companies(1)[0]
        with mock.patch.dict(os.environ, {"OPENROUTER_MAX_ATTEMPTS": "1"}):
            with mock.patch.object(server.urllib.request, "urlopen", return_value=FakeResponse()) as urlopen:
                with self.assertRaisesRegex(RuntimeError, "ResourceExhausted"):
                    server.call_openrouter("Score COMPANY", company, MODEL, run_id=1)

        request_headers = {key.lower(): value for key, value in urlopen.call_args.args[0].header_items()}
        self.assertEqual(request_headers["x-openrouter-cache"], "true")
        self.assertEqual(request_headers["x-openrouter-cache-ttl"], "86400")
        entry = json.loads(server.AI_REQUEST_LOG_PATH.read_text())[-1]
        self.assertEqual(entry["response"]["raw_payload"], payload)
        self.assertIn("ResourceExhausted", entry["response"]["error"]["message"])

    def test_call_openrouter_logs_response_cache_hit(self):
        miss_payload = {
            "choices": [{"message": {"content": "77"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 7,
                "total_tokens": 12,
                "cost": 0.01,
                "cost_details": {"upstream_inference_cost": 0.009},
            },
        }
        hit_payload = {
            "choices": [{"message": {"content": "77"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0},
        }

        class FakeResponse:
            status = 200

            def __init__(self, payload, status):
                self.payload = payload
                self.headers = {
                    "X-OpenRouter-Cache-Status": status,
                    "X-OpenRouter-Cache-Age": "12" if status == "HIT" else None,
                    "X-OpenRouter-Cache-TTL": "86388",
                }

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        company = server.scoring_companies(1)[0]
        responses = [FakeResponse(miss_payload, "MISS"), FakeResponse(hit_payload, "HIT")]
        with mock.patch.object(server.urllib.request, "urlopen", side_effect=responses):
            server.call_openrouter("Score COMPANY", company, MODEL, run_id=1)
            response = server.call_openrouter("Score COMPANY", company, MODEL, run_id=2)

        self.assertEqual(response, "77")
        raw_entry = json.loads(server.AI_REQUEST_LOG_PATH.read_text())[-1]
        self.assertEqual(raw_entry["response"]["cache"]["status"], "HIT")
        self.assertEqual(raw_entry["token_stats"]["total_tokens"], 0)

        entry = server.find_ai_request_entry(2, company["ticker"])
        self.assertEqual(entry["token_stats"]["prompt_tokens"], 5)
        self.assertEqual(entry["token_stats"]["completion_tokens"], 7)
        self.assertEqual(entry["token_stats"]["total_tokens"], 12)
        self.assertEqual(entry["token_stats"]["cost"], 0)
        self.assertEqual(entry["token_stats"]["cost_details"]["upstream_inference_cost"], 0)
        self.assertTrue(entry["token_stats"]["cache_reused_token_counts"])
        self.assertEqual(entry["response"]["cache"]["token_stats_source"], "matched_prior_request")

        stats = server.ai_request_stats_for_run(2)
        self.assertEqual(stats["total_tokens"], 12)
        self.assertEqual(stats["response_tokens"], 7)
        self.assertEqual(stats["cost"], 0)

    def test_call_openrouter_rejects_token_limited_response(self):
        payload = {
            "choices": [
                {
                    "message": {"content": "The company has 1 strong product line, but the analysis"},
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 2000, "total_tokens": 2012},
        }

        class FakeResponse:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        company = server.scoring_companies(1)[0]
        with mock.patch.object(server.urllib.request, "urlopen", return_value=FakeResponse()):
            with self.assertRaisesRegex(ValueError, "token limit"):
                server.call_openrouter("Score COMPANY", company, MODEL, run_id=1)

        entry = json.loads(server.AI_REQUEST_LOG_PATH.read_text())[-1]
        self.assertFalse(entry["response"]["success"])
        self.assertEqual(entry["response"]["finish_reason"], "length")
        self.assertEqual(entry["response"]["visible_content"], payload["choices"][0]["message"]["content"])

    def test_historical_token_limited_score_is_invalidated(self):
        run_id = self.create_run(company_count=1)
        company = server.scoring_companies(1)[0]
        with self.connect() as connection:
            server.save_result(connection, run_id, company, 1, "Analysis starts with 1", None)
            server.update_run_counts(connection, run_id)
            connection.commit()
        server.AI_REQUEST_LOG_PATH.write_text(
            json.dumps(
                [
                    {
                        "run_id": run_id,
                        "company": {"ticker": company["ticker"]},
                        "response": {"finish_reason": "length"},
                    }
                ]
            )
        )

        self.assertEqual(server.invalidate_truncated_scoring_results(), 1)
        with self.connect() as connection:
            result = connection.execute(
                "SELECT score, error FROM scoring_results WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            run = connection.execute(
                "SELECT completed_count, failed_count FROM scoring_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        self.assertIsNone(result["score"])
        self.assertEqual(result["error"], server.TOKEN_LIMIT_ERROR)
        self.assertEqual((run["completed_count"], run["failed_count"]), (0, 1))


class RunWorkerTests(ServerTestCase):
    def test_run_can_be_starred_and_unstarred(self):
        run_id = self.create_run(company_count=1)

        starred = server.update_scoring_run(run_id, starred=True)
        self.assertEqual(starred["starred"], 1)
        listed = next(run for run in server.list_runs() if run["id"] == run_id)
        self.assertEqual(listed["starred"], 1)

        unstarred = server.update_scoring_run(run_id, starred=False)
        self.assertEqual(unstarred["starred"], 0)

        with self.assertRaisesRegex(ValueError, "true or false"):
            server.update_scoring_run(run_id, starred="yes")

    def test_custom_stock_list_run_uses_saved_members_in_order(self):
        stock_list = server.save_stock_list("Focused", ["CCC", "AAA"])
        server.start_scoring_worker_process = lambda run_id, start_index=0: 12345
        run_id = server.create_scoring_run(
            "Custom Run",
            "Score COMPANY",
            MODEL,
            reasoning_mode="none",
            stock_list_id=stock_list["id"],
            max_tokens=777,
        )

        run = server.get_run(run_id)
        self.assertEqual(run["stock_list_name"], "Focused")
        self.assertEqual(run["company_count"], 2)
        self.assertEqual(run["company_tickers"], ["CCC", "AAA"])
        self.assertEqual(run["max_tokens"], 777)

        calls = []

        def fake_call_openrouter(prompt, company, model, reasoning_mode=None, run_id=None, max_tokens=None):
            calls.append((company["ticker"], max_tokens))
            return "50"

        server.call_openrouter = fake_call_openrouter
        server.scoring_concurrency = lambda: 1
        server.score_run_worker(run_id)

        self.assertEqual(calls, [("CCC", 777), ("AAA", 777)])

    def test_custom_stock_list_run_can_limit_members(self):
        stock_list = server.save_stock_list("Focused", ["CCC", "AAA", "BBB"])
        server.start_scoring_worker_process = lambda run_id, start_index=0: 12345
        run_id = server.create_scoring_run(
            "Partial Custom Run",
            "Score COMPANY",
            MODEL,
            company_count=2,
            reasoning_mode="none",
            stock_list_id=stock_list["id"],
        )

        run = server.get_run(run_id)
        self.assertEqual(run["company_count"], 2)
        self.assertEqual(run["company_tickers"], ["CCC", "AAA"])

    def test_custom_stock_list_run_can_extend_to_remaining_members(self):
        stock_list = server.save_stock_list("Focused", ["CCC", "AAA", "BBB"])
        server.start_scoring_worker_process = lambda run_id, start_index=0: 12345
        run_id = server.create_scoring_run(
            "Partial Custom Run",
            "Score COMPANY",
            MODEL,
            company_count=2,
            reasoning_mode="none",
            stock_list_id=stock_list["id"],
        )
        with self.connect() as connection:
            connection.execute("UPDATE scoring_runs SET status = 'completed' WHERE id = ?", (run_id,))
            connection.commit()

        server.save_stock_list("Focused", ["BBB", "AAA", "CCC"], stock_list["id"])
        before = server.get_run(run_id)
        extended = server.extend_scoring_run(run_id, 3)

        self.assertEqual(before["extension_limit"], 3)
        self.assertEqual(extended["company_count"], 3)
        self.assertEqual(extended["company_tickers"], ["CCC", "AAA", "BBB"])

    def test_score_worker_fills_only_incomplete_companies(self):
        run_id = self.create_run(company_count=3)
        with self.connect() as connection:
            server.save_result(
                connection,
                run_id,
                server.scoring_companies(1)[0],
                88,
                "88",
                None,
            )
            server.update_run_counts(connection, run_id)
            connection.commit()

        calls = []

        def fake_call_openrouter(prompt, company, model, reasoning_mode=None, run_id=None, max_tokens=None):
            calls.append((company["ticker"], prompt))
            return {"BBB": "42", "CCC": "55"}[company["ticker"]]

        server.call_openrouter = fake_call_openrouter
        server.scoring_concurrency = lambda: 1
        server.score_run_worker(run_id)

        with self.connect() as connection:
            run = connection.execute("SELECT status, completed_count, failed_count FROM scoring_runs WHERE id = ?", (run_id,)).fetchone()
            results = connection.execute(
                "SELECT ticker, score FROM scoring_results WHERE run_id = ? ORDER BY ticker",
                (run_id,),
            ).fetchall()

        self.assertEqual([ticker for ticker, _prompt in calls], ["BBB", "CCC"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["completed_count"], 3)
        self.assertEqual(run["failed_count"], 0)
        self.assertEqual([(row["ticker"], row["score"]) for row in results], [("AAA", 88), ("BBB", 42), ("CCC", 55)])

    def test_get_run_includes_company_logos(self):
        run_id = self.create_run(company_count=1)
        with self.connect() as connection:
            server.save_result(connection, run_id, server.scoring_companies(1)[0], 90, "90", None)
            server.update_run_counts(connection, run_id)
            connection.commit()

        run = server.get_run(run_id)
        self.assertEqual(run["results"][0]["logo"], "https://logos/aaa.png")


class StockListTests(ServerTestCase):
    def test_stock_list_can_be_created_and_updated(self):
        stock_list = server.save_stock_list("Semis", ["AAA", "CCC"])
        self.assertEqual(stock_list["name"], "Semis")
        self.assertEqual([company["ticker"] for company in stock_list["companies"]], ["AAA", "CCC"])

        updated = server.save_stock_list("Semis Plus", ["BBB", "AAA"], stock_list["id"])
        self.assertEqual(updated["name"], "Semis Plus")
        self.assertEqual([company["ticker"] for company in updated["companies"]], ["BBB", "AAA"])


class CostEstimateTests(ServerTestCase):
    def test_cost_estimate_uses_recent_request_average(self):
        entries = [
            {"request": {"model": MODEL}, "token_stats": {"cost": 0.01}},
            {"request": {"model": MODEL}, "token_stats": {"cost": 0.03}},
            {"request": {"model": "other/model"}, "token_stats": {"cost": 10}},
        ]
        server.AI_REQUEST_LOG_PATH.write_text(json.dumps(entries))

        estimate = server.cost_estimate(MODEL, 2)

        self.assertEqual(estimate["sample_size"], 2)
        self.assertAlmostEqual(estimate["average_request_cost"], 0.02)
        self.assertAlmostEqual(estimate["estimated_cost"], 0.04)


if __name__ == "__main__":
    unittest.main()
