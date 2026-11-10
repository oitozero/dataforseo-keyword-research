#!/usr/bin/env python3
"""DataForSEO keyword research, converted from the n8n workflow of the same name.

Takes the first row with Status = "Ready" from the "Main Keyword" tab, pulls
related keywords, keyword suggestions, autocomplete, content ideas, SERP results
and People Also Ask from DataForSEO, appends them to their tabs, then sets the
row's Status to "Success" and fills in "Last Run". One keyword per run.

Usage:
    python keyword_research.py
    python keyword_research.py --skip-content-ideas

Settings come from the environment or a .env file (see .env.example).
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

import gspread
import requests
from dotenv import load_dotenv
from gspread.utils import rowcol_to_a1

API_BASE = "https://api.dataforseo.com/v3/"
REQUEST_TIMEOUT_SECONDS = 120
STATUS_OK = 20000
STATUS_NO_RESULTS = 40102  # A keyword with no data is an empty result, not a failure.
STATUS_PARTIAL = 40106  # Some pages could not be retrieved, but the rest are usable.

MAIN_TAB = "Main Keyword"
MAIN_COLUMNS = ["Main Keyword", "Location", "Language", "Limit", "Status", "Last Run"]

METRIC_COLUMNS = [
    "Main Keyword", "Location", "Type", "Keyword", "MSV",
    "Search Intent", "KW Difficulty", "Competition", "CPC",
]
RELATED_TAB = "Related KW"
SUGGESTIONS_TAB = "KW Suggestion"
ALL_RESULTS_TAB = "All Results"
AUTOCOMPLETE_TAB = "Autocomplete Suggestion"
CONTENT_IDEAS_TAB = "Content Idea"
SERP_TAB = "SERP"
PAA_TAB = "PAA"

OUTPUT_COLUMNS = {
    RELATED_TAB: METRIC_COLUMNS,
    SUGGESTIONS_TAB: METRIC_COLUMNS,
    ALL_RESULTS_TAB: METRIC_COLUMNS,
    AUTOCOMPLETE_TAB: ["Main Keyword", "Location", "Type", "Autocomplete"],
    CONTENT_IDEAS_TAB: ["Main Keyword", "Location", "Type", "Subtopics"],
    SERP_TAB: ["Main Keyword", "Location", "Type", "Domain", "URL", "Title", "Description"],
    PAA_TAB: ["Main Keyword", "Location", "Type", "PAA", "Related Topic", "Related Topic Description"],
}

Row = dict[str, Any]
Post = Callable[[str, Row], Row]


class WorkflowError(Exception):
    """A problem to report to the user without a traceback."""


# --- DataForSEO -------------------------------------------------------------


def make_post(login: str, password: str) -> Post:
    """Return a function that runs one live task and returns its first result."""

    def post(path: str, task: Row) -> Row:
        response = requests.post(
            API_BASE + path,
            json=[task],
            auth=(login, password),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        try:
            body = response.json()
        except ValueError:
            raise WorkflowError(f"{path}: HTTP {response.status_code}, response was not JSON") from None

        # DataForSEO reports most failures inside an HTTP 200 body, so check both levels.
        if body.get("status_code") != STATUS_OK:
            raise WorkflowError(f"{path}: {body.get('status_code')} {body.get('status_message')}")
        tasks = body.get("tasks") or []
        if not tasks:
            raise WorkflowError(f"{path}: response contained no tasks")
        task_result = tasks[0]
        status = task_result.get("status_code")
        if status == STATUS_NO_RESULTS:
            return {}
        if status == STATUS_PARTIAL:
            print(f"Warning: {path} returned partial results: {task_result.get('status_message')}", file=sys.stderr)
        elif status != STATUS_OK:
            raise WorkflowError(f"{path}: {status} {task_result.get('status_message')}")
        return (task_result.get("result") or [{}])[0] or {}

    return post


def build_tasks(keyword: Row, skip_content_ideas: bool) -> dict[str, tuple[str, Row]]:
    """Return the API path and request body for each branch of the workflow."""
    target = {
        "keyword": keyword["Main Keyword"],
        "location_name": keyword["Location"],
        "language_name": keyword["Language"],
    }
    tasks = {
        "related": (
            "dataforseo_labs/google/related_keywords/live",
            {**target, "limit": keyword["Limit"], "include_serp_info": True,
             "include_seed_keyword": True, "depth": 2},
        ),
        "suggestions": (
            "dataforseo_labs/google/keyword_suggestions/live",
            {**target, "limit": keyword["Limit"], "include_serp_info": True,
             "include_seed_keyword": False, "ignore_synonyms": True},
        ),
        "autocomplete": (
            "serp/google/autocomplete/live/advanced",
            {**target, "client": "gws-wiz-serp"},
        ),
        "serp": (
            "serp/google/organic/live/advanced",
            {**target, "depth": 20, "people_also_ask_click_depth": 1},
        ),
    }
    if not skip_content_ideas:
        tasks["content_ideas"] = (
            "content_generation/generate_sub_topics/live",
            {"topic": keyword["Main Keyword"]},
        )
    return tasks


def fetch_all(tasks: dict[str, tuple[str, Row]], post: Post) -> dict[str, Row]:
    """Run every task in parallel. Raises if any fails, so nothing partial gets written."""
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {name: pool.submit(post, path, body) for name, (path, body) in tasks.items()}

    results: dict[str, Row] = {}
    errors: list[str] = []
    for name, future in futures.items():
        try:
            results[name] = future.result()
        except (WorkflowError, requests.RequestException) as error:
            errors.append(f"  {name}: {error}")
    if errors:
        raise WorkflowError(
            "DataForSEO request(s) failed, so nothing was written and Status is still Ready:\n"
            + "\n".join(errors)
        )
    return results


# --- Mapping API responses to sheet rows ------------------------------------


def dig(data: Any, *path: str) -> Any:
    """Read a nested value, returning "" if any level is missing or null."""
    for key in path:
        if not isinstance(data, dict):
            return ""
        data = data.get(key)
    return "" if data is None else data


def metric_row(base: Row, row_type: str, data: Any) -> Row:
    return {
        **base,
        "Type": row_type,
        "Keyword": dig(data, "keyword"),
        "MSV": dig(data, "keyword_info", "search_volume"),
        "Search Intent": dig(data, "search_intent_info", "main_intent"),
        "KW Difficulty": dig(data, "keyword_properties", "keyword_difficulty"),
        "Competition": dig(data, "keyword_info", "competition_level"),
        "CPC": dig(data, "keyword_info", "cpc"),
    }


def build_rows(keyword: Row, results: dict[str, Row]) -> dict[str, list[Row]]:
    """Turn API results into rows per tab, using the workflow's column mappings."""
    base = {"Main Keyword": keyword["Main Keyword"], "Location": keyword["Location"]}

    def items(name: str, field: str = "items") -> list[Any]:
        return results.get(name, {}).get(field) or []

    related = [metric_row(base, "Related KW", dig(i, "keyword_data")) for i in items("related")]
    suggestions = [metric_row(base, "KW Suggestions", i) for i in items("suggestions")]

    serp_items = items("serp")
    organic = [
        {
            **base,
            "Type": dig(i, "type"),
            "Domain": dig(i, "domain"),
            "URL": dig(i, "url"),
            "Title": dig(i, "title"),
            "Description": dig(i, "description"),
        }
        for i in serp_items
        if i.get("type") == "organic"
    ]

    paa = []
    for block in serp_items:
        if block.get("type") != "people_also_ask":
            continue
        for element in block.get("items") or []:
            expanded = (element.get("expanded_element") or [{}])[0]
            paa.append({
                **base,
                "Type": dig(element, "type"),
                "PAA": dig(element, "title"),
                "Related Topic": dig(expanded, "title"),
                "Related Topic Description": dig(expanded, "description"),
            })

    return {
        RELATED_TAB: related,
        SUGGESTIONS_TAB: suggestions,
        ALL_RESULTS_TAB: related + suggestions,
        AUTOCOMPLETE_TAB: [
            {**base, "Type": "Autocomplete Suggestion", "Autocomplete": dig(i, "suggestion")}
            for i in items("autocomplete")
        ],
        CONTENT_IDEAS_TAB: [
            {**base, "Type": "Content Idea", "Subtopics": topic}
            for topic in items("content_ideas", "sub_topics")
        ],
        SERP_TAB: organic,
        PAA_TAB: paa,
    }


