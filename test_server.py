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
from fetch_companies_to_db import sync_us_stock_list


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


class AppPreferenceTests(ServerTestCase):
    def test_portfolio_table_columns_persist_in_database(self):
        saved = server.save_portfolio_table_columns_preference(
            ["company", "weight", "weightUplift", "company"]
        )

        self.assertEqual(saved, ["company", "weight", "weightUplift"])
        self.assertEqual(
            server.get_portfolio_table_columns_preference(),
            ["company", "weight", "weightUplift"],
        )

    def test_portfolio_table_columns_reject_invalid_selections(self):
        with self.assertRaisesRegex(ValueError, "Unknown portfolio table column"):
            server.save_portfolio_table_columns_preference(["company", "not-a-column"])

        with self.assertRaisesRegex(ValueError, "Keep at least one portfolio table column"):
            server.save_portfolio_table_columns_preference([])

    def test_run_table_columns_persist_in_database(self):
        saved = server.save_run_table_columns_preference(
            ["company", "score", "total", "company"]
        )

        self.assertEqual(saved, ["company", "score", "total"])
        self.assertEqual(
            server.get_run_table_columns_preference(),
            ["company", "score", "total"],
        )

    def test_run_table_columns_reject_unknown_column(self):
        with self.assertRaisesRegex(ValueError, "Unknown run table column"):
            server.save_run_table_columns_preference(["company", "not-a-column"])

    def test_run_table_column_views_persist_separately(self):
        server.save_run_table_columns_preference(["company", "score"], "ranking")
        server.save_run_table_columns_preference(["company", "error"], "failed")

        self.assertEqual(
            server.get_run_table_columns_preference("ranking"),
            ["company", "score"],
        )
        self.assertEqual(
            server.get_run_table_columns_preference("failed"),
            ["company", "error"],
        )

    def test_run_table_columns_reject_unknown_view(self):
        with self.assertRaisesRegex(ValueError, "Unknown run table view"):
            server.get_run_table_columns_preference("other")

    def test_legacy_run_table_columns_add_search_before_details(self):
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO app_preferences (key, value, updated_at) VALUES (?, ?, ?)",
                (
                    server.RUN_TABLE_COLUMNS_PREFERENCE_KEY,
                    json.dumps(["company", "actions"]),
                    int(time.time()),
                ),
            )

        self.assertEqual(
            server.get_run_table_columns_preference(),
            ["scorePercentile", "confidence", "company", "search", "chart", "dashboard", "actions"],
        )

    def test_previous_column_preferences_add_price_chart_before_details(self):
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO app_preferences (key, value, updated_at) VALUES (?, ?, ?)",
                (
                    server.RUN_TABLE_COLUMNS_PREFERENCE_KEY,
                    json.dumps(
                        {"version": 3, "columns": ["company", "search", "actions"]}
                    ),
                    int(time.time()),
                ),
            )

        self.assertEqual(
            server.get_run_table_columns_preference(),
            ["scorePercentile", "confidence", "company", "search", "chart", "dashboard", "actions"],
        )

    def test_previous_column_preferences_add_dashboard_before_details(self):
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO app_preferences (key, value, updated_at) VALUES (?, ?, ?)",
                (
                    server.RUN_TABLE_COLUMNS_PREFERENCE_KEY,
                    json.dumps(
                        {
                            "version": 4,
                            "columns": ["company", "search", "chart", "actions"],
                        }
                    ),
                    int(time.time()),
                ),
            )

        self.assertEqual(
            server.get_run_table_columns_preference(),
            ["scorePercentile", "confidence", "company", "search", "chart", "dashboard", "actions"],
        )

    def test_score_percentiles_use_average_rank_for_ties(self):
        results = [
            {"ticker": "LOW", "score": 50, "error": None},
            {"ticker": "MID1", "score": 75, "error": None},
            {"ticker": "MID2", "score": 75, "error": None},
            {"ticker": "HIGH", "score": 100, "error": None},
            {"ticker": "FAIL", "score": None, "error": "failed"},
        ]

        server.add_score_percentiles(results)

        self.assertEqual(results[0]["score_percentile"], 0)
        self.assertEqual(results[1]["score_percentile"], 50)
        self.assertEqual(results[2]["score_percentile"], 50)
        self.assertEqual(results[3]["score_percentile"], 100)
        self.assertIsNone(results[4]["score_percentile"])

    def test_single_score_is_100th_percentile(self):
        results = [{"ticker": "ONLY", "score": 80, "error": None}]

        server.add_score_percentiles(results)

        self.assertEqual(results[0]["score_percentile"], 100)


