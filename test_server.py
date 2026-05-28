import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

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


class RunWorkerTests(ServerTestCase):
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

        def fake_call_openrouter(prompt, company, model, reasoning_mode=None, run_id=None):
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
