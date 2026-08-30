#!/usr/bin/env python3
import concurrent.futures
import copy
import html
import json
import math
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlparse


ROOT = Path(__file__).resolve().parent
SOURCE_URL = "https://companiesmarketcap.com/"
COMPANY_UNIVERSE_LIMIT = 2000
COMPANIESMARKETCAP_PAGE_SIZE = 100
CACHE_SECONDS = 60 * 15
DB_PATH = ROOT / "companies.db"
AI_REQUEST_LOG_PATH = ROOT / "ai_requests.json"
PROVIDER_BLOCKLIST_PATH = ROOT / "provider_blocklist.json"
SCORING_WORKER_PATH = ROOT / "scoring_worker.py"
SCORING_WORKER_LOG_PATH = ROOT / "scoring_worker.log"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL_ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model}/endpoints"
OPENROUTER_ENDPOINT_CACHE_SECONDS = 60 * 5
DEFAULT_PROVIDER_BLOCKLIST = ["siliconflow", "gmicloud"]
RUN_FIELD_UNSET = object()
PROVIDER_SLUGS = {
    "AkashML": "akashml",
    "Alibaba": "alibaba",
    "AtlasCloud": "atlas-cloud",
    "Baidu": "baidu",
    "DeepSeek": "deepseek",
    "GMICloud": "gmicloud",
    "SiliconFlow": "siliconflow",
}
MODEL_OPTIONS = [
    {
        "id": "deepseek/deepseek-v4-flash-0731",
        "label": "DeepSeek V4 Flash 0731",
        "reasoning": {
            "mandatory": False,
            "default_enabled": True,
            "supported_efforts": ["max", "high", "low"],
            "default_effort": "high",
        },
        "provider": {
            "require_parameters": True,
        },
    },
    {
        "id": "z-ai/glm-5.3-flash",
        "label": "GLM 5.3 Flash",
        "reasoning": {
            "mandatory": True,
            "default_enabled": True,
            "supported_efforts": ["max", "high", "low"],
            "default_effort": "max",
        },
        "provider": {
            "require_parameters": True,
        },
    },
    {
        "id": "openai/gpt-5.6-luna",
        "label": "GPT-5.6 Luna",
        "supports_temperature": False,
        "reasoning": {
            "mandatory": False,
            "default_enabled": True,
            "supported_efforts": ["max", "xhigh", "high", "medium", "low", "none"],
            "default_effort": "medium",
        },
        "provider": {
            "require_parameters": True,
        },
    },
    {
        "id": "xiaomi/mimo-v2.5",
        "label": "Xiaomi MiMo V2.5",
        "reasoning": {
            "mandatory": False,
            "default_enabled": False,
        },
        "provider": {
            "require_parameters": True,
        },
    },
    {
        "id": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "label": "NVIDIA Nemotron 3 Ultra (free)",
        "reasoning": {
            "mandatory": False,
            "default_enabled": True,
            "supported_efforts": ["high", "medium"],
            "default_effort": "high",
        },
        "provider": {
            "require_parameters": True,
        },
    },
]
LEGACY_MODEL_OPTIONS = [
    {
        "id": "deepseek/deepseek-v4-flash",
        "label": "DeepSeek V4 Flash (legacy)",
        "reasoning": {
            "mandatory": False,
            "default_enabled": True,
            "supported_efforts": ["xhigh", "high"],
            "default_effort": "high",
        },
        "provider": {
            "require_parameters": True,
        },
    },
]
ALL_MODEL_OPTIONS = MODEL_OPTIONS + LEGACY_MODEL_OPTIONS
REASONING_EFFORT_ORDER = ("none", "low", "medium", "high", "xhigh", "max")
REASONING_EFFORT_LABELS = {
    "none": "Non-reasoning",
    "low": "Low reasoning",
    "medium": "Medium reasoning",
    "high": "High reasoning",
    "xhigh": "XHigh reasoning",
    "max": "Max reasoning",
}
DEFAULT_OPENROUTER_MODEL = MODEL_OPTIONS[0]["id"]
DEFAULT_SCORING_COMPANY_COUNT = 10
DEFAULT_SCORING_CONCURRENCY = 20
OPENROUTER_MAX_TOKENS = int(os.environ.get("OPENROUTER_MAX_TOKENS", "200"))
OPENROUTER_MAX_ATTEMPTS = 3
OPENROUTER_ATTEMPT_TIMEOUT_SECONDS = 90
OPENROUTER_TIMEOUT_SECONDS_PER_TOKEN = 0.08
OPENROUTER_MAX_ATTEMPT_TIMEOUT_SECONDS = 900
MAX_OPENROUTER_RESPONSE_TOKENS = 32768
OPENROUTER_RESPONSE_CACHE_TTL_SECONDS = 86400
TOKEN_LIMIT_ERROR = "Invalid response: model hit the response token limit before completing."
CONFIDENCE_SCORE_PROMPT = """rate from 0 to 100 on how well you know, understand, and are confident in your ability to evaluate this company: (COMPANY, ticker: TICKER)
write about a 100 word explanation and then end only with the number score"""
RUN_TABLE_COLUMN_KEYS = (
    "rank",
    "score",
    "scorePercentile",
    "confidence",
    "company",
    "marketCap",
    "input",
    "response",
    "reasoning",
    "total",
    "budget",
    "time",
    "cost",
    "error",
    "search",
    "chart",
    "dashboard",
    "actions",
)
PINNED_CONFIDENCE_RUN_KEY = "pinned_confidence_run_id"
RUN_TABLE_COLUMNS_PREFERENCE_KEY = "run_table_columns"
RUN_TABLE_COLUMNS_PREFERENCE_KEYS = {
    "ranking": "run_table_columns_ranking",
    "failed": "run_table_columns_failed",
}
RUN_TABLE_COLUMN_ORDER_PREFERENCE_KEYS = {
    "ranking": "run_table_column_order_ranking",
    "failed": "run_table_column_order_failed",
}
PROVIDER_TABLE_COLUMN_KEYS = (
    "provider",
    "requests",
    "stocks",
    "success",
    "tokens",
    "reasoning",
    "cost",
    "costPerMillion",
    "inputCostPerMillion",
    "outputCostPerMillion",
    "latency",
    "trace",
    "cache",
)
PROVIDER_TABLE_COLUMNS_PREFERENCE_KEY = "provider_table_columns"
PROVIDER_TABLE_COLUMN_ORDER_PREFERENCE_KEY = "provider_table_column_order"
PORTFOLIO_TABLE_COLUMN_KEYS = (
    "position",
    "company",
    "score",
    "scorePercentile",
    "marketCap",
    "multiplier",
    "adjustedMarketCap",
    "weight",
    "weightUplift",
)
PORTFOLIO_TABLE_COLUMNS_PREFERENCE_KEY = "portfolio_table_columns"
PORTFOLIO_TABLE_COLUMN_ORDER_PREFERENCE_KEY = "portfolio_table_column_order"

_cache = {"companies": None, "fetched_at": 0, "error": None}
_cache_lock = threading.Lock()
_ai_request_log_lock = threading.Lock()
_provider_blocklist_lock = threading.Lock()
_provider_endpoint_cache_lock = threading.Lock()
_provider_endpoint_cache = {}


def load_env_file():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()


def openrouter_model_options():
    return [
        {
            **option,
            "reasoning_modes": reasoning_options(option["id"]),
            "default_reasoning_mode": default_reasoning_mode(option["id"]),
        }
        for option in MODEL_OPTIONS
    ]


def openrouter_model():
    return DEFAULT_OPENROUTER_MODEL


def reasoning_options(model=None):
    config = model_config(model or DEFAULT_OPENROUTER_MODEL)
    capability = config.get("reasoning") or {}
    mandatory = bool(capability.get("mandatory"))
    supported_efforts = capability.get("supported_efforts")
    options = []
    if not mandatory:
        options.append(
            {
                "id": "none",
                "label": REASONING_EFFORT_LABELS["none"],
                "reasoning": {"enabled": False, "exclude": False},
            }
        )
    if supported_efforts:
        for effort in REASONING_EFFORT_ORDER:
            if effort == "none" or effort not in supported_efforts:
                continue
            options.append(
                {
                    "id": effort,
                    "label": REASONING_EFFORT_LABELS[effort],
                    "reasoning": {"effort": effort, "exclude": False},
                }
            )
    else:
        options.append(
            {
                "id": "default",
                "label": "Reasoning",
                "reasoning": {"enabled": True, "exclude": False},
            }
        )
    return options


def default_reasoning_mode(model=None):
    config = model_config(model or DEFAULT_OPENROUTER_MODEL)
    capability = config.get("reasoning") or {}
    if capability.get("mandatory") or capability.get("default_enabled"):
        return capability.get("default_effort") or "default"
    return "none"


def normalize_model(model):
    requested = (model or DEFAULT_OPENROUTER_MODEL).strip()
    allowed = {option["id"] for option in ALL_MODEL_OPTIONS}
    if requested not in allowed:
        raise ValueError("Selected model is not available.")
    return requested


def model_config(model):
    normalized = normalize_model(model)
    return next(option for option in ALL_MODEL_OPTIONS if option["id"] == normalized)


def normalize_reasoning_mode(reasoning_mode, model=None):
    normalized_model = normalize_model(model)
    requested = (reasoning_mode or default_reasoning_mode(normalized_model)).strip()
    allowed = {option["id"] for option in reasoning_options(normalized_model)}
    if requested not in allowed:
        raise ValueError("Selected reasoning mode is not available for this model.")
    return requested


def compatible_reasoning_mode(reasoning_mode, model=None):
    try:
        return normalize_reasoning_mode(reasoning_mode, model)
    except ValueError:
        return default_reasoning_mode(model)


def reasoning_config(reasoning_mode, model=None, allow_fallback=False):
    normalized_model = normalize_model(model)
    normalized = (
        compatible_reasoning_mode(reasoning_mode, normalized_model)
        if allow_fallback
        else normalize_reasoning_mode(reasoning_mode, normalized_model)
    )
    return next(option for option in reasoning_options(normalized_model) if option["id"] == normalized)


def provider_slug(provider):
    if not provider:
        return ""
    provider = str(provider).strip()
    if not provider:
        return ""
    if provider in PROVIDER_SLUGS:
        return PROVIDER_SLUGS[provider]
    return re.sub(r"[^a-z0-9]+", "-", provider.lower()).strip("-")


def provider_blocklist():
    blocked = {provider_slug(provider) for provider in DEFAULT_PROVIDER_BLOCKLIST}
    with _provider_blocklist_lock:
        if PROVIDER_BLOCKLIST_PATH.exists():
            try:
                payload = json.loads(PROVIDER_BLOCKLIST_PATH.read_text())
                providers = payload.get("blocked_providers") if isinstance(payload, dict) else payload
                if isinstance(providers, list):
                    blocked.update(provider_slug(provider) for provider in providers if provider)
            except json.JSONDecodeError:
                pass
    return sorted(provider for provider in blocked if provider)


def save_provider_blocklist(blocked):
    payload = {
        "blocked_providers": sorted(set(blocked)),
        "updated_at": int(time.time()),
    }
    tmp_path = PROVIDER_BLOCKLIST_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp_path.replace(PROVIDER_BLOCKLIST_PATH)


def block_reasoning_provider(provider, run_id=None, ticker=None, reasoning_tokens=0):
    slug = provider_slug(provider)
    if not slug:
        return False
    with _provider_blocklist_lock:
        blocked = {provider_slug(item) for item in DEFAULT_PROVIDER_BLOCKLIST}
        if PROVIDER_BLOCKLIST_PATH.exists():
            try:
                payload = json.loads(PROVIDER_BLOCKLIST_PATH.read_text())
                providers = payload.get("blocked_providers") if isinstance(payload, dict) else payload
                if isinstance(providers, list):
                    blocked.update(provider_slug(item) for item in providers if item)
            except json.JSONDecodeError:
                pass
        blocked = {item for item in blocked if item}
        already_blocked = slug in blocked
        blocked.add(slug)
        payload = {
            "blocked_providers": sorted(blocked),
            "updated_at": int(time.time()),
            "last_blocked": {
                "provider": provider,
                "provider_slug": slug,
                "run_id": run_id,
                "ticker": ticker,
                "reasoning_tokens": reasoning_tokens,
            },
        }
        tmp_path = PROVIDER_BLOCKLIST_PATH.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        tmp_path.replace(PROVIDER_BLOCKLIST_PATH)
    return not already_blocked


def normalize_low_cost_mode(value):
    if value in (None, False, 0):
        return False
    if value in (True, 1):
        return True
    raise ValueError("Low cost mode must be true or false.")


def provider_preferences(config, low_cost_provider=None):
    provider = dict(config.get("provider") or {})
    blocked = provider_blocklist()
    if blocked:
        provider["ignore"] = blocked
    if low_cost_provider:
        provider["only"] = [low_cost_provider["tag"]]
        provider["allow_fallbacks"] = False
    return provider