class ConfidenceScoreTests(ServerTestCase):
    def test_confidence_runs_are_separate_and_can_be_pinned(self):
        standard_run_id = self.create_run()
        confidence_run_id = self.create_run()
        with self.connect() as connection:
            connection.execute(
                "UPDATE scoring_runs SET run_type = 'confidence', name = ? WHERE id = ?",
                ("Confidence Dataset", confidence_run_id),
            )

        pinned = server.set_pinned_confidence_run(confidence_run_id)

        self.assertTrue(pinned["pinned"])
        self.assertEqual(server.pinned_confidence_run_id(), confidence_run_id)
        self.assertEqual([run["id"] for run in server.list_runs()], [standard_run_id])
        self.assertEqual([run["id"] for run in server.list_confidence_runs()], [confidence_run_id])
        self.assertTrue(server.list_confidence_runs()[0]["pinned"])

    def test_minimum_confidence_filters_before_queueing(self):
        confidence_run_id = self.create_run()
        companies = server.scoring_companies(3)
        with self.connect() as connection:
            connection.execute(
                "UPDATE scoring_runs SET run_type = 'confidence' WHERE id = ?",
                (confidence_run_id,),
            )
            server.save_result(connection, confidence_run_id, companies[0], 90, "90", None)
            server.save_result(connection, confidence_run_id, companies[1], 60, "60", None)
            connection.commit()
        server.set_pinned_confidence_run(confidence_run_id)

        eligible, used_run_id, excluded = server.filter_companies_by_confidence(companies, 75)

        self.assertEqual([company["ticker"] for company in eligible], ["AAA"])
        self.assertEqual(used_run_id, confidence_run_id)
        self.assertEqual(excluded, 2)

    def test_latest_confidence_scores_returns_full_latest_run_universe(self):
        older_run_id = self.create_run(company_count=1)
        latest_run_id = self.create_run(company_count=3)
        companies = server.scoring_companies(3)
        with self.connect() as connection:
            connection.execute(
                "UPDATE scoring_runs SET prompt = ?, created_at = ? WHERE id = ?",
                (server.CONFIDENCE_SCORE_PROMPT, 100, older_run_id),
            )
            connection.execute(
                "UPDATE scoring_runs SET prompt = ?, created_at = ?, status = ? WHERE id = ?",
                (server.CONFIDENCE_SCORE_PROMPT, 200, "completed", latest_run_id),
            )
            server.snapshot_run_companies(connection, latest_run_id, companies)
            server.save_result(connection, latest_run_id, companies[0], 91, "Explanation 91", None)
            server.save_result(connection, latest_run_id, companies[1], None, None, "Provider timeout")
            server.update_run_counts(connection, latest_run_id)
            connection.commit()

        payload = server.latest_confidence_scores()

        self.assertEqual(payload["run"]["id"], latest_run_id)
        self.assertEqual(len(payload["scores"]), 3)
        self.assertEqual(payload["scores"][0]["ticker"], "AAA")
        self.assertEqual(payload["scores"][0]["score"], 91)
        self.assertEqual(payload["scores"][1]["ticker"], "BBB")
        self.assertEqual(payload["scores"][1]["error"], "Provider timeout")
        self.assertEqual(payload["scores"][2]["ticker"], "CCC")
        self.assertIsNone(payload["scores"][2]["score"])

    def test_latest_confidence_scores_handles_missing_run(self):
        self.assertEqual(server.latest_confidence_scores(), {"run": None, "scores": []})