# --- Google Sheets ----------------------------------------------------------


def require_columns(tab: str, headers: list[str], required: list[str]) -> None:
    missing = [column for column in required if column not in headers]
    if missing:
        raise WorkflowError(f'Tab "{tab}" is missing column(s): {", ".join(missing)}')


def find_ready_row(worksheet: Any) -> tuple[int, list[str], Row] | None:
    """Return (sheet row number, headers, row) for the first Ready row, if any."""
    values = worksheet.get_all_values()
    if not values:
        raise WorkflowError(f'Tab "{MAIN_TAB}" is empty')
    headers = values[0]
    require_columns(MAIN_TAB, headers, MAIN_COLUMNS)
    for row_number, cells in enumerate(values[1:], start=2):
        row = dict(zip(headers, cells))
        if row.get("Status") == "Ready":
            return row_number, headers, row
    return None


def validate_keyword(row: Row, row_number: int) -> Row:
    """Check the inputs and convert Limit to an int before any paid call is made."""
    keyword = {column: str(row.get(column, "")).strip() for column in MAIN_COLUMNS[:4]}
    empty = [column for column, value in keyword.items() if not value]
    if empty:
        raise WorkflowError(f'Row {row_number} of "{MAIN_TAB}" has no value for: {", ".join(empty)}')
    try:
        keyword["Limit"] = int(keyword["Limit"].replace(",", ""))
    except ValueError:
        raise WorkflowError(f'Row {row_number}: Limit must be a whole number, got "{keyword["Limit"]}"') from None
    if keyword["Limit"] < 1:
        raise WorkflowError(f"Row {row_number}: Limit must be at least 1")
    return keyword