def openrouter_model_endpoints(model, force=False):
    model = normalize_model(model)
    now = time.time()
    with _provider_endpoint_cache_lock:
        cached = _provider_endpoint_cache.get(model)
        if cached and not force and now - cached["fetched_at"] < OPENROUTER_ENDPOINT_CACHE_SECONDS:
            return copy.deepcopy(cached["endpoints"])

    request = urllib.request.Request(
        OPENROUTER_MODEL_ENDPOINTS_URL.format(model=quote(model, safe="/:")),
        headers={
            "Accept": "application/json",
            **(
                {"Authorization": f"Bearer {os.environ['OPENROUTER_KEY']}"}
                if os.environ.get("OPENROUTER_KEY")
                else {}
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    endpoints = payload.get("data", {}).get("endpoints")
    if not isinstance(endpoints, list):
        raise RuntimeError("OpenRouter did not return provider pricing for this model.")
    with _provider_endpoint_cache_lock:
        _provider_endpoint_cache[model] = {
            "fetched_at": now,
            "endpoints": copy.deepcopy(endpoints),
        }
    return endpoints


def lowest_cost_provider(model, reasoning_mode=None, max_tokens=None, endpoints=None):
    config = model_config(model)
    reasoning = reasoning_config(reasoning_mode, config["id"], allow_fallback=True)["reasoning"]
    token_limit = normalize_max_tokens(max_tokens)
    required_parameters = {"max_tokens", "reasoning"}
    if "effort" in reasoning:
        required_parameters.add("reasoning_effort")
    if config.get("supports_temperature", True):
        required_parameters.add("temperature")
    blocked = set(provider_blocklist())
    candidates = []

    for endpoint in endpoints if endpoints is not None else openrouter_model_endpoints(config["id"]):
        provider_name = str(endpoint.get("provider_name") or "").strip()
        tag = str(endpoint.get("tag") or "").strip()
        endpoint_slugs = {provider_slug(provider_name), provider_slug(tag.split("/", 1)[0])}
        if not provider_name or not tag or blocked.intersection(endpoint_slugs):
            continue
        if endpoint.get("status") not in (None, 0, "0"):
            continue
        try:
            endpoint_limit = int(endpoint.get("max_completion_tokens") or 0)
        except (TypeError, ValueError):
            endpoint_limit = 0
        if endpoint_limit and endpoint_limit < token_limit:
            continue
        supported = set(endpoint.get("supported_parameters") or [])
        if not required_parameters.issubset(supported):
            continue
        pricing = endpoint.get("pricing") or {}
        try:
            prompt_price = float(pricing["prompt"])
            completion_price = float(pricing["completion"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(prompt_price) or not math.isfinite(completion_price):
            continue
        if prompt_price < 0 or completion_price < 0:
            continue
        candidates.append(
            {
                "provider_name": provider_name,
                "tag": tag,
                "prompt_price": prompt_price,
                "completion_price": completion_price,
            }
        )

    if not candidates:
        raise RuntimeError(
            "No eligible OpenRouter provider has pricing and supports this run's settings."
        )
    return min(
        candidates,
        key=lambda item: (
            item["completion_price"],
            item["prompt_price"],
            item["provider_name"].lower(),
            item["tag"].lower(),
        ),
    )


def model_details(model, reasoning_mode=None):
    config = model_config(model)
    reasoning = reasoning_config(reasoning_mode, config["id"], allow_fallback=True)
    return {
        "id": config["id"],
        "label": config["label"],
        "reasoning_mode": reasoning["id"],
        "reasoning_label": reasoning["label"],
        "reasoning": reasoning["reasoning"],
        "reasoning_modes": reasoning_options(config["id"]),
        "default_reasoning_mode": default_reasoning_mode(config["id"]),
        "provider": provider_preferences(config),
    }


def openrouter_max_tokens():
    return int(os.environ.get("OPENROUTER_MAX_TOKENS", str(OPENROUTER_MAX_TOKENS)))


def normalize_max_tokens(value):
    if value is None or value == "":
        value = openrouter_max_tokens()
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Response token limit must be a whole number.") from exc
    if value < 1 or value > MAX_OPENROUTER_RESPONSE_TOKENS:
        raise ValueError(f"Choose a response token limit from 1 to {MAX_OPENROUTER_RESPONSE_TOKENS:,}.")
    return value


def openrouter_max_attempts():
    try:
        value = int(os.environ.get("OPENROUTER_MAX_ATTEMPTS", str(OPENROUTER_MAX_ATTEMPTS)))
    except ValueError:
        value = OPENROUTER_MAX_ATTEMPTS
    return max(1, min(5, value))


def openrouter_attempt_timeout_seconds(max_tokens=None):
    configured_value = os.environ.get("OPENROUTER_ATTEMPT_TIMEOUT_SECONDS")
    if configured_value is None:
        token_limit = normalize_max_tokens(max_tokens)
        scaled_timeout = token_limit * OPENROUTER_TIMEOUT_SECONDS_PER_TOKEN
        return min(
            OPENROUTER_MAX_ATTEMPT_TIMEOUT_SECONDS,
            max(OPENROUTER_ATTEMPT_TIMEOUT_SECONDS, scaled_timeout),
        )
    try:
        value = float(configured_value)
    except (TypeError, ValueError):
        value = OPENROUTER_ATTEMPT_TIMEOUT_SECONDS
    return max(1, value)


def read_http_response_with_deadline(response, timeout_seconds):
    timed_out = threading.Event()

    def abort_response():
        timed_out.set()
        try:
            sock = response.fp.raw._sock
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            response.close()
        except Exception:
            pass

    timer = threading.Timer(timeout_seconds, abort_response)
    timer.daemon = True
    timer.start()
    try:
        payload = response.read()
    except Exception as exc:
        if timed_out.is_set():
            raise TimeoutError(
                f"OpenRouter request exceeded the {timeout_seconds:g}-second attempt limit."
            ) from exc
        raise
    finally:
        timer.cancel()

    if timed_out.is_set():
        raise TimeoutError(
            f"OpenRouter request exceeded the {timeout_seconds:g}-second attempt limit."
        )
    return payload


def openrouter_cache_ttl_seconds():
    try:
        value = int(
            os.environ.get(
                "OPENROUTER_CACHE_TTL_SECONDS",
                str(OPENROUTER_RESPONSE_CACHE_TTL_SECONDS),
            )
        )
    except ValueError:
        value = OPENROUTER_RESPONSE_CACHE_TTL_SECONDS
    return max(1, min(86400, value))


def scoring_concurrency():
    try:
        value = int(os.environ.get("SCORING_CONCURRENCY", str(DEFAULT_SCORING_CONCURRENCY)))
    except ValueError:
        value = DEFAULT_SCORING_CONCURRENCY
    return max(1, min(20, value))


def prompt_has_company_keyword(prompt):
    return "COMPANY" in prompt


def append_ai_request_log(entry):
    with _ai_request_log_lock:
        if AI_REQUEST_LOG_PATH.exists():
            try:
                entries = json.loads(AI_REQUEST_LOG_PATH.read_text())
                if not isinstance(entries, list):
                    entries = []
            except json.JSONDecodeError:
                entries = []
        else:
            entries = []

        entries.append(entry)
        tmp_path = AI_REQUEST_LOG_PATH.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(entries, indent=2, sort_keys=True))
        tmp_path.replace(AI_REQUEST_LOG_PATH)


def _clean(value):
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _number_from_market_cap(value):
    match = re.search(r"\$\s*([\d,.]+)\s*([TBM])", value)
    if not match:
        return 0
    amount = float(match.group(1).replace(",", ""))
    unit = match.group(2)
    multiplier = {"T": 1_000_000_000_000, "B": 1_000_000_000, "M": 1_000_000}[unit]
    return round(amount * multiplier)


def _fetch_html(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return response.read().decode("utf-8", "replace")


def company_source_url_for_page(page):
    if page <= 1:
        return SOURCE_URL
    return f"{SOURCE_URL}page/{page}/"


def _parse_companies(page_html):
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, flags=re.I | re.S)
    companies = []

    for row in rows:
        if "company-code" not in row and "company-name" not in row:
            continue

        rank_match = re.search(r'<td[^>]*class="[^"]*rank-td[^"]*"[^>]*>\s*(\d+)', row, re.I)
        name_match = re.search(r'<div[^>]*class="[^"]*company-name[^"]*"[^>]*>(.*?)</div>', row, re.I | re.S)
        ticker_match = re.search(r'<div[^>]*class="[^"]*company-code[^"]*"[^>]*>(.*?)</div>', row, re.I | re.S)
        logo_match = re.search(r'<img[^>]+class="[^"]*company-logo[^"]*"[^>]+src="([^"]+)"', row, re.I)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.I | re.S)

        if not (rank_match and name_match and ticker_match) or len(tds) < 5:
            continue

        values = [_clean(td) for td in tds]
        country = re.sub(r"[^\x00-\x7F]+", "", values[-1]).strip()
        today = values[5]
        if "percentage-red" in tds[5] and not today.startswith("-"):
            today = f"-{today}"
        logo = html.unescape(logo_match.group(1)) if logo_match else ""
        if logo.startswith("/"):
            logo = f"https://companiesmarketcap.com{logo}"

        companies.append(
            {
                "rank": int(rank_match.group(1)),
                "name": _clean(name_match.group(1)),
                "ticker": _clean(ticker_match.group(1)),
                "marketCap": values[3],
                "marketCapValue": _number_from_market_cap(values[3]),
                "price": values[4],
                "today": today,
                "country": country,
                "logo": logo,
            }
        )

    if not companies:
        # Fallback for the text-like shape returned by some crawlers.
        compact = re.sub(r"\s+", " ", _clean(page_html))
        pattern = re.compile(
            r"(?:favorite icon )?(?P<rank>\d+) Image: (?P<name>.+?) logo "
            r"(?P=name) (?P<ticker>[A-Z0-9.\-]+) (?P<cap>\$[\d,.]+ [TBM])"
            r"(?P<price>\$[\d,.]+) (?P<today>[\d.\-]+%) (?P<country>[A-Za-z. ]+?)(?= favorite icon \d|$)"
        )
        for match in pattern.finditer(compact):
            companies.append(
                {
                    "rank": int(match.group("rank")),
                    "name": match.group("name").strip(),
                    "ticker": match.group("ticker"),
                    "marketCap": match.group("cap"),
                    "marketCapValue": _number_from_market_cap(match.group("cap")),
                    "price": match.group("price"),
                    "today": match.group("today"),
                    "country": match.group("country").strip(),
                    "logo": "",
                }
            )

    return sorted(companies, key=lambda item: item["rank"])


def fetch_top_market_cap_companies(limit=COMPANY_UNIVERSE_LIMIT, progress_callback=None):
    companies_by_ticker = {}
    page_count = None if limit is None else (
        limit + COMPANIESMARKETCAP_PAGE_SIZE - 1
    ) // COMPANIESMARKETCAP_PAGE_SIZE
    page = 1
    previous_ranks = None
    while page_count is None or page <= page_count:
        last_error = None
        for attempt in range(1, 4):
            try:
                parsed = _parse_companies(_fetch_html(company_source_url_for_page(page)))
                break
            except Exception as exc:
                last_error = exc
                if attempt == 3:
                    raise
                time.sleep(attempt)
        else:
            raise last_error

        if not parsed:
            if limit is None and companies_by_ticker:
                break
            raise ValueError(f"No companies found on CompaniesMarketCap page {page}")

        page_ranks = tuple(company["rank"] for company in parsed)
        if page_ranks == previous_ranks:
            if limit is None:
                break
            raise ValueError(f"CompaniesMarketCap repeated page data at page {page}")
        previous_ranks = page_ranks

        count_before = len(companies_by_ticker)
        for company in parsed:
            companies_by_ticker[company["ticker"]] = company
        if len(companies_by_ticker) == count_before:
            if limit is None:
                break
            raise ValueError(f"CompaniesMarketCap returned no new companies on page {page}")
        if progress_callback:
            progress_callback(page, len(companies_by_ticker), parsed[-1]["rank"])
        page += 1

    companies = sorted(companies_by_ticker.values(), key=lambda item: item["rank"])
    if limit is not None:
        companies = companies[:limit]
    if limit is not None and len(companies) < limit:
        raise ValueError(f"Expected {limit} companies, parsed {len(companies)}")
    return companies


def get_companies(force=False):
    now = time.time()
    with _cache_lock:
        if (
            not force
            and _cache["companies"] is not None
            and now - _cache["fetched_at"] < CACHE_SECONDS
        ):
            return _cache

    try:
        companies = fetch_top_market_cap_companies(COMPANY_UNIVERSE_LIMIT)
        payload = {"companies": companies, "fetched_at": time.time(), "error": None}
    except Exception as exc:
        with _cache_lock:
            stale = _cache["companies"]
        if stale:
            payload = {"companies": stale, "fetched_at": _cache["fetched_at"], "error": str(exc)}
        else:
            raise

    with _cache_lock:
        _cache.update(payload)
        return _cache


def db_connect():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_scoring_schema():
    with db_connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS scoring_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                prompt TEXT NOT NULL,
                model TEXT NOT NULL,
                reasoning_mode TEXT NOT NULL DEFAULT 'none',
                max_tokens INTEGER NOT NULL DEFAULT 200,
                low_cost_mode INTEGER NOT NULL DEFAULT 0,
                stock_list_id INTEGER,
                run_type TEXT NOT NULL DEFAULT 'scoring',
                minimum_confidence_score REAL,
                confidence_run_id INTEGER,
                manual_ranking_id INTEGER,
                status TEXT NOT NULL,
                company_count INTEGER NOT NULL DEFAULT 0,
                completed_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                queue_count INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                finished_at INTEGER,
                deleted_at INTEGER,
                starred INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                worker_pid INTEGER,
                worker_started_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS scoring_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                company_name TEXT NOT NULL,
                rank INTEGER NOT NULL,
                market_cap TEXT NOT NULL,
                market_cap_value INTEGER NOT NULL,
                price TEXT NOT NULL,
                country TEXT NOT NULL,
                score REAL,
                raw_response TEXT,
                error TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(run_id) REFERENCES scoring_runs(id),
                UNIQUE(run_id, ticker)
            );

            CREATE TABLE IF NOT EXISTS stock_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                deleted_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS stock_list_members (
                list_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY(list_id, ticker),
                FOREIGN KEY(list_id) REFERENCES stock_lists(id)
            );

            CREATE TABLE IF NOT EXISTS scoring_run_companies (
                run_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY(run_id, ticker),
                FOREIGN KEY(run_id) REFERENCES scoring_runs(id)
            );

            CREATE TABLE IF NOT EXISTS app_preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS manual_rankings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                stock_list_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                deleted_at INTEGER,
                FOREIGN KEY(stock_list_id) REFERENCES stock_lists(id)
            );

            CREATE TABLE IF NOT EXISTS manual_ranking_scores (
                manual_ranking_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                position INTEGER NOT NULL,
                score REAL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(manual_ranking_id, ticker),
                FOREIGN KEY(manual_ranking_id) REFERENCES manual_rankings(id)
            );

            CREATE INDEX IF NOT EXISTS idx_scoring_runs_created_at ON scoring_runs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_scoring_results_run_score ON scoring_results(run_id, score DESC);
            CREATE INDEX IF NOT EXISTS idx_stock_list_members_position ON stock_list_members(list_id, position);
            CREATE INDEX IF NOT EXISTS idx_scoring_run_companies_position ON scoring_run_companies(run_id, position);
            CREATE INDEX IF NOT EXISTS idx_manual_ranking_scores_position ON manual_ranking_scores(manual_ranking_id, position);
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(scoring_runs)").fetchall()
        }
        if "name" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN name TEXT")
        if "deleted_at" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN deleted_at INTEGER")
        if "worker_pid" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN worker_pid INTEGER")
        if "worker_started_at" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN worker_started_at INTEGER")
        if "reasoning_mode" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN reasoning_mode TEXT NOT NULL DEFAULT 'none'")
        if "max_tokens" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN max_tokens INTEGER NOT NULL DEFAULT 200")
        if "low_cost_mode" not in columns:
            connection.execute(
                "ALTER TABLE scoring_runs ADD COLUMN low_cost_mode INTEGER NOT NULL DEFAULT 0"
            )
        if "stock_list_id" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN stock_list_id INTEGER")
        if "starred" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN starred INTEGER NOT NULL DEFAULT 0")
        if "queue_count" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN queue_count INTEGER NOT NULL DEFAULT 0")
        if "run_type" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN run_type TEXT NOT NULL DEFAULT 'scoring'")
        if "minimum_confidence_score" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN minimum_confidence_score REAL")
        if "confidence_run_id" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN confidence_run_id INTEGER")
        if "manual_ranking_id" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN manual_ranking_id INTEGER")
        connection.execute(
            """
            UPDATE scoring_runs
            SET name = 'Run #' || id
            WHERE name IS NULL OR trim(name) = ''
            """
        )
        connection.execute(
            """
            UPDATE scoring_runs
            SET reasoning_mode = 'none'
            WHERE reasoning_mode IS NULL OR trim(reasoning_mode) = ''
            """
        )
        connection.execute(
            "UPDATE scoring_runs SET max_tokens = 200 WHERE max_tokens IS NULL OR max_tokens < 1"
        )
        connection.execute(
            """
            UPDATE scoring_runs
            SET run_type = 'confidence'
            WHERE run_type = 'scoring'
              AND (prompt = ? OR lower(name) LIKE 'us company confidence%')
            """,
            (CONFIDENCE_SCORE_PROMPT,),
        )
        connection.commit()


def run_table_columns_preference_key(view):
    normalized_view = str(view or "ranking").strip().lower()
    if normalized_view not in RUN_TABLE_COLUMNS_PREFERENCE_KEYS:
        raise ValueError("Unknown run table view.")
    return RUN_TABLE_COLUMNS_PREFERENCE_KEYS[normalized_view]


def run_table_column_order_preference_key(view):
    normalized_view = str(view or "ranking").strip().lower()
    if normalized_view not in RUN_TABLE_COLUMN_ORDER_PREFERENCE_KEYS:
        raise ValueError("Unknown run table view.")
    return RUN_TABLE_COLUMN_ORDER_PREFERENCE_KEYS[normalized_view]


def normalized_column_order(columns, allowed_columns, label):
    if not isinstance(columns, list):
        raise ValueError("Column order must be a list.")
    unknown_columns = [column for column in columns if column not in allowed_columns]
    if unknown_columns:
        raise ValueError(f"Unknown {label} column: {unknown_columns[0]}")
    ordered_columns = list(dict.fromkeys(columns))
    ordered_columns.extend(
        column for column in allowed_columns if column not in ordered_columns
    )
    return ordered_columns