class PromptAndParsingTests(ServerTestCase):
    def test_luna_xhigh_model_and_reasoning_settings(self):
        self.assertEqual(server.normalize_model("openai/gpt-5.6-luna"), "openai/gpt-5.6-luna")
        self.assertEqual(
            server.reasoning_config("xhigh")["reasoning"],
            {"effort": "xhigh", "exclude": False},
        )

    def test_luna_xhigh_request_payload(self):
        payload = {
            "choices": [{"message": {"content": "88"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
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
        with mock.patch.object(server.urllib.request, "urlopen", return_value=FakeResponse()) as urlopen:
            server.call_openrouter(
                "Score COMPANY",
                company,
                "openai/gpt-5.6-luna",
                reasoning_mode="xhigh",
                run_id=1,
            )

        request_payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(request_payload["model"], "openai/gpt-5.6-luna")
        self.assertEqual(request_payload["reasoning"], {"effort": "xhigh", "exclude": False})
        self.assertNotIn("temperature", request_payload)

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

    def test_openrouter_timeout_scales_with_response_token_limit(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENROUTER_ATTEMPT_TIMEOUT_SECONDS", None)
            self.assertEqual(server.openrouter_attempt_timeout_seconds(200), 90)
            self.assertEqual(server.openrouter_attempt_timeout_seconds(3000), 240)
            self.assertEqual(server.openrouter_attempt_timeout_seconds(32768), 900)

    def test_openrouter_timeout_allows_an_explicit_override(self):
        with mock.patch.dict(os.environ, {"OPENROUTER_ATTEMPT_TIMEOUT_SECONDS": "150"}):
            self.assertEqual(server.openrouter_attempt_timeout_seconds(3000), 150)

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
        self.assertEqual(stats["reasoning_tokens"], 20)
        self.assertEqual(stats["average_reasoning_tokens"], 10.0)

    def test_token_limit_risk_uses_successful_completion_distribution(self):
        risk = server.estimate_token_limit_failure_risk(
            [180, 220, 250, 290, 330, 380, 450, 540, 680, 850],
            1000,
        )

        self.assertEqual(risk["sample_size"], 10)
        self.assertIsInstance(risk["one_in"], int)
        self.assertGreater(risk["one_in"], 1)
        self.assertGreater(risk["probability"], 0)

    def test_token_limit_risk_requires_ten_successful_samples(self):
        risk = server.estimate_token_limit_failure_risk([100, 200, 300], 1000)

        self.assertEqual(risk["sample_size"], 3)
        self.assertIsNone(risk["one_in"])
        self.assertIsNone(risk["probability"])

    def test_run_token_limit_risk_uses_latest_success_per_stock(self):
        entries = []
        for index in range(10):
            entries.append(
                {
                    "run_id": 7,
                    "company": {"ticker": f"T{index}"},
                    "response": {"success": True},
                    "token_stats": {"completion_tokens": 100 + index * 10},
                }
            )
        entries.append(
            {
                "run_id": 7,
                "company": {"ticker": "T0"},
                "response": {"success": False},
                "token_stats": {"completion_tokens": 1000},
            }
        )

        with mock.patch.object(server, "ai_request_entries", return_value=entries):
            stats = server.ai_request_stats_for_run(7, token_limit=1000)

        self.assertEqual(stats["token_limit_risk_sample_size"], 9)
        self.assertIsNone(stats["token_limit_risk_one_in"])

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

    def test_request_stats_by_ticker_include_token_budget_percent(self):
        entries = [
            {
                "run_id": 7,
                "company": {"ticker": "AAA"},
                "request": {"max_tokens": 2000},
                "response": {"success": True},
                "token_stats": {
                    "completion_tokens": 1250,
                    "completion_tokens_details": {"reasoning_tokens": 800},
                },
            }
        ]

        with mock.patch.object(server, "ai_request_entries", return_value=entries):
            stats = server.ai_request_stats_by_ticker(7)

        self.assertEqual(stats["AAA"]["token_budget"], 2000)
        self.assertEqual(stats["AAA"]["token_budget_used_percent"], 62.5)

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

    def test_us_stock_list_sync_uses_latest_us_companies_in_rank_order(self):
        with self.connect() as connection:
            fetched_at = connection.execute("SELECT MAX(fetched_at) FROM companies").fetchone()[0]
            result = sync_us_stock_list(connection, fetched_at)

        stock_list = server.get_stock_list(result["list_id"])
        self.assertEqual(stock_list["name"], "All US Stocks")
        self.assertEqual([company["ticker"] for company in stock_list["companies"]], ["AAA", "BBB"])

    def test_prompt_for_company_includes_name_and_ticker(self):
        prompt = server.prompt_for_company(
            "Rate COMPANY from 0 to 100",
            {"name": "NVIDIA", "ticker": "NVDA"},
        )
        self.assertEqual(prompt, "Rate NVIDIA (ticker: NVDA) from 0 to 100")

    def test_prompt_for_company_replaces_explicit_ticker_keyword(self):
        prompt = server.prompt_for_company(
            "Rate (COMPANY, ticker: TICKER) from 0 to 100",
            {"name": "NVIDIA", "ticker": "NVDA"},
        )
        self.assertEqual(prompt, "Rate (NVIDIA, ticker: NVDA) from 0 to 100")

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

    def test_fetch_all_companies_stops_on_first_empty_page(self):
        def page_html(start_rank):
            return "".join(
                f"""
                <tr>
                  <td class="rank-td">{rank}</td>
                  <td><div class="company-name">Company {rank}</div><div class="company-code">C{rank}</div></td>
                  <td></td><td>$ 1.00 B</td><td>$10.00</td><td>0.00%</td><td>USA</td>
                </tr>
                """
                for rank in range(start_rank, start_rank + 2)
            )

        with mock.patch.object(
            server,
            "_fetch_html",
            side_effect=[page_html(1), page_html(3), "<html>No companies here</html>"],
        ) as fetch:
            progress = []
            companies = server.fetch_top_market_cap_companies(
                None,
                progress_callback=lambda page, count, rank: progress.append((page, count, rank)),
            )

        self.assertEqual([company["ticker"] for company in companies], ["C1", "C2", "C3", "C4"])
        self.assertEqual(progress, [(1, 2, 2), (2, 4, 4)])
        self.assertEqual(fetch.call_count, 3)

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
    def test_run_name_and_prompt_can_be_edited_together(self):
        run_id = self.create_run(company_count=1)

        updated = server.update_scoring_run(
            run_id,
            name="Updated ranking",
            prompt="Updated score for COMPANY",
            max_tokens=2400,
        )

        self.assertEqual(updated["name"], "Updated ranking")
        self.assertEqual(updated["prompt"], "Updated score for COMPANY")
        self.assertEqual(updated["max_tokens"], 2400)

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

    def test_run_universe_can_change_to_a_superset(self):
        original = server.save_stock_list("Original", ["CCC", "AAA"])
        superset = server.save_stock_list("Expanded", ["BBB", "CCC", "AAA"])
        server.start_scoring_worker_process = lambda run_id, start_index=0: 12345
        run_id = server.create_scoring_run(
            "Expandable Run",
            "Score COMPANY",
            MODEL,
            reasoning_mode="none",
            stock_list_id=original["id"],
        )

        updated = server.update_scoring_run(run_id, stock_list_id=superset["id"])

        self.assertEqual(updated["stock_list_id"], superset["id"])
        self.assertEqual(updated["stock_list_name"], "Expanded")
        self.assertEqual(updated["company_tickers"], ["CCC", "AAA"])
        self.assertEqual(updated["company_count"], 2)
        self.assertEqual(updated["extension_limit"], 3)

    def test_run_universe_rejects_a_non_superset(self):
        original = server.save_stock_list("Original", ["CCC", "AAA"])
        missing_current_stock = server.save_stock_list("Missing One", ["CCC", "BBB"])
        server.start_scoring_worker_process = lambda run_id, start_index=0: 12345
        run_id = server.create_scoring_run(
            "Protected Run",
            "Score COMPANY",
            MODEL,
            reasoning_mode="none",
            stock_list_id=original["id"],
        )

        with self.assertRaisesRegex(ValueError, "must include every stock already in this run"):
            server.update_scoring_run(run_id, stock_list_id=missing_current_stock["id"])

        unchanged = server.get_run(run_id)
        self.assertEqual(unchanged["stock_list_id"], original["id"])
        self.assertEqual(unchanged["company_tickers"], ["CCC", "AAA"])

    def test_run_universe_can_change_back_to_top_companies(self):
        original = server.save_stock_list("Original", ["CCC", "AAA"])
        server.start_scoring_worker_process = lambda run_id, start_index=0: 12345
        run_id = server.create_scoring_run(
            "Top Universe Run",
            "Score COMPANY",
            MODEL,
            reasoning_mode="none",
            stock_list_id=original["id"],
        )

        updated = server.update_scoring_run(run_id, stock_list_id=None)

        self.assertIsNone(updated["stock_list_id"])
        self.assertIsNone(updated["stock_list_name"])
        self.assertEqual(updated["company_tickers"], ["CCC", "AAA"])
        self.assertEqual(updated["extension_limit"], 3)

    def test_run_universe_options_mark_only_supersets_eligible(self):
        original = server.save_stock_list("Original", ["CCC", "AAA"])
        superset = server.save_stock_list("Expanded", ["BBB", "CCC", "AAA"])
        subset = server.save_stock_list("Too Small", ["CCC"])
        server.start_scoring_worker_process = lambda run_id, start_index=0: 12345
        run_id = server.create_scoring_run(
            "Options Run",
            "Score COMPANY",
            MODEL,
            reasoning_mode="none",
            stock_list_id=original["id"],
        )

        options = server.run_universe_options(run_id)
        by_id = {item["stock_list_id"]: item for item in options}

        self.assertTrue(by_id[None]["eligible"])
        self.assertTrue(by_id[original["id"]]["current"])
        self.assertTrue(by_id[superset["id"]]["eligible"])
        self.assertFalse(by_id[subset["id"]]["eligible"])
        self.assertEqual(by_id[subset["id"]]["missing_count"], 1)

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

    def test_redrive_failed_run_targets_only_failed_tickers(self):
        run_id = self.create_run(company_count=3)
        companies = server.scoring_companies(3)
        with self.connect() as connection:
            server.save_result(connection, run_id, companies[0], 88, "88", None)
            server.save_result(connection, run_id, companies[1], None, None, "Provider timeout")
            server.update_run_counts(connection, run_id)
            connection.execute(
                "UPDATE scoring_runs SET status = ? WHERE id = ?",
                ("completed", run_id),
            )
            connection.commit()

        worker_calls = []

        def fake_start_worker(run_id, start_index=0, target_tickers=None):
            worker_calls.append((run_id, start_index, target_tickers))
            return 12345

        server.start_scoring_worker_process = fake_start_worker
        run = server.redrive_failed_scoring_run(run_id)

        self.assertEqual(worker_calls, [(run_id, 0, ["BBB"])])
        self.assertEqual(run["status"], "queued")
        self.assertEqual(run["failed_count"], 1)
        self.assertEqual(run["queue_count"], 1)

    def test_redrive_single_failed_stock_targets_only_requested_ticker(self):
        run_id = self.create_run(company_count=3)
        companies = server.scoring_companies(3)
        with self.connect() as connection:
            server.save_result(connection, run_id, companies[0], 88, "88", None)
            server.save_result(connection, run_id, companies[1], None, None, "Provider timeout")
            server.save_result(connection, run_id, companies[2], None, None, "Token limit")
            server.update_run_counts(connection, run_id)
            connection.execute(
                "UPDATE scoring_runs SET status = ? WHERE id = ?",
                ("completed", run_id),
            )
            connection.commit()

        worker_calls = []

        def fake_start_worker(run_id, start_index=0, target_tickers=None):
            worker_calls.append((run_id, start_index, target_tickers))
            return 12345

        server.start_scoring_worker_process = fake_start_worker
        run = server.redrive_failed_scoring_run(run_id, ["CCC"])

        self.assertEqual(worker_calls, [(run_id, 0, ["CCC"])])
        self.assertEqual(run["status"], "queued")
        self.assertEqual(run["queue_count"], 1)

    def test_worker_queue_count_includes_active_and_waiting_stocks(self):
        run_id = self.create_run(company_count=3)
        observed_queue_counts = []

        def fake_call_openrouter(prompt, company, model, reasoning_mode=None, run_id=None, max_tokens=None):
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT queue_count FROM scoring_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
            observed_queue_counts.append(row["queue_count"])
            return "50"

        server.call_openrouter = fake_call_openrouter
        server.scoring_concurrency = lambda: 1
        server.score_run_worker(run_id)

        with self.connect() as connection:
            run = connection.execute(
                "SELECT status, queue_count FROM scoring_runs WHERE id = ?",
                (run_id,),
            ).fetchone()

        self.assertEqual(observed_queue_counts, [3, 2, 1])
        self.assertEqual((run["status"], run["queue_count"]), ("completed", 0))

    def test_redrive_single_stock_rejects_successful_ticker(self):
        run_id = self.create_run(company_count=2)
        companies = server.scoring_companies(2)
        with self.connect() as connection:
            server.save_result(connection, run_id, companies[0], 88, "88", None)
            server.save_result(connection, run_id, companies[1], None, None, "Provider timeout")
            server.update_run_counts(connection, run_id)
            connection.execute(
                "UPDATE scoring_runs SET status = ? WHERE id = ?",
                ("completed", run_id),
            )
            connection.commit()

        with self.assertRaisesRegex(ValueError, "Only currently failed stocks"):
            server.redrive_failed_scoring_run(run_id, ["AAA"])

    def test_get_run_includes_company_logos(self):
        run_id = self.create_run(company_count=1)
        with self.connect() as connection:
            server.save_result(connection, run_id, server.scoring_companies(1)[0], 90, "90", None)
            server.update_run_counts(connection, run_id)
            connection.commit()

        run = server.get_run(run_id)
        self.assertEqual(run["results"][0]["logo"], "https://logos/aaa.png")

    def test_companies_are_paginated_and_searched_on_the_server(self):
        first_page = server.paginated_companies(page=1, page_size=2)
        second_page = server.paginated_companies(page=2, page_size=2)
        searched = server.paginated_companies(query="Gamma")

        self.assertEqual(first_page["pagination"]["total"], 3)
        self.assertEqual([company["ticker"] for company in first_page["companies"]], ["AAA", "BBB"])
        self.assertEqual([company["ticker"] for company in second_page["companies"]], ["CCC"])
        self.assertEqual([company["ticker"] for company in searched["companies"]], ["CCC"])

    def test_run_page_omits_responses_and_keeps_global_ranks(self):
        run_id = self.create_run(company_count=3)
        companies = server.scoring_companies(3)
        with self.connect() as connection:
            for index, company in enumerate(companies):
                server.save_result(connection, run_id, company, 90 - index, f"Long response {index}", None)
            server.update_run_counts(connection, run_id)
            connection.commit()

        second_page = server.paginated_run(run_id, page=2, page_size=2)

        self.assertEqual(second_page["result_page"]["total"], 3)
        self.assertEqual(second_page["result_page"]["offset"], 2)
        self.assertEqual(second_page["results"][0]["scoreRank"], 3)
        self.assertNotIn("raw_response", second_page["results"][0])
        self.assertEqual(second_page["score_stats"]["median"], 89)
        self.assertEqual(second_page["score_stats"]["size_score_sample_size"], 3)
        self.assertGreater(second_page["score_stats"]["size_score_correlation"], 0.98)

    def test_size_score_correlation_uses_log_market_cap(self):
        correlation, sample_size = server.size_score_correlation(
            [
                {"score": 30, "market_cap_value": 1_000},
                {"score": 20, "market_cap_value": 100},
                {"score": 10, "market_cap_value": 10},
                {"score": None, "market_cap_value": 1},
            ]
        )

        self.assertEqual(sample_size, 3)
        self.assertAlmostEqual(correlation, 1.0)


class StockListTests(ServerTestCase):
    def test_stock_list_can_be_created_and_updated(self):
        stock_list = server.save_stock_list("Semis", ["AAA", "CCC"])
        self.assertEqual(stock_list["name"], "Semis")
        self.assertEqual([company["ticker"] for company in stock_list["companies"]], ["AAA", "CCC"])

        updated = server.save_stock_list("Semis Plus", ["BBB", "AAA"], stock_list["id"])
        self.assertEqual(updated["name"], "Semis Plus")
        self.assertEqual([company["ticker"] for company in updated["companies"]], ["BBB", "AAA"])


class PortfolioTests(ServerTestCase):
    def scored_run(self):
        run_id = self.create_run(company_count=3)
        scores = {"AAA": 90, "BBB": 50, "CCC": 10}
        with self.connect() as connection:
            for company in server.scoring_companies(3):
                server.save_result(
                    connection,
                    run_id,
                    company,
                    scores[company["ticker"]],
                    str(scores[company["ticker"]]),
                    None,
                )
            server.update_run_counts(connection, run_id)
            connection.commit()
        return run_id

    def test_portfolio_applies_percentile_multiplier_and_normalizes_weights(self):
        portfolio = server.calculate_portfolio(
            self.scored_run(), "Test Portfolio", 3, 50, 4
        )

        self.assertEqual([holding["ticker"] for holding in portfolio["holdings"]], ["AAA", "BBB"])
        self.assertEqual(portfolio["holdings"][0]["score_percentile"], 100)
        self.assertEqual(portfolio["holdings"][0]["score_multiplier"], 4)
        self.assertEqual(portfolio["holdings"][1]["score_percentile"], 50)
        self.assertEqual(portfolio["holdings"][1]["score_multiplier"], 1)
        self.assertAlmostEqual(portfolio["holdings"][0]["portfolio_weight"], 85.7142857)
        self.assertAlmostEqual(portfolio["holdings"][1]["portfolio_weight"], 14.2857143)
        self.assertAlmostEqual(portfolio["holdings"][0]["market_cap_weight"], 60)
        self.assertAlmostEqual(portfolio["holdings"][1]["market_cap_weight"], 40)
        self.assertAlmostEqual(portfolio["holdings"][0]["weight_uplift"], 1.4285714)
        self.assertAlmostEqual(portfolio["holdings"][1]["weight_uplift"], 0.3571429)
        self.assertAlmostEqual(
            sum(holding["portfolio_weight"] for holding in portfolio["holdings"]),
            100,
        )
        self.assertEqual(portfolio["name"], "Test Portfolio")
        self.assertEqual(portfolio["holding_count"], 2)
        with self.connect() as connection:
            portfolio_tables = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name IN ('portfolios', 'portfolio_holdings')
                """
            ).fetchall()
        self.assertEqual(portfolio_tables, [])

    def test_market_cap_limit_is_applied_before_score_percentiles(self):
        portfolio = server.calculate_portfolio(
            self.scored_run(), "Top Two", 2, 50, 4
        )

        self.assertEqual([holding["ticker"] for holding in portfolio["holdings"]], ["AAA"])
        self.assertEqual(portfolio["holdings"][0]["score_percentile"], 100)
        self.assertEqual(portfolio["market_cap_limit"], 2)

    def test_portfolio_rejects_more_companies_than_successful_results(self):
        with self.assertRaisesRegex(ValueError, "no more than 3"):
            server.calculate_portfolio(self.scored_run(), "Too Large", 4, 50, 4)


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