def open_output_tabs(spreadsheet: Any, tabs: list[str]) -> dict[str, tuple[Any, list[str]]]:
    """Open each tab and check its headers, so a bad sheet fails before any spend."""
    opened = {}
    for tab in tabs:
        try:
            worksheet = spreadsheet.worksheet(tab)
        except gspread.exceptions.WorksheetNotFound:
            raise WorkflowError(f'The spreadsheet has no tab named "{tab}"') from None
        headers = worksheet.row_values(1)
        require_columns(tab, headers, OUTPUT_COLUMNS[tab])
        opened[tab] = (worksheet, headers)
    return opened


def append_rows(worksheet: Any, headers: list[str], rows: list[Row]) -> None:
    """Append rows, placing each value under its column by header name."""
    if not rows:
        return
    values = [[row.get(header, "") for header in headers] for row in rows]
    # RAW keeps a scraped title such as "=IMPORTXML(...)" as text instead of running
    # it as a formula. table_range anchors the append to the table that starts at A1.
    worksheet.append_rows(values, value_input_option="RAW", table_range="A1")


def mark_success(worksheet: Any, headers: list[str], row_number: int) -> None:
    last_run = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    updates = {"Status": "Success", "Last Run": last_run}
    worksheet.batch_update(
        [
            {"range": rowcol_to_a1(row_number, headers.index(column) + 1), "values": [[value]]}
            for column, value in updates.items()
        ],
        value_input_option="RAW",
    )


# --- Workflow ---------------------------------------------------------------


def run(spreadsheet: Any, post: Post, skip_content_ideas: bool = False) -> None:
    main_worksheet = spreadsheet.worksheet(MAIN_TAB)
    found = find_ready_row(main_worksheet)
    if found is None:
        print('No row with Status "Ready". Nothing to do.')
        return
    row_number, main_headers, row = found
    keyword = validate_keyword(row, row_number)

    tabs = [tab for tab in OUTPUT_COLUMNS if not (skip_content_ideas and tab == CONTENT_IDEAS_TAB)]
    output_tabs = open_output_tabs(spreadsheet, tabs)

    print(f'Researching "{keyword["Main Keyword"]}" ({keyword["Location"]}, {keyword["Language"]})')
    results = fetch_all(build_tasks(keyword, skip_content_ideas), post)
    rows_by_tab = build_rows(keyword, results)

    written: list[str] = []
    try:
        for tab, (worksheet, headers) in output_tabs.items():
            append_rows(worksheet, headers, rows_by_tab[tab])
            written.append(tab)
            print(f"  {tab}: {len(rows_by_tab[tab])} rows")
    except gspread.exceptions.GSpreadException as error:
        raise WorkflowError(
            f"Writing to the sheet failed: {error}\n"
            f"Status is still Ready. Tabs already written: {', '.join(written) or 'none'}. "
            "Remove those rows before re-running, or they will be duplicated."
        ) from error

    mark_success(main_worksheet, main_headers, row_number)
    print(f'Done. Row {row_number} of "{MAIN_TAB}" is now Success.')


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise WorkflowError(f"Missing setting {name}. See .env.example.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DataForSEO keyword research for one Ready row.")
    parser.add_argument(
        "--skip-content-ideas",
        action="store_true",
        help="skip the Content Generation sub-topics call and the Content Idea tab",
    )
    args = parser.parse_args()
    load_dotenv()

    try:
        post = make_post(require_env("DATAFORSEO_LOGIN"), require_env("DATAFORSEO_PASSWORD"))
        sheet_id = require_env("GOOGLE_SHEET_ID")
        credentials = require_env("GOOGLE_SERVICE_ACCOUNT_FILE")
        spreadsheet = gspread.service_account(filename=credentials).open_by_key(sheet_id)
        run(spreadsheet, post, skip_content_ideas=args.skip_content_ideas)
    except (WorkflowError, gspread.exceptions.GSpreadException, FileNotFoundError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