def get_column_order_preference(preference_key, allowed_columns):
    with db_connect() as connection:
        row = connection.execute(
            "SELECT value FROM app_preferences WHERE key = ?",
            (preference_key,),
        ).fetchone()
    if not row:
        return list(allowed_columns)
    try:
        stored_order = json.loads(row["value"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return list(allowed_columns)
    if not isinstance(stored_order, list):
        return list(allowed_columns)
    valid_order = [column for column in stored_order if column in allowed_columns]
    valid_order.extend(column for column in allowed_columns if column not in valid_order)
    return list(dict.fromkeys(valid_order))


def save_column_order_preference(preference_key, columns, allowed_columns, label):
    ordered_columns = normalized_column_order(columns, allowed_columns, label)
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO app_preferences (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (preference_key, json.dumps(ordered_columns), int(time.time())),
        )
        connection.commit()
    return ordered_columns


def get_run_table_column_order_preference(view="ranking"):
    return get_column_order_preference(
        run_table_column_order_preference_key(view), RUN_TABLE_COLUMN_KEYS
    )


def save_run_table_column_order_preference(columns, view="ranking"):
    return save_column_order_preference(
        run_table_column_order_preference_key(view),
        columns,
        RUN_TABLE_COLUMN_KEYS,
        "run table",
    )


def get_run_table_columns_preference(view="ranking"):
    preference_key = run_table_columns_preference_key(view)
    with db_connect() as connection:
        row = connection.execute(
            "SELECT value FROM app_preferences WHERE key = ?",
            (preference_key,),
        ).fetchone()
        if not row:
            row = connection.execute(
                "SELECT value FROM app_preferences WHERE key = ?",
                (RUN_TABLE_COLUMNS_PREFERENCE_KEY,),
            ).fetchone()
    if not row:
        return None
    try:
        stored_value = json.loads(row["value"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    legacy_columns = isinstance(stored_value, list)
    stored_version = 1
    if legacy_columns:
        columns = stored_value
    elif isinstance(stored_value, dict):
        columns = stored_value.get("columns")
        try:
            stored_version = int(stored_value.get("version") or 1)
        except (TypeError, ValueError):
            stored_version = 1
    else:
        return None
    if not isinstance(columns, list):
        return None
    valid_columns = [column for column in columns if column in RUN_TABLE_COLUMN_KEYS]
    migrated = False
    if stored_version < 3 and "search" not in valid_columns:
        insert_at = valid_columns.index("actions") if "actions" in valid_columns else len(valid_columns)
        valid_columns.insert(insert_at, "search")
        migrated = True
    if stored_version < 4 and "chart" not in valid_columns:
        insert_at = valid_columns.index("actions") if "actions" in valid_columns else len(valid_columns)
        valid_columns.insert(insert_at, "chart")
        migrated = True
    if stored_version < 5 and "dashboard" not in valid_columns:
        insert_at = valid_columns.index("actions") if "actions" in valid_columns else len(valid_columns)
        valid_columns.insert(insert_at, "dashboard")
        migrated = True
    if stored_version < 6 and "confidence" not in valid_columns:
        insert_at = valid_columns.index("company") if "company" in valid_columns else len(valid_columns)
        valid_columns.insert(insert_at, "confidence")
        migrated = True
    if stored_version < 7:
        if preference_key == RUN_TABLE_COLUMNS_PREFERENCE_KEYS["ranking"] and "scorePercentile" not in valid_columns:
            insert_at = valid_columns.index("score") + 1 if "score" in valid_columns else 0
            valid_columns.insert(insert_at, "scorePercentile")
        migrated = True
    if migrated:
        save_run_table_columns_preference(valid_columns, view)
    elif valid_columns:
        with db_connect() as connection:
            has_view_preference = connection.execute(
                "SELECT 1 FROM app_preferences WHERE key = ?",
                (preference_key,),
            ).fetchone()
        if not has_view_preference:
            save_run_table_columns_preference(valid_columns, view)
    return valid_columns or None


def save_run_table_columns_preference(columns, view="ranking"):
    preference_key = run_table_columns_preference_key(view)
    if not isinstance(columns, list):
        raise ValueError("Columns must be a list.")
    unknown_columns = [column for column in columns if column not in RUN_TABLE_COLUMN_KEYS]
    if unknown_columns:
        raise ValueError(f"Unknown run table column: {unknown_columns[0]}")
    selected_columns = list(dict.fromkeys(columns))
    if not selected_columns:
        raise ValueError("Keep at least one run table column visible.")
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO app_preferences (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (
                preference_key,
                json.dumps({"version": 7, "columns": selected_columns}),
                int(time.time()),
            ),
        )
        connection.commit()
    return selected_columns


def get_provider_table_columns_preference():
    with db_connect() as connection:
        row = connection.execute(
            "SELECT value FROM app_preferences WHERE key = ?",
            (PROVIDER_TABLE_COLUMNS_PREFERENCE_KEY,),
        ).fetchone()
    if not row:
        return None
    try:
        stored_value = json.loads(row["value"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if isinstance(stored_value, list):
        columns = stored_value
        stored_version = 1
    elif isinstance(stored_value, dict):
        columns = stored_value.get("columns")
        try:
            stored_version = int(stored_value.get("version") or 1)
        except (TypeError, ValueError):
            stored_version = 1
    else:
        return None
    if not isinstance(columns, list):
        return None
    valid_columns = [column for column in columns if column in PROVIDER_TABLE_COLUMN_KEYS]
    if stored_version < 2:
        insert_at = (
            valid_columns.index("latency")
            if "latency" in valid_columns
            else len(valid_columns)
        )
        for column in ("inputCostPerMillion", "outputCostPerMillion"):
            if column not in valid_columns:
                valid_columns.insert(insert_at, column)
                insert_at += 1
        save_provider_table_columns_preference(valid_columns)
    return list(dict.fromkeys(valid_columns)) or None


def save_provider_table_columns_preference(columns):
    if not isinstance(columns, list):
        raise ValueError("Columns must be a list.")
    unknown_columns = [column for column in columns if column not in PROVIDER_TABLE_COLUMN_KEYS]
    if unknown_columns:
        raise ValueError(f"Unknown provider table column: {unknown_columns[0]}")
    selected_columns = list(dict.fromkeys(columns))
    if not selected_columns:
        raise ValueError("Keep at least one provider table column visible.")
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO app_preferences (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (
                PROVIDER_TABLE_COLUMNS_PREFERENCE_KEY,
                json.dumps({"version": 2, "columns": selected_columns}),
                int(time.time()),
            ),
        )
        connection.commit()
    return selected_columns


def get_provider_table_column_order_preference():
    return get_column_order_preference(
        PROVIDER_TABLE_COLUMN_ORDER_PREFERENCE_KEY,
        PROVIDER_TABLE_COLUMN_KEYS,
    )


def save_provider_table_column_order_preference(columns):
    return save_column_order_preference(
        PROVIDER_TABLE_COLUMN_ORDER_PREFERENCE_KEY,
        columns,
        PROVIDER_TABLE_COLUMN_KEYS,
        "provider table",
    )


def get_portfolio_table_columns_preference():
    with db_connect() as connection:
        row = connection.execute(
            "SELECT value FROM app_preferences WHERE key = ?",
            (PORTFOLIO_TABLE_COLUMNS_PREFERENCE_KEY,),
        ).fetchone()
    if not row:
        return None
    try:
        columns = json.loads(row["value"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(columns, list):
        return None
    valid_columns = [
        column for column in columns if column in PORTFOLIO_TABLE_COLUMN_KEYS
    ]
    return valid_columns or None


def save_portfolio_table_columns_preference(columns):
    if not isinstance(columns, list):
        raise ValueError("Columns must be a list.")
    unknown_columns = [
        column for column in columns if column not in PORTFOLIO_TABLE_COLUMN_KEYS
    ]
    if unknown_columns:
        raise ValueError(f"Unknown portfolio table column: {unknown_columns[0]}")
    selected_columns = list(dict.fromkeys(columns))
    if not selected_columns:
        raise ValueError("Keep at least one portfolio table column visible.")
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO app_preferences (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (
                PORTFOLIO_TABLE_COLUMNS_PREFERENCE_KEY,
                json.dumps(selected_columns),
                int(time.time()),
            ),
        )
        connection.commit()
    return selected_columns


def get_portfolio_table_column_order_preference():
    return get_column_order_preference(
        PORTFOLIO_TABLE_COLUMN_ORDER_PREFERENCE_KEY,
        PORTFOLIO_TABLE_COLUMN_KEYS,
    )


def save_portfolio_table_column_order_preference(columns):
    return save_column_order_preference(
        PORTFOLIO_TABLE_COLUMN_ORDER_PREFERENCE_KEY,
        columns,
        PORTFOLIO_TABLE_COLUMN_KEYS,
        "portfolio table",
    )


def row_to_company(row):
    return {
        "rank": row["rank"],
        "name": row["name"],
        "ticker": row["ticker"],
        "marketCap": row["market_cap"],
        "marketCapValue": row["market_cap_value"],
        "price": row["price"],
        "today": row["today"],
        "country": row["country"],
        "logo": row["logo"],
    }


def add_score_percentiles(results):
    scored = sorted(
        (float(result["score"]), index)
        for index, result in enumerate(results)
        if result.get("score") is not None and not result.get("error")
    )
    for result in results:
        result["score_percentile"] = None
    sample_size = len(scored)
    start = 0
    while start < sample_size:
        end = start + 1
        while end < sample_size and scored[end][0] == scored[start][0]:
            end += 1
        average_zero_based_rank = (start + end - 1) / 2
        percentile = (
            100.0
            if sample_size == 1
            else average_zero_based_rank / (sample_size - 1) * 100
        )
        for _, result_index in scored[start:end]:
            results[result_index]["score_percentile"] = percentile
        start = end
    return results


def db_companies(active_only=True):
    where_clause = "WHERE fetched_at = (SELECT MAX(fetched_at) FROM companies)" if active_only else ""
    with db_connect() as connection:
        rows = connection.execute(
            f"""
            SELECT ticker, rank, name, market_cap, market_cap_value, price, today, country, logo
            FROM companies
            {where_clause}
            ORDER BY rank
            """
        ).fetchall()
    return [row_to_company(row) for row in rows]


def paginated_companies(page=1, page_size=100, query="", sort_key="rank", direction="asc"):
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 100)))
    query = str(query or "").strip()
    direction = "DESC" if str(direction).lower() == "desc" else "ASC"
    sort_columns = {
        "rank": "rank",
        "name": "name COLLATE NOCASE",
        "marketCapValue": "market_cap_value",
        "country": "country COLLATE NOCASE",
    }
    sort_column = sort_columns.get(sort_key, "rank")
    where_parts = ["fetched_at = (SELECT MAX(fetched_at) FROM companies)"]
    parameters = []
    if query:
        where_parts.append("(name LIKE ? COLLATE NOCASE OR ticker LIKE ? COLLATE NOCASE)")
        match = f"%{query}%"
        parameters.extend([match, match])
    where_clause = " AND ".join(where_parts)

    with db_connect() as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM companies WHERE {where_clause}", parameters
        ).fetchone()[0]
        total_pages = max(1, math.ceil(total / page_size))
        page = min(page, total_pages)
        offset = (page - 1) * page_size
        rows = connection.execute(
            f"""
            SELECT ticker, rank, name, market_cap, market_cap_value, price, today, country, logo
            FROM companies
            WHERE {where_clause}
            ORDER BY {sort_column} {direction}, rank ASC
            LIMIT ? OFFSET ?
            """,
            [*parameters, page_size, offset],
        ).fetchall()
    return {
        "companies": [row_to_company(row) for row in rows],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "offset": offset,
        },
    }


def scoring_companies(company_count):
    return db_companies()[:company_count]


def normalize_stock_list_name(value):
    name = (value or "").strip()
    if not name:
        raise ValueError("List name is required.")
    if len(name) > 120:
        raise ValueError("List name must be 120 characters or fewer.")
    return name


def companies_for_tickers(tickers):
    requested = []
    seen = set()
    for ticker in tickers or []:
        normalized = str(ticker or "").strip().upper()
        if normalized and normalized not in seen:
            requested.append(normalized)
            seen.add(normalized)
    if not requested:
        raise ValueError("Choose at least one stock for the list.")

    companies = {company["ticker"].upper(): company for company in db_companies(active_only=False)}
    missing = [ticker for ticker in requested if ticker not in companies]
    if missing:
        raise ValueError(f"Unknown ticker{'s' if len(missing) != 1 else ''}: {', '.join(missing)}")
    return [companies[ticker] for ticker in requested]


def get_stock_list(list_id):
    try:
        list_id = int(list_id)
    except (TypeError, ValueError):
        return None
    with db_connect() as connection:
        stock_list = connection.execute(
            """
            SELECT id, name, created_at, updated_at
            FROM stock_lists
            WHERE id = ? AND deleted_at IS NULL
            """,
            (list_id,),
        ).fetchone()
        if not stock_list:
            return None
        rows = connection.execute(
            """
            SELECT companies.ticker, companies.rank, companies.name, companies.market_cap,
                   companies.market_cap_value, companies.price, companies.today,
                   companies.country, companies.logo
            FROM stock_list_members
            JOIN companies ON companies.ticker = stock_list_members.ticker
            WHERE stock_list_members.list_id = ?
            ORDER BY stock_list_members.position
            """,
            (list_id,),
        ).fetchall()
    payload = dict(stock_list)
    payload["companies"] = [row_to_company(row) for row in rows]
    payload["company_count"] = len(payload["companies"])
    return payload


def list_stock_lists():
    with db_connect() as connection:
        ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM stock_lists WHERE deleted_at IS NULL ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        ]
    return [stock_list for list_id in ids if (stock_list := get_stock_list(list_id))]


def save_stock_list(name, tickers, list_id=None):
    name = normalize_stock_list_name(name)
    companies = companies_for_tickers(tickers)
    now = int(time.time())
    with db_connect() as connection:
        if list_id is None:
            cursor = connection.execute(
                "INSERT INTO stock_lists (name, created_at, updated_at) VALUES (?, ?, ?)",
                (name, now, now),
            )
            list_id = cursor.lastrowid
        else:
            try:
                list_id = int(list_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("Stock list not found.") from exc
            existing = connection.execute(
                "SELECT id FROM stock_lists WHERE id = ? AND deleted_at IS NULL",
                (list_id,),
            ).fetchone()
            if not existing:
                return None
            connection.execute(
                "UPDATE stock_lists SET name = ?, updated_at = ? WHERE id = ?",
                (name, now, list_id),
            )
            connection.execute("DELETE FROM stock_list_members WHERE list_id = ?", (list_id,))

        connection.executemany(
            "INSERT INTO stock_list_members (list_id, ticker, position) VALUES (?, ?, ?)",
            [(list_id, company["ticker"], position) for position, company in enumerate(companies)],
        )
        connection.commit()
    return get_stock_list(list_id)


def archive_stock_list(list_id):
    now = int(time.time())
    with db_connect() as connection:
        cursor = connection.execute(
            "UPDATE stock_lists SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
            (now, now, list_id),
        )
        connection.commit()
    return cursor.rowcount > 0


def normalize_manual_ranking_name(name):
    name = str(name or "").strip()
    if not name:
        raise ValueError("Manual ranking name is required.")
    if len(name) > 120:
        raise ValueError("Manual ranking name must be 120 characters or fewer.")
    return name


def get_manual_ranking(ranking_id, include_companies=True):
    try:
        ranking_id = int(ranking_id)
    except (TypeError, ValueError):
        return None
    with db_connect() as connection:
        ranking = connection.execute(
            """
            SELECT manual_rankings.id, manual_rankings.name, manual_rankings.stock_list_id,
                   stock_lists.name AS stock_list_name, manual_rankings.created_at,
                   manual_rankings.updated_at
            FROM manual_rankings
            JOIN stock_lists ON stock_lists.id = manual_rankings.stock_list_id
            WHERE manual_rankings.id = ? AND manual_rankings.deleted_at IS NULL
            """,
            (ranking_id,),
        ).fetchone()
        if not ranking:
            return None
        rows = connection.execute(
            """
            SELECT manual_ranking_scores.ticker, manual_ranking_scores.position,
                   manual_ranking_scores.score, companies.name, companies.rank,
                   companies.market_cap, companies.market_cap_value, companies.price,
                   companies.today, companies.country, companies.logo
            FROM manual_ranking_scores
            JOIN companies ON companies.ticker = manual_ranking_scores.ticker
            WHERE manual_ranking_scores.manual_ranking_id = ?
            ORDER BY manual_ranking_scores.score IS NULL,
                     manual_ranking_scores.score DESC,
                     manual_ranking_scores.position
            """,
            (ranking_id,),
        ).fetchall()
    payload = dict(ranking)
    companies = []
    for row in rows:
        company = row_to_company(row)
        company["score"] = row["score"]
        company["position"] = row["position"]
        companies.append(company)
    add_score_percentiles(companies)
    scored = [company for company in companies if company["score"] is not None]
    scored.sort(key=lambda company: (-float(company["score"]), company["position"]))
    rank_by_ticker = {company["ticker"]: index for index, company in enumerate(scored, 1)}
    for company in companies:
        company["manual_rank"] = rank_by_ticker.get(company["ticker"])
    payload["company_count"] = len(companies)
    payload["scored_count"] = len(scored)
    if include_companies:
        payload["companies"] = companies
    return payload


def list_manual_rankings():
    with db_connect() as connection:
        ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM manual_rankings WHERE deleted_at IS NULL ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        ]
    return [ranking for ranking_id in ids if (ranking := get_manual_ranking(ranking_id, False))]


def create_manual_ranking(name, stock_list_id):
    name = normalize_manual_ranking_name(name)
    stock_list = get_stock_list(stock_list_id)
    if not stock_list:
        raise ValueError("Choose a saved stock list.")
    now = int(time.time())
    with db_connect() as connection:
        cursor = connection.execute(
            "INSERT INTO manual_rankings (name, stock_list_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name, int(stock_list_id), now, now),
        )
        ranking_id = cursor.lastrowid
        connection.executemany(
            """
            INSERT INTO manual_ranking_scores
                (manual_ranking_id, ticker, position, score, updated_at)
            VALUES (?, ?, ?, NULL, ?)
            """,
            [
                (ranking_id, company["ticker"], position, now)
                for position, company in enumerate(stock_list["companies"])
            ],
        )
        connection.commit()
    return get_manual_ranking(ranking_id)


def update_manual_ranking_score(ranking_id, ticker, score):
    ranking = get_manual_ranking(ranking_id, False)
    if not ranking:
        return None
    ticker = str(ticker or "").strip().upper()
    if score in (None, ""):
        normalized_score = None
    else:
        try:
            normalized_score = float(score)
        except (TypeError, ValueError) as exc:
            raise ValueError("Score must be a number from 0 to 100.") from exc
        if not math.isfinite(normalized_score) or not 0 <= normalized_score <= 100:
            raise ValueError("Score must be a number from 0 to 100.")
    now = int(time.time())
    with db_connect() as connection:
        cursor = connection.execute(
            """
            UPDATE manual_ranking_scores
            SET score = ?, updated_at = ?
            WHERE manual_ranking_id = ? AND ticker = ?
            """,
            (normalized_score, now, int(ranking_id), ticker),
        )
        if not cursor.rowcount:
            raise ValueError("Stock is not part of this manual ranking.")
        connection.execute(
            "UPDATE manual_rankings SET updated_at = ? WHERE id = ?",
            (now, int(ranking_id)),
        )
        connection.commit()
    return get_manual_ranking(ranking_id)


def archive_manual_ranking(ranking_id):
    now = int(time.time())
    with db_connect() as connection:
        cursor = connection.execute(
            "UPDATE manual_rankings SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
            (now, now, ranking_id),
        )
        if cursor.rowcount:
            connection.execute(
                "UPDATE scoring_runs SET manual_ranking_id = NULL WHERE manual_ranking_id = ?",
                (ranking_id,),
            )
        connection.commit()
    return cursor.rowcount > 0


def pearson_correlation(points):
    if len(points) < 2:
        return None
    left_mean = sum(left for left, _ in points) / len(points)
    right_mean = sum(right for _, right in points) / len(points)
    covariance = sum(
        (left - left_mean) * (right - right_mean) for left, right in points
    )
    left_variance = sum((left - left_mean) ** 2 for left, _ in points)
    right_variance = sum((right - right_mean) ** 2 for _, right in points)
    denominator = math.sqrt(left_variance * right_variance)
    return covariance / denominator if denominator else None


def manual_ranking_comparison(results, ranking_id):
    if not ranking_id:
        return None
    ranking = get_manual_ranking(ranking_id)
    if not ranking:
        return None
    manual_percentiles = {
        company["ticker"].upper(): company["score_percentile"]
        for company in ranking["companies"]
        if company.get("score_percentile") is not None
    }
    points = []
    for result in results:
        ticker = str(result.get("ticker") or "").upper()
        if result.get("score_percentile") is None or ticker not in manual_percentiles:
            continue
        points.append((float(result["score_percentile"]), float(manual_percentiles[ticker])))
    return {
        "manual_ranking_id": ranking["id"],
        "name": ranking["name"],
        "correlation": pearson_correlation(points),
        "sample_size": len(points),
    }


def set_run_manual_ranking(run_id, ranking_id):
    if ranking_id in (None, ""):
        normalized_id = None
    else:
        try:
            normalized_id = int(ranking_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Manual ranking not found.") from exc
        if not get_manual_ranking(normalized_id, False):
            raise ValueError("Manual ranking not found.")
    with db_connect() as connection:
        cursor = connection.execute(
            "UPDATE scoring_runs SET manual_ranking_id = ? WHERE id = ? AND deleted_at IS NULL",
            (normalized_id, run_id),
        )
        connection.commit()
    return get_run(run_id) if cursor.rowcount else None


def snapshot_run_companies(connection, run_id, companies):
    connection.execute("DELETE FROM scoring_run_companies WHERE run_id = ?", (run_id,))
    connection.executemany(
        "INSERT INTO scoring_run_companies (run_id, ticker, position) VALUES (?, ?, ?)",
        [(run_id, company["ticker"], position) for position, company in enumerate(companies)],
    )


def scoring_companies_for_run(run_id):
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT companies.ticker, companies.rank, companies.name, companies.market_cap,
                   companies.market_cap_value, companies.price, companies.today,
                   companies.country, companies.logo
            FROM scoring_run_companies
            JOIN companies ON companies.ticker = scoring_run_companies.ticker
            WHERE scoring_run_companies.run_id = ?
            ORDER BY scoring_run_companies.position
            """,
            (run_id,),
        ).fetchall()
        if rows:
            return [row_to_company(row) for row in rows]
        run = connection.execute(
            "SELECT company_count FROM scoring_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    return scoring_companies(run["company_count"]) if run else []


def extension_companies_for_run(run_id, stock_list_id=None):
    existing = scoring_companies_for_run(run_id)
    if stock_list_id is not None:
        stock_list = get_stock_list(stock_list_id)
        source = stock_list["companies"] if stock_list else []
    else:
        source = db_companies()

    companies = list(existing)
    seen = {company["ticker"] for company in companies}
    for company in source:
        if company["ticker"] in seen:
            continue
        companies.append(company)
        seen.add(company["ticker"])
    return companies


def normalize_minimum_confidence_score(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Minimum confidence score must be between 0 and 100.") from exc
    if not 0 <= score <= 100:
        raise ValueError("Minimum confidence score must be between 0 and 100.")
    return score


def pinned_confidence_run_id():
    with db_connect() as connection:
        row = connection.execute(
            "SELECT value FROM app_preferences WHERE key = ?",
            (PINNED_CONFIDENCE_RUN_KEY,),
        ).fetchone()
        if not row:
            return None
        try:
            run_id = int(row["value"])
        except (TypeError, ValueError):
            return None
        run = connection.execute(
            "SELECT id FROM scoring_runs WHERE id = ? AND run_type = 'confidence' AND deleted_at IS NULL",
            (run_id,),
        ).fetchone()
    return run_id if run else None


def confidence_scores_for_run(run_id):
    if not run_id:
        return {}
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT ticker, score
            FROM scoring_results
            WHERE run_id = ? AND score IS NOT NULL AND error IS NULL
            """,
            (run_id,),
        ).fetchall()
    return {(row["ticker"] or "").upper(): row["score"] for row in rows}


def set_pinned_confidence_run(run_id):
    try:
        run_id = int(run_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Confidence run was not found.") from exc
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT id, name, status, company_count, completed_count, failed_count, created_at
            FROM scoring_runs
            WHERE id = ? AND run_type = 'confidence' AND deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
        if not run:
            raise ValueError("Confidence run was not found.")
        connection.execute(
            """
            INSERT INTO app_preferences (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (PINNED_CONFIDENCE_RUN_KEY, str(run_id), int(time.time())),
        )
        connection.commit()
    payload = dict(run)
    payload["pinned"] = True
    return payload


def filter_companies_by_confidence(companies, minimum_score, confidence_run_id=None):
    minimum_score = normalize_minimum_confidence_score(minimum_score)
    if minimum_score is None:
        return list(companies), None, 0
    confidence_run_id = confidence_run_id or pinned_confidence_run_id()
    if not confidence_run_id:
        raise ValueError("Pin a confidence score run before setting a minimum confidence score.")
    scores = confidence_scores_for_run(confidence_run_id)
    eligible = [
        company
        for company in companies
        if scores.get((company["ticker"] or "").upper()) is not None
        and float(scores[(company["ticker"] or "").upper()]) >= minimum_score
    ]
    return eligible, confidence_run_id, len(companies) - len(eligible)


def companies_for_run_universe(stock_list_id):
    if stock_list_id is None:
        return None, db_companies()
    try:
        normalized_id = int(stock_list_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Selected stock universe was not found.") from exc
    stock_list = get_stock_list(normalized_id)
    if not stock_list:
        raise ValueError("Selected stock universe was not found.")
    return stock_list["id"], stock_list["companies"]


def run_universe_options(run_id):
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT scoring_runs.id, scoring_runs.stock_list_id, stock_lists.name AS stock_list_name
            FROM scoring_runs
            LEFT JOIN stock_lists ON stock_lists.id = scoring_runs.stock_list_id
            WHERE scoring_runs.id = ? AND scoring_runs.deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
    if not run:
        return None

    current_tickers = {company["ticker"] for company in scoring_companies_for_run(run_id)}

    def option(stock_list_id, name, companies, current=False, archived=False):
        candidate_tickers = {company["ticker"] for company in companies}
        missing_count = len(current_tickers - candidate_tickers)
        return {
            "stock_list_id": stock_list_id,
            "name": name,
            "company_count": len(companies),
            "eligible": missing_count == 0,
            "missing_count": missing_count,
            "current": current,
            "archived": archived,
        }

    options = [
        option(
            None,
            "Top companies by market cap",
            db_companies(),
            current=run["stock_list_id"] is None,
        )
    ]
    for stock_list in list_stock_lists():
        options.append(
            option(
                stock_list["id"],
                stock_list["name"],
                stock_list["companies"],
                current=stock_list["id"] == run["stock_list_id"],
            )
        )

    if run["stock_list_id"] is not None and not any(item["current"] for item in options):
        options.append(
            {
                "stock_list_id": run["stock_list_id"],
                "name": run["stock_list_name"] or "Current archived universe",
                "company_count": len(current_tickers),
                "eligible": True,
                "missing_count": 0,
                "current": True,
                "archived": True,
            }
        )
    return options


def normalize_company_count(value):
    companies_available = len(db_companies())
    if companies_available <= 0:
        raise RuntimeError("No companies found. Run ./fetch_companies_to_db.py first.")
    try:
        company_count = int(value)
    except (TypeError, ValueError):
        company_count = DEFAULT_SCORING_COMPANY_COUNT
    if company_count < 1:
        raise ValueError("Company count must be at least 1.")
    if company_count > companies_available:
        raise ValueError(f"Company count cannot exceed {companies_available}.")
    return company_count


def normalize_run_name(value):
    name = (value or "").strip()
    if not name:
        raise ValueError("Run name is required.")
    if len(name) > 120:
        raise ValueError("Run name must be 120 characters or fewer.")
    return name


def normalize_scoring_prompt(value):
    prompt = (value or "").strip()
    if not prompt:
        raise ValueError("Prompt is required")
    if not prompt_has_company_keyword(prompt):
        raise ValueError("Prompt must include the COMPANY keyword.")
    return prompt


def process_is_running(pid):
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_scoring_worker_process(run_id, start_index=0, target_tickers=None):
    command = [sys.executable, str(SCORING_WORKER_PATH), str(run_id), str(start_index)]
    if target_tickers:
        command.append(",".join(target_tickers))
    with SCORING_WORKER_LOG_PATH.open("ab") as log_file:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            close_fds=True,
        )

    with db_connect() as connection:
        connection.execute(
            """
            UPDATE scoring_runs
            SET worker_pid = ?, worker_started_at = ?
            WHERE id = ?
            """,
            (process.pid, int(time.time()), run_id),
        )
        connection.commit()
    return process.pid


def create_scoring_run(
    name,
    prompt,
    model,
    company_count=None,
    reasoning_mode=None,
    stock_list_id=None,
    tickers=None,
    max_tokens=None,
    run_type="scoring",
    minimum_confidence_score=None,
    low_cost_mode=False,
):
    if not os.environ.get("OPENROUTER_KEY"):
        raise RuntimeError("OPENROUTER_KEY is not set")

    name = normalize_run_name(name)
    prompt = normalize_scoring_prompt(prompt)
    model = normalize_model(model)
    reasoning_mode = normalize_reasoning_mode(reasoning_mode, model)
    max_tokens = normalize_max_tokens(max_tokens)
    low_cost_mode = normalize_low_cost_mode(low_cost_mode)
    run_type = str(run_type or "scoring").strip().lower()
    if run_type not in ("scoring", "confidence"):
        raise ValueError("Unknown scoring run type.")
    minimum_confidence_score = normalize_minimum_confidence_score(minimum_confidence_score)
    selected_list = None
    if stock_list_id is not None:
        if tickers is not None:
            try:
                stock_list_id = int(stock_list_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("Selected stock list was not found.") from exc
        else:
            selected_list = get_stock_list(stock_list_id)
            if not selected_list:
                raise ValueError("Selected stock list was not found.")
            stock_list_id = selected_list["id"]

    if tickers is not None:
        companies = companies_for_tickers(tickers)
    elif selected_list:
        companies = selected_list["companies"]
    else:
        company_count = normalize_company_count(company_count)
        companies = scoring_companies(company_count)
    if (tickers is not None or selected_list) and company_count is not None:
        company_count = normalize_company_count(company_count)
        if company_count > len(companies):
            raise ValueError(f"Company count cannot exceed {len(companies)} for the selected stock list.")
        companies = companies[:company_count]
    if not companies:
        raise RuntimeError("No companies found. Run ./fetch_companies_to_db.py first.")
    confidence_run_id = None
    if run_type == "scoring":
        companies, confidence_run_id, _ = filter_companies_by_confidence(
            companies, minimum_confidence_score
        )
        if not companies:
            raise ValueError("No stocks in this selection meet the minimum confidence score.")

    now = int(time.time())
    with db_connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO scoring_runs (
                name, prompt, model, reasoning_mode, max_tokens, low_cost_mode, stock_list_id, run_type,
                minimum_confidence_score, confidence_run_id, status,
                company_count, queue_count, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                prompt,
                model,
                reasoning_mode,
                max_tokens,
                1 if low_cost_mode else 0,
                stock_list_id,
                run_type,
                minimum_confidence_score,
                confidence_run_id,
                "queued",
                len(companies),
                len(companies),
                now,
            ),
        )
        run_id = cursor.lastrowid
        snapshot_run_companies(connection, run_id, companies)
        connection.commit()

    start_scoring_worker_process(run_id)
    return run_id


def preview_scoring_selection(company_count=None, stock_list_id=None, tickers=None, minimum_confidence_score=None):
    selected_list = None
    if stock_list_id is not None and tickers is None:
        selected_list = get_stock_list(stock_list_id)
        if not selected_list:
            raise ValueError("Selected stock list was not found.")
    if tickers is not None:
        companies = companies_for_tickers(tickers)
    elif selected_list:
        companies = selected_list["companies"]
    else:
        companies = scoring_companies(normalize_company_count(company_count))
    if (tickers is not None or selected_list) and company_count is not None:
        requested_count = normalize_company_count(company_count)
        if requested_count > len(companies):
            raise ValueError(f"Company count cannot exceed {len(companies)} for the selected stock list.")
        companies = companies[:requested_count]
    selected_count = len(companies)
    eligible, confidence_run_id, excluded_count = filter_companies_by_confidence(
        companies, minimum_confidence_score
    )
    return {
        "selected_count": selected_count,
        "eligible_count": len(eligible),
        "excluded_count": excluded_count,
        "confidence_run_id": confidence_run_id,
    }


def extend_scoring_run(run_id, company_count):
    target_count = normalize_company_count(company_count)
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT id, status, company_count, stock_list_id,
                   minimum_confidence_score, confidence_run_id
            FROM scoring_runs
            WHERE id = ? AND deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return None
        if run["status"] in ("queued", "running", "stop_requested"):
            raise ValueError("Wait for the current run to finish before extending it.")
        if target_count <= run["company_count"]:
            raise ValueError(f"Choose a stock count above {run['company_count']}.")
        extension_companies = extension_companies_for_run(run_id, run["stock_list_id"])
        if run["minimum_confidence_score"] is not None:
            extension_companies, _, _ = filter_companies_by_confidence(
                extension_companies,
                run["minimum_confidence_score"],
                run["confidence_run_id"],
            )
        if target_count > len(extension_companies):
            raise ValueError(
                f"Company count cannot exceed {len(extension_companies)} for this run's stock universe."
            )

        connection.execute(
            """
            UPDATE scoring_runs
            SET company_count = ?, queue_count = ?, status = ?, started_at = ?,
                finished_at = NULL, error = NULL
            WHERE id = ?
            """,
            (
                target_count,
                target_count - run["company_count"],
                "queued",
                int(time.time()),
                run_id,
            ),
        )
        snapshot_run_companies(connection, run_id, extension_companies[:target_count])
        connection.commit()
        start_index = run["company_count"]

    start_scoring_worker_process(run_id, start_index)
    return get_run(run_id)


def failed_tickers_for_run(run_id):
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT ticker
            FROM scoring_results
            WHERE run_id = ? AND error IS NOT NULL
            ORDER BY rank ASC
            """,
            (run_id,),
        ).fetchall()
    return [row["ticker"] for row in rows]


def redrive_failed_scoring_run(run_id, requested_tickers=None):
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT id, status
            FROM scoring_runs
            WHERE id = ? AND deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return None
        if run["status"] in ("queued", "running", "stop_requested"):
            raise ValueError("Wait for the current run to finish before redriving failed stocks.")
        failed_tickers = failed_tickers_for_run(run_id)
        if not failed_tickers:
            raise ValueError("This run has no failed stocks to redrive.")
        if requested_tickers is None:
            target_tickers = failed_tickers
        else:
            requested = {
                str(ticker).strip().upper()
                for ticker in requested_tickers
                if str(ticker).strip()
            }
            target_tickers = [
                ticker for ticker in failed_tickers if ticker.upper() in requested
            ]
            if not requested or len(target_tickers) != len(requested):
                raise ValueError("Only currently failed stocks can be redriven.")

        connection.execute(
            """
            UPDATE scoring_runs
            SET status = ?, queue_count = ?, started_at = ?, finished_at = NULL, error = NULL
            WHERE id = ?
            """,
            ("queued", len(target_tickers), int(time.time()), run_id),
        )
        connection.commit()

    start_scoring_worker_process(run_id, target_tickers=target_tickers)
    return get_run(run_id)


def get_run(run_id, include_raw_response=True):
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT scoring_runs.id, scoring_runs.name, scoring_runs.prompt, scoring_runs.model,
                   scoring_runs.reasoning_mode, scoring_runs.max_tokens, scoring_runs.low_cost_mode,
                   scoring_runs.stock_list_id,
                   scoring_runs.starred, scoring_runs.run_type,
                   scoring_runs.minimum_confidence_score, scoring_runs.confidence_run_id,
                   scoring_runs.manual_ranking_id,
                   stock_lists.name AS stock_list_name,
                   scoring_runs.status, scoring_runs.company_count, scoring_runs.completed_count,
                   scoring_runs.failed_count, scoring_runs.queue_count,
                   scoring_runs.created_at, scoring_runs.started_at,
                   scoring_runs.finished_at, scoring_runs.error
            FROM scoring_runs
            LEFT JOIN stock_lists ON stock_lists.id = scoring_runs.stock_list_id
            WHERE scoring_runs.id = ? AND scoring_runs.deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return None
        raw_response_column = ", raw_response" if include_raw_response else ""
        results = connection.execute(
            f"""
            SELECT scoring_results.ticker, company_name, scoring_results.rank,
                   scoring_results.market_cap, scoring_results.market_cap_value,
                   scoring_results.price, scoring_results.country,
                   score{raw_response_column}, error, created_at
            FROM scoring_results
            WHERE run_id = ?
            ORDER BY score IS NULL, score DESC, rank ASC
            """,
            (run_id,),
        ).fetchall()
        logo_rows = connection.execute(
            """
            SELECT ticker, logo
            FROM companies
            WHERE ticker IN (SELECT ticker FROM scoring_results WHERE run_id = ?)
            """,
            (run_id,),
        ).fetchall()
        logos = {row["ticker"]: row["logo"] for row in logo_rows}
    result_stats = ai_request_stats_by_ticker(run_id)
    current_confidence_run_id = pinned_confidence_run_id()
    current_confidence_scores = confidence_scores_for_run(current_confidence_run_id)
    payload = dict(run)
    payload["low_cost_mode"] = bool(payload.get("low_cost_mode"))
    payload["results"] = []
    for row in results:
        result = dict(row)
        stats = result_stats.get((result["ticker"] or "").upper(), {})
        result["total_tokens"] = stats.get("total_tokens", 0)
        result["prompt_tokens"] = stats.get("prompt_tokens", 0)
        result["completion_tokens"] = stats.get("completion_tokens", 0)
        result["response_tokens"] = stats.get("response_tokens", 0)
        result["reasoning_tokens"] = stats.get("reasoning_tokens", 0)
        result["token_budget"] = stats.get("token_budget", 0)
        result["token_budget_used_percent"] = stats.get("token_budget_used_percent")
        result["duration_ms"] = stats.get("duration_ms")
        result["cost"] = stats.get("cost", 0)
        result["logo"] = logos.get(result["ticker"], "")
        result["confidence_score"] = current_confidence_scores.get((result["ticker"] or "").upper())
        payload["results"].append(result)
    add_score_percentiles(payload["results"])
    payload["manual_comparison"] = manual_ranking_comparison(
        payload["results"], payload.get("manual_ranking_id")
    )
    payload["model_details"] = model_details(run["model"], run["reasoning_mode"])
    payload["stats"] = ai_request_stats_for_run(run_id, run["max_tokens"])
    payload["provider_stats"] = provider_stats_for_run(run_id)
    payload["stats"]["recent_average_latency_ms"] = recent_average_latency_ms(run["model"])
    payload["scoring_concurrency"] = scoring_concurrency()
    payload["incomplete_count"] = incomplete_company_count(run_id)
    payload["company_tickers"] = [company["ticker"] for company in scoring_companies_for_run(run_id)]
    payload["extension_limit"] = len(extension_companies_for_run(run_id, run["stock_list_id"]))
    payload["pinned_confidence_run_id"] = current_confidence_run_id
    return payload


def calculate_portfolio(
    run_id,
    name,
    market_cap_limit,
    minimum_score_percentile,
    maximum_multiplier,
    base_weighting="market_cap",
):
    portfolio_name = str(name or "").strip()
    if not portfolio_name:
        raise ValueError("Portfolio name is required.")
    if len(portfolio_name) > 120:
        raise ValueError("Portfolio name must be 120 characters or fewer.")
    try:
        market_cap_limit = int(market_cap_limit)
    except (TypeError, ValueError):
        raise ValueError("Market-cap universe size must be a whole number.")
    try:
        minimum_score_percentile = float(minimum_score_percentile)
        maximum_multiplier = float(maximum_multiplier)
    except (TypeError, ValueError):
        raise ValueError("Percentile and multiplier must be numbers.")
    if market_cap_limit < 1:
        raise ValueError("Market-cap universe size must be at least 1.")
    if not 0 <= minimum_score_percentile <= 100:
        raise ValueError("Minimum score percentile must be from 0 to 100.")
    if not 1 <= maximum_multiplier <= 100:
        raise ValueError("Maximum score multiplier must be from 1 to 100.")
    base_weighting = str(base_weighting or "market_cap").strip().lower()
    if base_weighting not in {"market_cap", "equal"}:
        raise ValueError("Starting weights must be market cap weighted or equal weighted.")

    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT id, name, prompt
            FROM scoring_runs
            WHERE id = ? AND deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
        if not run:
            raise ValueError("Run not found.")
        rows = connection.execute(
            """
            SELECT scoring_results.ticker, scoring_results.company_name,
                   scoring_results.rank, scoring_results.market_cap,
                   scoring_results.market_cap_value, scoring_results.score,
                   companies.logo
            FROM scoring_results
            LEFT JOIN companies ON companies.ticker = scoring_results.ticker
            WHERE scoring_results.run_id = ?
              AND scoring_results.score IS NOT NULL
              AND scoring_results.error IS NULL
              AND scoring_results.market_cap_value > 0
            ORDER BY scoring_results.market_cap_value DESC,
                     scoring_results.rank ASC, scoring_results.ticker ASC
            """,
            (run_id,),
        ).fetchall()
        if not rows:
            raise ValueError("This run has no successful scores with market-cap data.")
        if market_cap_limit > len(rows):
            raise ValueError(
                f"Choose no more than {len(rows)} successfully scored companies."
            )

        candidates = [dict(row) for row in rows[:market_cap_limit]]
        add_score_percentiles(candidates)
        holdings = [
            candidate
            for candidate in candidates
            if candidate["score_percentile"] is not None
            and candidate["score_percentile"] >= minimum_score_percentile
        ]
        if not holdings:
            raise ValueError("These rules do not select any stocks.")

        percentile_span = 100 - minimum_score_percentile
        for holding in holdings:
            if percentile_span <= 0:
                multiplier = maximum_multiplier
            else:
                percentile_progress = (
                    holding["score_percentile"] - minimum_score_percentile
                ) / percentile_span
                multiplier = 1 + percentile_progress * (maximum_multiplier - 1)
            holding["score_multiplier"] = multiplier
            holding["weighting_base_value"] = (
                holding["market_cap_value"] if base_weighting == "market_cap" else 1
            )
            holding["adjusted_weighting_value"] = (
                holding["weighting_base_value"] * multiplier
            )
            holding["adjusted_market_cap"] = holding["adjusted_weighting_value"]

        adjusted_total = sum(holding["adjusted_weighting_value"] for holding in holdings)
        weighting_base_total = sum(holding["weighting_base_value"] for holding in holdings)
        market_cap_total = sum(holding["market_cap_value"] for holding in holdings)
        if adjusted_total <= 0:
            raise ValueError("The selected stocks do not have usable market-cap data.")
        for holding in holdings:
            holding["market_cap_weight"] = (
                holding["market_cap_value"] / market_cap_total * 100
            )
            holding["base_weight"] = (
                holding["weighting_base_value"] / weighting_base_total * 100
            )
            holding["portfolio_weight"] = (
                holding["adjusted_weighting_value"] / adjusted_total * 100
            )
            holding["weight_uplift"] = (
                holding["portfolio_weight"] / holding["base_weight"]
            )
        holdings.sort(
            key=lambda holding: (
                -holding["portfolio_weight"],
                -holding["score"],
                holding["rank"],
            )
        )

    for position, holding in enumerate(holdings, start=1):
        holding["position"] = position
        holding["source_rank"] = holding.pop("rank")
    return {
        "run_id": run_id,
        "run_name": run["name"] or f"Run #{run_id}",
        "run_prompt": run["prompt"],
        "name": portfolio_name,
        "market_cap_limit": market_cap_limit,
        "minimum_score_percentile": minimum_score_percentile,
        "maximum_multiplier": maximum_multiplier,
        "base_weighting": base_weighting,
        "created_at": int(time.time()),
        "holdings": holdings,
        "holding_count": len(holdings),
        "total_market_cap_value": sum(
            holding["market_cap_value"] for holding in holdings
        ),
        "total_adjusted_market_cap": sum(
            holding["adjusted_weighting_value"] for holding in holdings
        ),
    }


def _run_result_sort_value(result, key):
    values = {
        "scoreRank": result.get("scoreRank"),
        "score": result.get("score"),
        "scorePercentile": result.get("score_percentile"),
        "confidence": result.get("confidence_score"),
        "company": f"{result.get('company_name') or ''} {result.get('ticker') or ''}".lower(),
        "marketCap": result.get("market_cap_value"),
        "inputTokens": result.get("prompt_tokens"),
        "responseTokens": result.get("response_tokens"),
        "reasoningTokens": result.get("reasoning_tokens"),
        "totalTokens": result.get("total_tokens"),
        "tokenBudgetPercent": result.get("token_budget_used_percent"),
        "durationMs": result.get("duration_ms"),
        "cost": result.get("cost"),
        "error": (result.get("error") or "").lower(),
    }
    return values.get(key, result.get("scoreRank"))


def size_score_correlation(results):
    points = []
    for result in results:
        try:
            score = float(result.get("score"))
            market_cap = float(result.get("market_cap_value"))
        except (TypeError, ValueError):
            continue
        if market_cap > 0 and math.isfinite(score) and math.isfinite(market_cap):
            points.append((math.log10(market_cap), score))

    if len(points) < 2:
        return None, len(points)

    mean_size = sum(size for size, _ in points) / len(points)
    mean_score = sum(score for _, score in points) / len(points)
    covariance = sum(
        (size - mean_size) * (score - mean_score) for size, score in points
    )
    size_variance = sum((size - mean_size) ** 2 for size, _ in points)
    score_variance = sum((score - mean_score) ** 2 for _, score in points)
    denominator = math.sqrt(size_variance * score_variance)
    if denominator == 0:
        return None, len(points)
    return covariance / denominator, len(points)


def paginated_run(run_id, page=1, page_size=100, view="ranking", sort_key="scoreRank",
                  direction="asc", query="", score_target=None):
    payload = get_run(run_id, include_raw_response=False)
    if not payload:
        return None

    successful = [
        result for result in payload["results"]
        if result.get("score") is not None and not result.get("error")
    ]
    failed = [
        result for result in payload["results"]
        if result.get("score") is None or result.get("error")
    ]
    for index, result in enumerate(successful, 1):
        result["scoreRank"] = index
    for index, result in enumerate(failed, 1):
        result["scoreRank"] = index

    scores = sorted({float(result["score"]) for result in successful})
    matched_score = None
    if score_target not in (None, "") and scores:
        target = float(score_target)
        matched_score = min(scores, key=lambda score: (abs(score - target), -score))

    rows = failed if view == "failed" else successful
    if matched_score is not None and view != "failed":
        rows = [result for result in rows if float(result["score"]) == matched_score]
    query = str(query or "").strip().lower()
    if query:
        rows = [
            result for result in rows
            if query in f"{result.get('company_name') or ''} {result.get('ticker') or ''}".lower()
        ]

    reverse = str(direction).lower() == "desc"
    present = []
    missing = []
    for result in rows:
        value = _run_result_sort_value(result, sort_key)
        (missing if value in (None, "") else present).append(result)
    present.sort(
        key=lambda result: (_run_result_sort_value(result, sort_key), result.get("scoreRank", 0)),
        reverse=reverse,
    )
    rows = present + sorted(missing, key=lambda result: result.get("scoreRank", 0))

    total = len(rows)
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 100)))
    total_pages = max(1, math.ceil(total / page_size))
    page = min(page, total_pages)
    offset = (page - 1) * page_size
    all_scores = [float(result["score"]) for result in successful]
    sorted_scores = sorted(all_scores)
    middle = len(sorted_scores) // 2
    median = None
    if sorted_scores:
        median = sorted_scores[middle] if len(sorted_scores) % 2 else (
            sorted_scores[middle - 1] + sorted_scores[middle]
        ) / 2
    correlation, correlation_sample_size = size_score_correlation(successful)

    payload["results"] = rows[offset:offset + page_size]
    payload["result_page"] = {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "offset": offset,
        "counts": {"ranking": len(successful), "failed": len(failed)},
        "score_values": scores,
        "matched_score": matched_score,
    }
    payload["score_stats"] = {
        "minimum": min(all_scores) if all_scores else None,
        "maximum": max(all_scores) if all_scores else None,
        "average": sum(all_scores) / len(all_scores) if all_scores else None,
        "median": median,
        "size_score_correlation": correlation,
        "size_score_sample_size": correlation_sample_size,
    }
    return payload


def ai_request_entries():
    if not AI_REQUEST_LOG_PATH.exists():
        return []
    try:
        entries = json.loads(AI_REQUEST_LOG_PATH.read_text())
        return entries if isinstance(entries, list) else []
    except json.JSONDecodeError:
        return []


def ai_request_cache_key(entry):
    request = entry.get("request") or entry
    provider = request.get("provider_preferences")
    if provider is None:
        provider = request.get("provider")
    signature = {
        "model": request.get("model"),
        "messages": request.get("messages"),
        "temperature": request.get("temperature"),
        "max_tokens": request.get("max_tokens"),
        "reasoning": request.get("reasoning"),
        "provider": provider,
    }
    return json.dumps(signature, sort_keys=True, separators=(",", ":"))


def token_stats_have_counts(token_stats):
    if not isinstance(token_stats, dict):
        return False
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        try:
            if float(token_stats.get(key) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def zero_token_costs(token_stats):
    copied = copy.deepcopy(token_stats)
    copied["cost"] = 0
    cost_details = copied.get("cost_details")
    if isinstance(cost_details, dict):
        copied["cost_details"] = {key: 0 for key in cost_details}
    copied["cache_reused_token_counts"] = True
    return copied


def effective_ai_request_entries(entries=None):
    entries = ai_request_entries() if entries is None else entries
    prior_token_stats = {}
    effective_entries = []
    for entry in entries:
        cache_key = ai_request_cache_key(entry)
        token_stats = entry.get("token_stats") or {}
        cache = (entry.get("response") or {}).get("cache") or {}
        effective_entry = entry

        if cache.get("status") == "HIT" and not token_stats_have_counts(token_stats):
            prior_stats = prior_token_stats.get(cache_key)
            if prior_stats:
                effective_entry = copy.deepcopy(entry)
                effective_entry["token_stats"] = zero_token_costs(prior_stats)
                effective_cache = (effective_entry.get("response") or {}).setdefault("cache", {})
                effective_cache["token_stats_source"] = "matched_prior_request"

        effective_entries.append(effective_entry)
        effective_stats = effective_entry.get("token_stats") or {}
        if token_stats_have_counts(effective_stats):
            prior_token_stats[cache_key] = effective_stats
    return effective_entries


def find_ai_request_entry(run_id, ticker):
    ticker = ticker.upper()
    matches = [
        entry
        for entry in effective_ai_request_entries()
        if entry.get("run_id") == run_id
        and (entry.get("company", {}).get("ticker") or "").upper() == ticker
    ]
    return matches[-1] if matches else None


def ai_request_costs_by_run():
    costs = {}
    for entry in ai_request_entries():
        run_id = entry.get("run_id")
        if run_id is None:
            continue
        token_stats = entry.get("token_stats") or {}
        try:
            cost = float(token_stats.get("cost") or 0)
        except (TypeError, ValueError):
            cost = 0
        costs[run_id] = costs.get(run_id, 0) + cost
    return costs


def cost_estimate(model, company_count, reasoning_mode=None):
    model = normalize_model(model)
    reasoning_mode = normalize_reasoning_mode(reasoning_mode, model)
    selected_reasoning = reasoning_config(reasoning_mode, model)["reasoning"]
    company_count = normalize_company_count(company_count)
    costs = []
    for entry in ai_request_entries():
        request = entry.get("request") or {}
        if request.get("model") != model:
            continue
        if (entry.get("response", {}).get("cache") or {}).get("status") == "HIT":
            continue
        fallback_reasoning = reasoning_config(
            default_reasoning_mode(model),
            model,
        )["reasoning"]
        if (request.get("reasoning") or fallback_reasoning) != selected_reasoning:
            continue
        token_stats = entry.get("token_stats") or {}
        try:
            cost = float(token_stats.get("cost") or 0)
        except (TypeError, ValueError):
            continue
        if cost > 0:
            costs.append(cost)

    sample_size = min(200, len(costs))
    recent_costs = costs[-sample_size:]
    average_cost = sum(recent_costs) / sample_size if sample_size else 0.00005
    return {
        "model": model,
        "reasoning_mode": reasoning_mode,
        "company_count": company_count,
        "estimated_cost": average_cost * company_count,
        "average_request_cost": average_cost,
        "sample_size": sample_size,
        "source": "recent_requests" if sample_size else "fallback",
    }


def recent_average_latency_ms(model, limit=200):
    model = normalize_model(model)
    latencies = []
    for entry in ai_request_entries():
        request = entry.get("request") or {}
        if request.get("model") != model:
            continue
        try:
            duration = int((entry.get("timing") or {}).get("duration_ms") or 0)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            latencies.append(duration)

    sample_size = min(limit, len(latencies))
    recent_latencies = latencies[-sample_size:]
    if not recent_latencies:
        return None
    return round(sum(recent_latencies) / sample_size)


def estimate_token_limit_failure_risk(completion_tokens, token_limit, minimum_samples=10):
    """Estimate the completion-token tail as a one-in-N failure rate."""
    try:
        token_limit = float(token_limit)
    except (TypeError, ValueError):
        token_limit = 0

    samples = []
    for value in completion_tokens:
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            samples.append(value)

    result = {
        "one_in": None,
        "probability": None,
        "sample_size": len(samples),
        "method": "lognormal_tail",
        "capped": False,
    }
    if token_limit <= 0 or len(samples) < minimum_samples:
        return result

    log_samples = [math.log(value) for value in samples]
    log_mean = sum(log_samples) / len(log_samples)
    log_variance = sum((value - log_mean) ** 2 for value in log_samples) / (
        len(log_samples) - 1
    )
    log_stddev = math.sqrt(log_variance)
    if log_stddev <= 1e-9:
        tail_probability = 1.0 if samples[0] >= token_limit else 0.0
    else:
        z_score = (math.log(token_limit) - log_mean) / log_stddev
        tail_probability = 0.5 * math.erfc(z_score / math.sqrt(2))

    result["probability"] = tail_probability
    if tail_probability <= 0:
        result["one_in"] = 1_000_000
        result["capped"] = True
    else:
        one_in = max(1, round(1 / tail_probability))
        if one_in > 1_000_000:
            one_in = 1_000_000
            result["capped"] = True
        result["one_in"] = one_in
    return result


def ai_request_stats_for_run(run_id, token_limit=None):
    stats = {
        "cost": 0,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "response_tokens": 0,
        "reasoning_tokens": 0,
        "request_count": 0,
        "successful_request_count": 0,
        "failed_request_count": 0,
        "total_latency_ms": 0,
        "average_prompt_tokens": None,
        "average_response_tokens": None,
        "average_reasoning_tokens": None,
        "average_total_tokens": None,
        "average_latency_ms": None,
        "token_limit_risk_one_in": None,
        "token_limit_risk_probability": None,
        "token_limit_risk_sample_size": 0,
        "token_limit_risk_method": "lognormal_tail",
        "token_limit_risk_capped": False,
    }
    successful_completion_tokens = {}
    for entry_index, entry in enumerate(effective_ai_request_entries()):
        if entry.get("run_id") != run_id:
            continue

        stats["request_count"] += 1
        if entry.get("response", {}).get("success"):
            stats["successful_request_count"] += 1
        else:
            stats["failed_request_count"] += 1

        token_stats = entry.get("token_stats") or {}
        completion_details = token_stats.get("completion_tokens_details") or {}
        for key in ("cost", "total_tokens", "prompt_tokens", "completion_tokens"):
            try:
                stats[key] += float(token_stats.get(key) or 0)
            except (TypeError, ValueError):
                pass
        try:
            completion_tokens = float(token_stats.get("completion_tokens") or 0)
            reasoning_tokens = float(completion_details.get("reasoning_tokens") or 0)
            stats["response_tokens"] += max(0, completion_tokens - reasoning_tokens)
            ticker = (entry.get("company", {}).get("ticker") or "").upper()
            sample_key = ticker or f"entry-{entry_index}"
            if entry.get("response", {}).get("success") and completion_tokens > 0:
                successful_completion_tokens[sample_key] = completion_tokens
            elif ticker:
                successful_completion_tokens.pop(sample_key, None)
        except (TypeError, ValueError):
            pass
        try:
            stats["reasoning_tokens"] += int(completion_details.get("reasoning_tokens") or 0)
        except (TypeError, ValueError):
            pass
        try:
            stats["total_latency_ms"] += int((entry.get("timing") or {}).get("duration_ms") or 0)
        except (TypeError, ValueError):
            pass

    if stats["request_count"]:
        stats["average_prompt_tokens"] = round(
            stats["prompt_tokens"] / stats["request_count"], 1
        )
        stats["average_response_tokens"] = round(
            stats["response_tokens"] / stats["request_count"], 1
        )
        stats["average_reasoning_tokens"] = round(
            stats["reasoning_tokens"] / stats["request_count"], 1
        )
        stats["average_total_tokens"] = round(
            stats["total_tokens"] / stats["request_count"], 1
        )
        stats["average_latency_ms"] = round(stats["total_latency_ms"] / stats["request_count"])
    risk = estimate_token_limit_failure_risk(
        successful_completion_tokens.values(), token_limit
    )
    stats["token_limit_risk_one_in"] = risk["one_in"]
    stats["token_limit_risk_probability"] = risk["probability"]
    stats["token_limit_risk_sample_size"] = risk["sample_size"]
    stats["token_limit_risk_method"] = risk["method"]
    stats["token_limit_risk_capped"] = risk["capped"]
    return stats


def provider_stats_for_run(run_id):
    providers = {}
    for entry in effective_ai_request_entries():
        if entry.get("run_id") != run_id:
            continue

        response = entry.get("response") or {}
        provider = str(response.get("provider") or "").strip() or "Not reported"
        current = providers.setdefault(
            provider,
            {
                "provider": provider,
                "request_count": 0,
                "stock_count": 0,
                "successful_request_count": 0,
                "failed_request_count": 0,
                "success_rate_percent": 0,
                "prompt_tokens": 0,
                "response_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "cost": 0,
                "cost_per_million_tokens": None,
                "input_cost_per_million_tokens": None,
                "output_cost_per_million_tokens": None,
                "total_latency_ms": 0,
                "average_latency_ms": None,
                "reasoning_trace_visible_count": 0,
                "reasoning_trace_visible_percent": 0,
                "cache_hit_count": 0,
                "_prompt_cost": 0,
                "_completion_cost": 0,
                "_priced_prompt_tokens": 0,
                "_priced_completion_tokens": 0,
                "_tickers": set(),
            },
        )
        current["request_count"] += 1
        success = bool(response.get("success"))
        if success:
            current["successful_request_count"] += 1
        else:
            current["failed_request_count"] += 1

        ticker = str((entry.get("company") or {}).get("ticker") or "").strip().upper()
        if ticker:
            current["_tickers"].add(ticker)

        token_stats = entry.get("token_stats") or {}
        completion_details = token_stats.get("completion_tokens_details") or {}
        cache_hit = str((response.get("cache") or {}).get("status") or "").upper() == "HIT"
        prompt_tokens = 0
        completion_tokens = 0
        try:
            prompt_tokens = float(token_stats.get("prompt_tokens") or 0)
            completion_tokens = float(token_stats.get("completion_tokens") or 0)
            reasoning_tokens = float(completion_details.get("reasoning_tokens") or 0)
            current["prompt_tokens"] += prompt_tokens
            current["response_tokens"] += max(0, completion_tokens - reasoning_tokens)
            current["reasoning_tokens"] += reasoning_tokens
            current["total_tokens"] += float(token_stats.get("total_tokens") or 0)
            current["cost"] += float(token_stats.get("cost") or 0)
        except (TypeError, ValueError):
            pass
        cost_details = token_stats.get("cost_details") or {}
        if not cache_hit and isinstance(cost_details, dict):
            try:
                prompt_cost = cost_details.get("upstream_inference_prompt_cost")
                if prompt_cost is not None:
                    current["_prompt_cost"] += float(prompt_cost)
                    current["_priced_prompt_tokens"] += prompt_tokens
                completion_cost = cost_details.get("upstream_inference_completions_cost")
                if completion_cost is not None:
                    current["_completion_cost"] += float(completion_cost)
                    current["_priced_completion_tokens"] += completion_tokens
            except (TypeError, ValueError):
                pass
        try:
            current["total_latency_ms"] += int((entry.get("timing") or {}).get("duration_ms") or 0)
        except (TypeError, ValueError):
            pass

        reasoning_trace = entry.get("chain_of_thought")
        if not reasoning_trace:
            reasoning_trace = (
                response.get("reasoning")
                or response.get("reasoning_content")
                or response.get("reasoning_details")
            )
        if reasoning_trace:
            current["reasoning_trace_visible_count"] += 1
        if cache_hit:
            current["cache_hit_count"] += 1

    rows = []
    for current in providers.values():
        current["stock_count"] = len(current.pop("_tickers"))
        request_count = current["request_count"]
        successful_count = current["successful_request_count"]
        if request_count:
            current["success_rate_percent"] = round(successful_count / request_count * 100, 1)
            current["average_latency_ms"] = round(current["total_latency_ms"] / request_count)
        if successful_count:
            current["reasoning_trace_visible_percent"] = round(
                current["reasoning_trace_visible_count"] / successful_count * 100,
                1,
            )
        if current["total_tokens"]:
            current["cost_per_million_tokens"] = round(
                current["cost"] / current["total_tokens"] * 1_000_000,
                6,
            )
        priced_prompt_tokens = current.pop("_priced_prompt_tokens")
        priced_completion_tokens = current.pop("_priced_completion_tokens")
        prompt_cost = current.pop("_prompt_cost")
        completion_cost = current.pop("_completion_cost")
        if priced_prompt_tokens:
            current["input_cost_per_million_tokens"] = round(
                prompt_cost / priced_prompt_tokens * 1_000_000,
                6,
            )
        if priced_completion_tokens:
            current["output_cost_per_million_tokens"] = round(
                completion_cost / priced_completion_tokens * 1_000_000,
                6,
            )
        rows.append(current)
    return sorted(rows, key=lambda row: (-row["request_count"], row["provider"].lower()))


def ai_request_stats_by_ticker(run_id):
    stats = {}
    for entry in effective_ai_request_entries():
        if entry.get("run_id") != run_id:
            continue
        ticker = (entry.get("company", {}).get("ticker") or "").upper()
        if not ticker:
            continue

        token_stats = entry.get("token_stats") or {}
        completion_details = token_stats.get("completion_tokens_details") or {}
        current = stats.setdefault(
            ticker,
            {
                "cost": 0,
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "response_tokens": 0,
                "reasoning_tokens": 0,
                "token_budget": 0,
                "token_budget_used_percent": None,
                "duration_ms": None,
            },
        )
        try:
            current["token_budget"] += int((entry.get("request") or {}).get("max_tokens") or 0)
        except (TypeError, ValueError):
            pass
        for key in ("cost", "total_tokens", "prompt_tokens", "completion_tokens"):
            try:
                current[key] += float(token_stats.get(key) or 0)
            except (TypeError, ValueError):
                pass
        try:
            completion_tokens = float(token_stats.get("completion_tokens") or 0)
            reasoning_tokens = float(completion_details.get("reasoning_tokens") or 0)
            current["response_tokens"] += max(0, completion_tokens - reasoning_tokens)
        except (TypeError, ValueError):
            pass
        try:
            current["reasoning_tokens"] += int(completion_details.get("reasoning_tokens") or 0)
        except (TypeError, ValueError):
            pass
        try:
            current["duration_ms"] = int((entry.get("timing") or {}).get("duration_ms"))
        except (TypeError, ValueError):
            pass
    for current in stats.values():
        if current["token_budget"] > 0:
            current["token_budget_used_percent"] = round(
                current["completion_tokens"] / current["token_budget"] * 100,
                1,
            )
    return stats


def get_result_detail(run_id, ticker):
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT id, name, prompt, model, reasoning_mode, max_tokens, status, created_at, started_at, finished_at, error
            FROM scoring_runs
            WHERE id = ? AND deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return None

        result = connection.execute(
            """
            SELECT ticker, company_name, rank, market_cap, market_cap_value, price, country,
                   score, raw_response, error, created_at
            FROM scoring_results
            WHERE run_id = ? AND ticker = ?
            """,
            (run_id, ticker),
        ).fetchone()
        if not result:
            return None

    return {
        "run": dict(run),
        "result": dict(result),
        "aiRequest": sanitized_ai_request_entry(find_ai_request_entry(run_id, ticker)),
    }


def run_status(run_id):
    with db_connect() as connection:
        row = connection.execute("SELECT status FROM scoring_runs WHERE id = ? AND deleted_at IS NULL", (run_id,)).fetchone()
    return row["status"] if row else None


def stop_scoring_run(run_id):
    now = int(time.time())
    with db_connect() as connection:
        row = connection.execute("SELECT status FROM scoring_runs WHERE id = ? AND deleted_at IS NULL", (run_id,)).fetchone()
        if not row:
            return None
        if row["status"] in ("queued", "running"):
            connection.execute(
                "UPDATE scoring_runs SET status = ?, queue_count = 0, error = ? WHERE id = ?",
                ("stop_requested", "Stop requested by user.", run_id),
            )
        elif row["status"] == "stop_requested":
            pass
        update_run_counts(connection, run_id)
        connection.commit()
        updated = connection.execute(
            """
            SELECT id, name, prompt, model, reasoning_mode, max_tokens, status,
                   company_count, completed_count, failed_count, queue_count,
                   created_at, started_at, finished_at, error
            FROM scoring_runs
            WHERE id = ? AND deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
    return dict(updated)


def list_runs(run_type="scoring"):
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT id, name, prompt, model, reasoning_mode, max_tokens, low_cost_mode,
                   starred, run_type,
                   status, company_count, completed_count, failed_count,
                   created_at, started_at, finished_at, error
            FROM scoring_runs
            WHERE deleted_at IS NULL AND run_type = ?
            ORDER BY created_at DESC, id DESC
            """
            ,
            (run_type,),
        ).fetchall()
    costs = ai_request_costs_by_run()
    runs = []
    for row in rows:
        run = dict(row)
        run["cost"] = costs.get(run["id"], 0)
        runs.append(run)
    return runs


def list_confidence_runs():
    pinned_id = pinned_confidence_run_id()
    runs = list_runs("confidence")
    for run in runs:
        run["pinned"] = run["id"] == pinned_id
    return runs


def latest_confidence_scores():
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT id, name, status, company_count, completed_count, failed_count,
                   created_at, finished_at
            FROM scoring_runs
            WHERE deleted_at IS NULL AND (run_type = 'confidence' OR prompt = ?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (CONFIDENCE_SCORE_PROMPT,),
        ).fetchone()
        if not run:
            return {"run": None, "scores": []}

        rows = connection.execute(
            """
            SELECT scoring_run_companies.position,
                   scoring_run_companies.ticker,
                   COALESCE(scoring_results.company_name, companies.name) AS company_name,
                   COALESCE(scoring_results.rank, companies.rank) AS market_cap_rank,
                   COALESCE(scoring_results.market_cap, companies.market_cap) AS market_cap,
                   COALESCE(scoring_results.market_cap_value, companies.market_cap_value) AS market_cap_value,
                   companies.logo,
                   scoring_results.score,
                   scoring_results.error
            FROM scoring_run_companies
            JOIN companies ON companies.ticker = scoring_run_companies.ticker
            LEFT JOIN scoring_results
              ON scoring_results.run_id = scoring_run_companies.run_id
             AND scoring_results.ticker = scoring_run_companies.ticker
            WHERE scoring_run_companies.run_id = ?
            ORDER BY scoring_results.score IS NULL,
                     scoring_results.score DESC,
                     scoring_run_companies.position
            """,
            (run["id"],),
        ).fetchall()

    return {"run": dict(run), "scores": [dict(row) for row in rows]}


def rename_scoring_run(run_id, name):
    return update_scoring_run(run_id, name=name)


def update_scoring_run(
    run_id,
    name=None,
    prompt=None,
    starred=None,
    max_tokens=None,
    low_cost_mode=None,
    stock_list_id=RUN_FIELD_UNSET,
):
    updates = []
    values = []
    if name is not None:
        updates.append("name = ?")
        values.append(normalize_run_name(name))
    if prompt is not None:
        updates.append("prompt = ?")
        values.append(normalize_scoring_prompt(prompt))
    if max_tokens is not None:
        updates.append("max_tokens = ?")
        values.append(normalize_max_tokens(max_tokens))
    if low_cost_mode is not None:
        updates.append("low_cost_mode = ?")
        values.append(1 if normalize_low_cost_mode(low_cost_mode) else 0)
    if starred is not None:
        if not isinstance(starred, bool):
            raise ValueError("Starred must be true or false.")
        updates.append("starred = ?")
        values.append(1 if starred else 0)

    with db_connect() as connection:
        row = connection.execute("SELECT id FROM scoring_runs WHERE id = ? AND deleted_at IS NULL", (run_id,)).fetchone()
        if not row:
            return None
        if stock_list_id is not RUN_FIELD_UNSET:
            normalized_id, universe_companies = companies_for_run_universe(stock_list_id)
            universe_tickers = {company["ticker"] for company in universe_companies}
            current_tickers = [company["ticker"] for company in scoring_companies_for_run(run_id)]
            missing = [ticker for ticker in current_tickers if ticker not in universe_tickers]
            if missing:
                preview = ", ".join(missing[:8])
                remainder = len(missing) - 8
                suffix = f" and {remainder} more" if remainder > 0 else ""
                raise ValueError(
                    "New stock universe must include every stock already in this run. "
                    f"Missing {len(missing)}: {preview}{suffix}."
                )
            updates.append("stock_list_id = ?")
            values.append(normalized_id)
        if not updates:
            return get_run(run_id)
        values.append(run_id)
        connection.execute(
            f"UPDATE scoring_runs SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        connection.commit()
    return get_run(run_id)


def delete_scoring_run(run_id):
    now = int(time.time())
    with db_connect() as connection:
        row = connection.execute("SELECT id FROM scoring_runs WHERE id = ? AND deleted_at IS NULL", (run_id,)).fetchone()
        if not row:
            return False
        connection.execute(
            """
            UPDATE scoring_runs
            SET deleted_at = ?,
                queue_count = 0,
                status = CASE
                    WHEN status IN ('queued', 'running', 'stop_requested') THEN 'stopped'
                    ELSE status
                END,
                finished_at = CASE
                    WHEN finished_at IS NULL THEN ?
                    ELSE finished_at
                END,
                error = CASE
                    WHEN status IN ('queued', 'running', 'stop_requested') THEN 'Archived by user.'
                    ELSE error
                END
            WHERE id = ?
            """,
            (now, now, run_id),
        )
        connection.commit()
    return True


def update_run_counts(connection, run_id):
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN error IS NULL THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS failed
        FROM scoring_results
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    connection.execute(
        """
        UPDATE scoring_runs
        SET completed_count = ?, failed_count = ?
        WHERE id = ?
        """,
        (row["completed"] or 0, row["failed"] or 0, run_id),
    )


def invalidate_truncated_scoring_results():
    latest_entries = {}
    for entry in ai_request_entries():
        company = entry.get("company") or {}
        ticker = (company.get("ticker") or "").upper()
        run_id = entry.get("run_id")
        if run_id is not None and ticker:
            latest_entries[(run_id, ticker)] = entry

    truncated = [
        (run_id, ticker)
        for (run_id, ticker), entry in latest_entries.items()
        if (entry.get("response") or {}).get("finish_reason") == "length"
    ]
    if not truncated:
        return 0

    affected_runs = set()
    invalidated = 0
    with db_connect() as connection:
        for run_id, ticker in truncated:
            cursor = connection.execute(
                """
                UPDATE scoring_results
                SET score = NULL, error = ?
                WHERE run_id = ? AND UPPER(ticker) = ? AND error IS NULL
                """,
                (TOKEN_LIMIT_ERROR, run_id, ticker),
            )
            if cursor.rowcount:
                invalidated += cursor.rowcount
                affected_runs.add(run_id)
        for run_id in affected_runs:
            update_run_counts(connection, run_id)
        connection.commit()
    return invalidated


def result_tickers_for_run(run_id):
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT ticker FROM scoring_results WHERE run_id = ?",
            (run_id,),
        ).fetchall()
    return {row["ticker"] for row in rows}


def completed_score_tickers_for_run(run_id):
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT ticker
            FROM scoring_results
            WHERE run_id = ?
              AND error IS NULL
              AND score IS NOT NULL
            """,
            (run_id,),
        ).fetchall()
    return {row["ticker"] for row in rows}


def incomplete_company_count(run_id):
    with db_connect() as connection:
        run = connection.execute(
            "SELECT company_count FROM scoring_runs WHERE id = ? AND deleted_at IS NULL",
            (run_id,),
        ).fetchone()
    if not run:
        return 0
    complete_tickers = completed_score_tickers_for_run(run_id)
    return sum(
        1
        for company in scoring_companies_for_run(run_id)
        if company["ticker"] not in complete_tickers
    )


def mark_interrupted_runs():
    now = int(time.time())
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT id, status, worker_pid
            FROM scoring_runs
            WHERE deleted_at IS NULL
              AND status IN ('queued', 'running', 'stop_requested')
            """,
        ).fetchall()
        for row in rows:
            if process_is_running(row["worker_pid"]):
                continue
            final_error = (
                "Stopped by user."
                if row["status"] == "stop_requested"
                else "Interrupted because the scoring worker is no longer running."
            )
            connection.execute(
                """
                UPDATE scoring_runs
                SET status = ?,
                    queue_count = 0,
                    finished_at = COALESCE(finished_at, ?),
                    error = COALESCE(error, ?),
                    worker_pid = NULL,
                    worker_started_at = NULL
                WHERE id = ?
                """,
                ("stopped", now, final_error, row["id"]),
            )
        connection.commit()


def parse_numeric_score(text):
    matches = re.findall(r"-?\d+(?:\.\d+)?", text or "")
    if not matches:
        raise ValueError("Model response did not contain a number")
    return float(matches[-1])


class FatalScoringError(RuntimeError):
    pass


def openrouter_error_message(error):
    try:
        body = error.read().decode("utf-8", "replace")
    except Exception:
        body = ""
    message = f"OpenRouter HTTP {error.code}: {error.reason}"
    if body:
        try:
            payload = json.loads(body)
            detail = payload.get("error", {}).get("message") or payload.get("message")
            if detail:
                message = f"{message} - {detail}"
        except json.JSONDecodeError:
            message = f"{message} - {body[:300]}"
    return message


def prompt_for_company(prompt, company):
    prompt = prompt.strip()
    if "TICKER" in prompt:
        return prompt.replace("TICKER", company["ticker"]).replace("COMPANY", company["name"])
    company_reference = f"{company['name']} (ticker: {company['ticker']})"
    return prompt.replace("COMPANY", company_reference)


def openrouter_http_error_details(error):
    try:
        body = error.read().decode("utf-8", "replace")
    except Exception:
        body = ""

    message = f"OpenRouter HTTP {error.code}: {error.reason}"
    parsed_body = None
    if body:
        try:
            parsed_body = json.loads(body)
            detail = parsed_body.get("error", {}).get("message") or parsed_body.get("message")
            if detail:
                message = f"{message} - {detail}"
        except json.JSONDecodeError:
            message = f"{message} - {body[:300]}"

    return {
        "message": message,
        "status": error.code,
        "reason": error.reason,
        "body": parsed_body if parsed_body is not None else body,
    }


def openrouter_payload_error_details(payload, http_status=None):
    if not isinstance(payload, dict):
        return {
            "message": "OpenRouter returned an invalid response payload.",
            "status": http_status,
            "body": payload,
            "retryable": True,
        }

    error = payload.get("error")
    if error:
        if isinstance(error, dict):
            code = error.get("code")
            detail = error.get("message") or json.dumps(error)
        else:
            code = None
            detail = str(error)
        status = code or http_status
        prefix = f"OpenRouter API {status}" if status else "OpenRouter API error"
        return {
            "message": f"{prefix}: {detail}",
            "status": status,
            "body": payload,
            "retryable": status in (408, 409, 429, 500, 502, 503, 504),
        }

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return {
            "message": "OpenRouter returned no completion choices.",
            "status": http_status,
            "body": payload,
            "retryable": True,
        }
    return None


def ai_log_entry(
    run_id,
    company,
    request_payload,
    started_at,
    response_payload=None,
    error=None,
    http_status=None,
    cache_metadata=None,
):
    choice = None
    message = {}
    if response_payload and response_payload.get("choices"):
        choice = response_payload["choices"][0]
        message = choice.get("message", {}) or {}

    return {
        "timestamp": int(time.time()),
        "run_id": run_id,
        "company": {
            "name": company["name"],
            "ticker": company["ticker"],
            "market_cap_rank": company["rank"],
        },
        "request": {
            "provider": "openrouter",
            "url": OPENROUTER_API_URL,
            "model": request_payload.get("model"),
            "messages": request_payload.get("messages"),
            "temperature": request_payload.get("temperature"),
            "max_tokens": request_payload.get("max_tokens"),
            "attempt_timeout_seconds": openrouter_attempt_timeout_seconds(
                request_payload.get("max_tokens")
            ),
            "reasoning": request_payload.get("reasoning"),
            "provider_preferences": request_payload.get("provider"),
            "response_cache": {
                "enabled": True,
                "ttl_seconds": openrouter_cache_ttl_seconds(),
            },
            "prompt_sent": request_payload["messages"][0]["content"],
        },
        "response": {
            "success": error is None,
            "http_status": http_status,
            "id": response_payload.get("id") if response_payload else None,
            "created": response_payload.get("created") if response_payload else None,
            "model": response_payload.get("model") if response_payload else None,
            "provider": response_payload.get("provider") if response_payload else None,
            "visible_content": message.get("content") if choice else None,
            "reasoning": message.get("reasoning"),
            "reasoning_content": message.get("reasoning_content"),
            "reasoning_details": message.get("reasoning_details"),
            "finish_reason": choice.get("finish_reason") if choice else None,
            "raw_payload": sanitized_ai_payload(response_payload),
            "cache": cache_metadata,
            "error": error,
        },
        "token_stats": response_payload.get("usage") if response_payload else None,
        "timing": {
            "duration_ms": round((time.time() - started_at) * 1000),
        },
        "chain_of_thought": (
            message.get("reasoning")
            or message.get("reasoning_content")
            or message.get("reasoning_details")
        ),
        "chain_of_thought_note": (
            "Reasoning text was returned by the provider."
            if (
                message.get("reasoning")
                or message.get("reasoning_content")
                or message.get("reasoning_details")
            )
            else "Hidden chain-of-thought was not exposed by the model/API for this request."
        ),
    }


def reasoning_token_count(response_payload):
    usage = response_payload.get("usage") if response_payload else None
    completion_details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
    if not isinstance(completion_details, dict):
        return 0
    try:
        return int(completion_details.get("reasoning_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def response_provider(response_payload):
    if not response_payload:
        return None
    provider = response_payload.get("provider")
    if provider:
        return provider
    return None


def maybe_block_reasoning_provider(run_id, company, request_payload, response_payload):
    reasoning = request_payload.get("reasoning") or {}
    reasoning_disabled = reasoning.get("enabled") is False or reasoning.get("effort") == "none"
    if not reasoning_disabled:
        return
    tokens = reasoning_token_count(response_payload)
    if tokens <= 0:
        return
    block_reasoning_provider(
        response_provider(response_payload),
        run_id=run_id,
        ticker=company.get("ticker"),
        reasoning_tokens=tokens,
    )


def sanitized_ai_payload(payload):
    if payload is None:
        return None
    sanitized = json.loads(json.dumps(payload))
    for choice in sanitized.get("choices", []) or []:
        message = choice.get("message")
        if isinstance(message, dict):
            message.pop("reasoning", None)
            message.pop("reasoning_details", None)
            message.pop("reasoning_content", None)
    return sanitized


def sanitized_ai_request_entry(entry):
    if entry is None:
        return None
    sanitized = json.loads(json.dumps(entry))
    response = sanitized.get("response")
    if isinstance(response, dict):
        response["raw_payload"] = sanitized_ai_payload(response.get("raw_payload"))
    if not sanitized.get("chain_of_thought"):
        sanitized["chain_of_thought"] = (
            response.get("reasoning")
            or response.get("reasoning_content")
            or response.get("reasoning_details")
            if isinstance(response, dict)
            else None
        )
    if sanitized.get("chain_of_thought"):
        sanitized["chain_of_thought_note"] = "Reasoning text was returned by the provider."
    else:
        sanitized["chain_of_thought_note"] = "Hidden chain-of-thought was not exposed by the model/API for this request."
    return sanitized


def call_openrouter(
    prompt,
    company,
    model,
    reasoning_mode=None,
    run_id=None,
    max_tokens=None,
    low_cost_provider=None,
):
    api_key = os.environ.get("OPENROUTER_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_KEY is not set")

    config = model_config(model)
    reasoning = reasoning_config(reasoning_mode, config["id"], allow_fallback=True)["reasoning"]
    request_payload = {
        "model": config["id"],
        "messages": [
            {"role": "user", "content": prompt_for_company(prompt, company)},
        ],
        "max_tokens": normalize_max_tokens(max_tokens),
        "reasoning": reasoning,
        "provider": provider_preferences(config, low_cost_provider),
    }
    if config.get("supports_temperature", True):
        request_payload["temperature"] = 0
    body = json.dumps(request_payload).encode("utf-8")
    request = urllib.request.Request(
        OPENROUTER_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "AI Stock Scorer",
            "X-OpenRouter-Cache": "true",
            "X-OpenRouter-Cache-TTL": str(openrouter_cache_ttl_seconds()),
        },
        method="POST",
    )
    max_attempts = openrouter_max_attempts()
    attempt_timeout_seconds = openrouter_attempt_timeout_seconds(request_payload["max_tokens"])
    for attempt in range(1, max_attempts + 1):
        started_at = time.time()
        try:
            with urllib.request.urlopen(request, timeout=attempt_timeout_seconds) as response:
                http_status = response.status
                response_body = read_http_response_with_deadline(
                    response,
                    attempt_timeout_seconds,
                )
                payload = json.loads(response_body.decode("utf-8"))
                response_headers = getattr(response, "headers", None)
                header_value = response_headers.get if response_headers is not None else lambda _name: None
                cache_metadata = {
                    "status": header_value("X-OpenRouter-Cache-Status"),
                    "age_seconds": header_value("X-OpenRouter-Cache-Age"),
                    "ttl_seconds": header_value("X-OpenRouter-Cache-TTL"),
                }
        except urllib.error.HTTPError as exc:
            details = openrouter_http_error_details(exc)
            append_ai_request_log(
                ai_log_entry(
                    run_id,
                    company,
                    request_payload,
                    started_at,
                    error=details,
                    http_status=details["status"],
                )
            )
            message = details["message"]
            if exc.code in (401, 403):
                raise FatalScoringError(message) from exc
            raise RuntimeError(message) from exc
        except Exception as exc:
            append_ai_request_log(
                ai_log_entry(
                    run_id,
                    company,
                    request_payload,
                    started_at,
                    error={"message": str(exc), "type": exc.__class__.__name__},
                )
            )
            raise

        payload_error = openrouter_payload_error_details(payload, http_status)
        if not payload_error:
            break
        payload_error["attempt"] = attempt
        payload_error["max_attempts"] = max_attempts
        append_ai_request_log(
            ai_log_entry(
                run_id,
                company,
                request_payload,
                started_at,
                response_payload=payload,
                error=payload_error,
                http_status=http_status,
                cache_metadata=cache_metadata,
            )
        )
        if payload_error["retryable"] and attempt < max_attempts:
            time.sleep(attempt)
            continue
        raise RuntimeError(payload_error["message"])

    maybe_block_reasoning_provider(run_id, company, request_payload, payload)

    try:
        choice = payload["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError(TOKEN_LIMIT_ERROR)
        message = choice.get("message", {})
        content = message.get("content")
        if content is None:
            raise ValueError(
                "Model returned no visible content. It likely used the response token budget "
                "without producing an answer."
            )
        content = content.strip()
    except Exception as exc:
        append_ai_request_log(
            ai_log_entry(
                run_id,
                company,
                request_payload,
                started_at,
                response_payload=payload,
                error={"message": str(exc), "type": exc.__class__.__name__},
                http_status=http_status,
                cache_metadata=cache_metadata,
            )
        )
        raise

    append_ai_request_log(
        ai_log_entry(
            run_id,
            company,
            request_payload,
            started_at,
            response_payload=payload,
            http_status=http_status,
            cache_metadata=cache_metadata,
        )
    )
    return content


def save_result(connection, run_id, company, score, raw_response, error):
    now = int(time.time())
    connection.execute(
        """
        INSERT INTO scoring_results (
            run_id, ticker, company_name, rank, market_cap, market_cap_value, price,
            country, score, raw_response, error, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, ticker) DO UPDATE SET
            company_name = excluded.company_name,
            rank = excluded.rank,
            market_cap = excluded.market_cap,
            market_cap_value = excluded.market_cap_value,
            price = excluded.price,
            country = excluded.country,
            score = excluded.score,
            raw_response = excluded.raw_response,
            error = excluded.error,
            created_at = excluded.created_at
        """,
        (
            run_id,
            company["ticker"],
            company["name"],
            company["rank"],
            company["marketCap"],
            company["marketCapValue"],
            company["price"],
            company["country"],
            score,
            raw_response,
            error,
            now,
        ),
    )


def score_company_request(
    run_id,
    prompt,
    model,
    reasoning_mode,
    company,
    max_tokens,
    low_cost_provider=None,
):
    raw_response = None
    score = None
    error = None
    try:
        call_options = {"run_id": run_id, "max_tokens": max_tokens}
        if low_cost_provider:
            call_options["low_cost_provider"] = low_cost_provider
        raw_response = call_openrouter(prompt, company, model, reasoning_mode, **call_options)
        score = parse_numeric_score(raw_response)
    except FatalScoringError:
        raise
    except Exception as exc:
        error = str(exc)
    return company, score, raw_response, error


def save_scoring_result(run_id, company, score, raw_response, error):
    with db_connect() as connection:
        if run_status(run_id) is None:
            return False
        if run_status(run_id) == "stop_requested":
            return False
        save_result(connection, run_id, company, score, raw_response, error)
        connection.execute(
            "UPDATE scoring_runs SET queue_count = MAX(queue_count - 1, 0) WHERE id = ?",
            (run_id,),
        )
        update_run_counts(connection, run_id)
        connection.commit()
    return True


def score_run_worker(run_id, start_index=0, target_tickers=None):
    ensure_scoring_schema()
    with db_connect() as connection:
        run = connection.execute(
            "SELECT name, prompt, model, reasoning_mode, max_tokens, low_cost_mode, company_count FROM scoring_runs WHERE id = ? AND deleted_at IS NULL",
            (run_id,),
        ).fetchone()
        if not run:
            return
        connection.execute(
            "UPDATE scoring_runs SET status = ?, started_at = ? WHERE id = ?",
            ("running", int(time.time()), run_id),
        )
        connection.commit()

    fatal_error = None
    stopped = False
    try:
        low_cost_provider = None
        if run["low_cost_mode"]:
            low_cost_provider = lowest_cost_provider(
                run["model"], run["reasoning_mode"], run["max_tokens"]
            )
        companies = scoring_companies_for_run(run_id)
        if start_index:
            companies = companies[start_index:]
        if target_tickers:
            target_set = {ticker.upper() for ticker in target_tickers}
            companies = [company for company in companies if company["ticker"].upper() in target_set]
        completed_tickers = completed_score_tickers_for_run(run_id)
        companies = [company for company in companies if company["ticker"] not in completed_tickers]
        with db_connect() as connection:
            connection.execute(
                "UPDATE scoring_runs SET queue_count = ? WHERE id = ?",
                (len(companies), run_id),
            )
            connection.commit()
        if not companies:
            with db_connect() as connection:
                connection.execute(
                    """
                    UPDATE scoring_runs
                    SET status = ?, finished_at = ?, error = ?,
                        queue_count = 0, worker_pid = NULL, worker_started_at = NULL
                    WHERE id = ?
                    """,
                    ("completed", int(time.time()), None, run_id),
                )
                update_run_counts(connection, run_id)
                connection.commit()
            return
        company_iter = iter(companies)
        futures = {}
        max_workers = min(scoring_concurrency(), len(companies)) or 1

        def submit_next(executor):
            try:
                company = next(company_iter)
            except StopIteration:
                return False
            current_status = run_status(run_id)
            if current_status is None:
                return False
            if current_status == "stop_requested":
                return False
            future = executor.submit(
                score_company_request,
                run_id,
                run["prompt"],
                run["model"],
                run["reasoning_mode"],
                company,
                run["max_tokens"],
                low_cost_provider,
            )
            futures[future] = company
            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for _ in range(max_workers):
                if not submit_next(executor):
                    break

            while futures:
                current_status = run_status(run_id)
                if current_status is None:
                    stopped = True
                    break
                if current_status == "stop_requested":
                    stopped = True
                    break

                done, _pending = concurrent.futures.wait(
                    futures,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    futures.pop(future, None)
                    try:
                        company, score, raw_response, error = future.result()
                    except FatalScoringError as exc:
                        fatal_error = str(exc)
                        stopped = False
                        break

                    if not save_scoring_result(run_id, company, score, raw_response, error):
                        stopped = True
                        break

                    if not submit_next(executor):
                        continue

                if fatal_error or stopped:
                    break

            for future in futures:
                future.cancel()
    except Exception as exc:
        fatal_error = str(exc)

    with db_connect() as connection:
        current_status = run_status(run_id)
        if current_status is None:
            return
        if current_status == "stop_requested":
            stopped = True
        status = "stopped" if stopped else "failed" if fatal_error else "completed"
        final_error = "Stopped by user." if stopped else fatal_error
        connection.execute(
            """
            UPDATE scoring_runs
            SET status = ?, finished_at = ?, error = ?,
                queue_count = 0, worker_pid = NULL, worker_started_at = NULL
            WHERE id = ?
            """,
            (status, int(time.time()), final_error, run_id),
        )
        update_run_counts(connection, run_id)
        connection.commit()


def watched_signature():
    files = [
        "server.py",
        "scoring_worker.py",
        "index.html",
        "styles.css",
        "app.js",
        "run.html",
        "run.js",
        "result.html",
        "result.js",
        "start_server.sh",
    ]
    signature = []
    for filename in files:
        path = ROOT / filename
        if path.exists():
            stat = path.stat()
            signature.append(f"{filename}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(signature)


class Handler(SimpleHTTPRequestHandler):
    def handle_one_request(self):
        try:
            super().handle_one_request()
        except ConnectionResetError:
            pass

    def translate_path(self, path):
        parsed = urlparse(path).path.lstrip("/")
        if not parsed:
            parsed = "index.html"
        return str((ROOT / parsed).resolve())

    def send_json(self, payload, status=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def read_json(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        return json.loads(self.rfile.read(content_length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/run.html":
            self.path = "/run.html"
            super().do_GET()
            return
        if parsed.path == "/result.html":
            self.path = "/result.html"
            super().do_GET()
            return

        if parsed.path == "/api/companies":
            try:
                query = dict(parse_qsl(parsed.query))
                if query:
                    page_payload = paginated_companies(
                        query.get("page", 1), query.get("pageSize", 100),
                        query.get("q", ""), query.get("sort", "rank"), query.get("dir", "asc")
                    )
                    companies = page_payload["companies"]
                else:
                    page_payload = None
                    companies = db_companies()
                self.send_json(
                    {
                        "source": str(DB_PATH),
                        "companies": companies,
                        **({"pagination": page_payload["pagination"]} if page_payload else {}),
                    }
                )
            except (urllib.error.URLError, ValueError, TimeoutError) as exc:
                self.send_json({"error": str(exc), "companies": []}, 502)
            return

        if parsed.path == "/api/models":
            self.send_json(
                {
                    "models": openrouter_model_options(),
                    "default": openrouter_model(),
                    "reasoning_modes": reasoning_options(openrouter_model()),
                    "default_reasoning_mode": default_reasoning_mode(openrouter_model()),
                    "default_max_tokens": openrouter_max_tokens(),
                }
            )
            return

        if parsed.path == "/api/preferences/run-table-columns":
            ensure_scoring_schema()
            try:
                view = dict(parse_qsl(parsed.query)).get("view", "ranking")
                self.send_json(
                    {
                        "columns": get_run_table_columns_preference(view),
                        "order": get_run_table_column_order_preference(view),
                    }
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            return

        if parsed.path == "/api/preferences/provider-table-columns":
            ensure_scoring_schema()
            self.send_json(
                {
                    "columns": get_provider_table_columns_preference(),
                    "order": get_provider_table_column_order_preference(),
                }
            )
            return

        if parsed.path == "/api/preferences/portfolio-table-columns":
            ensure_scoring_schema()
            self.send_json(
                {
                    "columns": get_portfolio_table_columns_preference(),
                    "order": get_portfolio_table_column_order_preference(),
                }
            )
            return

        if parsed.path == "/api/stock-lists":
            ensure_scoring_schema()
            self.send_json({"lists": list_stock_lists()})
            return

        if parsed.path == "/api/manual-rankings":
            ensure_scoring_schema()
            self.send_json({"rankings": list_manual_rankings()})
            return

        manual_ranking_match = re.fullmatch(r"/api/manual-rankings/(\d+)", parsed.path)
        if manual_ranking_match:
            ensure_scoring_schema()
            ranking = get_manual_ranking(int(manual_ranking_match.group(1)))
            if not ranking:
                self.send_json({"error": "Manual ranking not found"}, 404)
                return
            self.send_json({"ranking": ranking})
            return

        if parsed.path == "/api/confidence-scores":
            ensure_scoring_schema()
            self.send_json(latest_confidence_scores())
            return

        if parsed.path == "/api/confidence-runs":
            ensure_scoring_schema()
            self.send_json({"runs": list_confidence_runs(), "pinnedRunId": pinned_confidence_run_id()})
            return

        if parsed.path == "/api/cost-estimate":
            query = dict(parse_qsl(parsed.query))
            try:
                estimate = cost_estimate(
                    query.get("model"),
                    query.get("companyCount"),
                    query.get("reasoningMode"),
                )
                self.send_json({"estimate": estimate})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        if parsed.path == "/api/runs":
            ensure_scoring_schema()
            self.send_json({"runs": list_runs()})
            return

        universe_options_match = re.fullmatch(r"/api/runs/(\d+)/universe-options", parsed.path)
        if universe_options_match:
            ensure_scoring_schema()
            options = run_universe_options(int(universe_options_match.group(1)))
            if options is None:
                self.send_json({"error": "Run not found"}, 404)
                return
            self.send_json({"options": options})
            return

        result_match = re.fullmatch(r"/api/runs/(\d+)/results/(.+)", parsed.path)
        if result_match:
            ensure_scoring_schema()
            detail = get_result_detail(int(result_match.group(1)), unquote(result_match.group(2)))
            if not detail:
                self.send_json({"error": "Result not found"}, 404)
                return
            self.send_json({"detail": detail})
            return

        run_match = re.fullmatch(r"/api/runs/(\d+)", parsed.path)
        if run_match:
            ensure_scoring_schema()
            query = dict(parse_qsl(parsed.query))
            try:
                run = paginated_run(
                    int(run_match.group(1)), query.get("page", 1), query.get("pageSize", 100),
                    query.get("view", "ranking"), query.get("sort", "scoreRank"),
                    query.get("dir", "asc"), query.get("q", ""), query.get("score")
                ) if query else get_run(int(run_match.group(1)))
            except (TypeError, ValueError):
                self.send_json({"error": "Invalid pagination or filter value"}, 400)
                return
            if not run:
                self.send_json({"error": "Run not found"}, 404)
                return
            self.send_json({"run": run})
            return

        if parsed.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            previous = watched_signature()
            try:
                while True:
                    current = watched_signature()
                    if current != previous:
                        previous = current
                        self.wfile.write(b"event: reload\ndata: changed\n\n")
                    else:
                        self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    time.sleep(1)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        confidence_pin_match = re.fullmatch(r"/api/confidence-runs/(\d+)/pin", parsed.path)
        if confidence_pin_match:
            ensure_scoring_schema()
            try:
                run = set_pinned_confidence_run(int(confidence_pin_match.group(1)))
                self.send_json({"run": run})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            return

        if parsed.path == "/api/run-preview":
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                preview = preview_scoring_selection(
                    payload.get("companyCount"),
                    stock_list_id=payload.get("stockListId"),
                    tickers=payload.get("tickers") if "tickers" in payload else None,
                    minimum_confidence_score=payload.get("minimumConfidenceScore"),
                )
                self.send_json({"preview": preview})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        if parsed.path == "/api/stock-lists":
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                stock_list = save_stock_list(payload.get("name"), payload.get("tickers"))
                self.send_json({"list": stock_list}, 201)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        if parsed.path == "/api/manual-rankings":
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                ranking = create_manual_ranking(payload.get("name"), payload.get("stockListId"))
                self.send_json({"ranking": ranking}, 201)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        manual_comparison_match = re.fullmatch(r"/api/runs/(\d+)/manual-ranking", parsed.path)
        if manual_comparison_match:
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                run = set_run_manual_ranking(
                    int(manual_comparison_match.group(1)), payload.get("manualRankingId")
                )
                if not run:
                    self.send_json({"error": "Run not found"}, 404)
                    return
                self.send_json({"run": run})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        if parsed.path == "/api/portfolios/preview":
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                portfolio = calculate_portfolio(
                    payload.get("runId"),
                    payload.get("name"),
                    payload.get("marketCapLimit"),
                    payload.get("minimumScorePercentile"),
                    payload.get("maximumMultiplier"),
                    payload.get("baseWeighting", "market_cap"),
                )
                self.send_json(
                    {
                        "portfolio": portfolio,
                        "url": "/portfolio.html?preview=1",
                    },
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        stop_match = re.fullmatch(r"/api/runs/(\d+)/stop", parsed.path)
        if stop_match:
            ensure_scoring_schema()
            run = stop_scoring_run(int(stop_match.group(1)))
            if not run:
                self.send_json({"error": "Run not found"}, 404)
                return
            self.send_json({"run": run})
            return

        if parsed.path == "/api/runs":
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                name = (payload.get("name") or "").strip()
                if not name:
                    self.send_json({"error": "Run name is required."}, 400)
                    return
                prompt = normalize_scoring_prompt(payload.get("prompt"))
                model = normalize_model(payload.get("model"))
                reasoning_mode = normalize_reasoning_mode(payload.get("reasoningMode"), model)
                run_id = create_scoring_run(
                    name,
                    prompt,
                    model,
                    payload.get("companyCount"),
                    reasoning_mode,
                    stock_list_id=payload.get("stockListId"),
                    tickers=payload.get("tickers") if "tickers" in payload else None,
                    max_tokens=payload.get("maxTokens"),
                    minimum_confidence_score=payload.get("minimumConfidenceScore"),
                    low_cost_mode=payload.get("lowCostMode", False),
                )
                self.send_json({"runId": run_id, "url": f"/run.html?id={run_id}"}, 201)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        extend_match = re.fullmatch(r"/api/runs/(\d+)/extend", parsed.path)
        if extend_match:
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                run = extend_scoring_run(int(extend_match.group(1)), payload.get("companyCount"))
                if not run:
                    self.send_json({"error": "Run not found"}, 404)
                    return
                self.send_json({"run": run})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        redrive_match = re.fullmatch(r"/api/runs/(\d+)/redrive-failed", parsed.path)
        if redrive_match:
            ensure_scoring_schema()
            try:
                run = redrive_failed_scoring_run(int(redrive_match.group(1)))
                if not run:
                    self.send_json({"error": "Run not found"}, 404)
                    return
                self.send_json({"run": run})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return
        redrive_result_match = re.fullmatch(
            r"/api/runs/(\d+)/results/([^/]+)/redrive", parsed.path
        )
        if redrive_result_match:
            ensure_scoring_schema()
            try:
                ticker = unquote(redrive_result_match.group(2)).strip().upper()
                run = redrive_failed_scoring_run(
                    int(redrive_result_match.group(1)), [ticker]
                )
                if not run:
                    self.send_json({"error": "Run not found"}, 404)
                    return
                self.send_json({"run": run})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return
        self.send_json({"error": "Not found"}, 404)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        manual_score_match = re.fullmatch(
            r"/api/manual-rankings/(\d+)/scores/([^/]+)", parsed.path
        )
        if manual_score_match:
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                ranking = update_manual_ranking_score(
                    int(manual_score_match.group(1)),
                    unquote(manual_score_match.group(2)),
                    payload.get("score"),
                )
                if not ranking:
                    self.send_json({"error": "Manual ranking not found"}, 404)
                    return
                self.send_json({"ranking": ranking})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        if parsed.path == "/api/preferences/run-table-columns":
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                view = dict(parse_qsl(parsed.query)).get("view", "ranking")
                columns = save_run_table_columns_preference(payload.get("columns"), view)
                order = (
                    save_run_table_column_order_preference(payload["order"], view)
                    if "order" in payload
                    else get_run_table_column_order_preference(view)
                )
                self.send_json({"columns": columns, "order": order})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        if parsed.path == "/api/preferences/provider-table-columns":
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                columns = save_provider_table_columns_preference(payload.get("columns"))
                order = (
                    save_provider_table_column_order_preference(payload["order"])
                    if "order" in payload
                    else get_provider_table_column_order_preference()
                )
                self.send_json({"columns": columns, "order": order})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        if parsed.path == "/api/preferences/portfolio-table-columns":
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                columns = save_portfolio_table_columns_preference(payload.get("columns"))
                order = (
                    save_portfolio_table_column_order_preference(payload["order"])
                    if "order" in payload
                    else get_portfolio_table_column_order_preference()
                )
                self.send_json({"columns": columns, "order": order})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        stock_list_match = re.fullmatch(r"/api/stock-lists/(\d+)", parsed.path)
        if stock_list_match:
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                stock_list = save_stock_list(
                    payload.get("name"),
                    payload.get("tickers"),
                    int(stock_list_match.group(1)),
                )
                if not stock_list:
                    self.send_json({"error": "Stock list not found"}, 404)
                    return
                self.send_json({"list": stock_list})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        run_match = re.fullmatch(r"/api/runs/(\d+)", parsed.path)
        if run_match:
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                run = update_scoring_run(
                    int(run_match.group(1)),
                    name=payload.get("name") if "name" in payload else None,
                    prompt=payload.get("prompt") if "prompt" in payload else None,
                    starred=payload.get("starred") if "starred" in payload else None,
                    max_tokens=payload.get("maxTokens") if "maxTokens" in payload else None,
                    low_cost_mode=(
                        payload.get("lowCostMode") if "lowCostMode" in payload else None
                    ),
                    stock_list_id=(
                        payload.get("stockListId")
                        if "stockListId" in payload
                        else RUN_FIELD_UNSET
                    ),
                )
                if not run:
                    self.send_json({"error": "Run not found"}, 404)
                    return
                self.send_json({"run": run})
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        self.send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        manual_ranking_match = re.fullmatch(r"/api/manual-rankings/(\d+)", parsed.path)
        if manual_ranking_match:
            ensure_scoring_schema()
            archived = archive_manual_ranking(int(manual_ranking_match.group(1)))
            if not archived:
                self.send_json({"error": "Manual ranking not found"}, 404)
                return
            self.send_json({"archived": True})
            return

        stock_list_match = re.fullmatch(r"/api/stock-lists/(\d+)", parsed.path)
        if stock_list_match:
            ensure_scoring_schema()
            archived = archive_stock_list(int(stock_list_match.group(1)))
            if not archived:
                self.send_json({"error": "Stock list not found"}, 404)
                return
            self.send_json({"archived": True})
            return

        run_match = re.fullmatch(r"/api/runs/(\d+)", parsed.path)
        if run_match:
            ensure_scoring_schema()
            archived = delete_scoring_run(int(run_match.group(1)))
            if not archived:
                self.send_json({"error": "Run not found"}, 404)
                return
            self.send_json({"archived": True})
            return
        self.send_json({"error": "Not found"}, 404)


def main():
    ensure_scoring_schema()
    invalidated = invalidate_truncated_scoring_results()
    if invalidated:
        print(f"Marked {invalidated} token-limited scoring responses invalid.")
    mark_interrupted_runs()
    port = 3001
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving AI Stock Scorer at http://localhost:{port}")
    print("Edit server.py, index.html, styles.css, or app.js and the browser will auto-refresh.")
    server.serve_forever()


if __name__ == "__main__":
    main()
