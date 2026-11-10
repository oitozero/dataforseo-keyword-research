"""Offline tests: no DataForSEO credits are spent and no real sheet is touched."""

import inspect
import re

import gspread
import pytest

import keyword_research as kr

MAIN_HEADERS = ["Status", "Main Keyword", "Location", "Language", "Limit", "Last Run",
                "Main Keyword + Location Pair"]
METRIC_API_ITEM = {
    "keyword": "best shoes",
    "keyword_info": {"search_volume": 100, "competition_level": "LOW", "cpc": 1.5},
    "search_intent_info": {"main_intent": "commercial"},
    "keyword_properties": {"keyword_difficulty": 42},
}
RESULTS = {
    "dataforseo_labs/google/related_keywords/live": {"items": [{"keyword_data": METRIC_API_ITEM}]},
    "dataforseo_labs/google/keyword_suggestions/live": {"items": [METRIC_API_ITEM]},
    "serp/google/autocomplete/live/advanced": {"items": [{"suggestion": "shoes near me"}]},
    "content_generation/generate_sub_topics/live": {"sub_topics": ["Fit", "Price"]},
    "serp/google/organic/live/advanced": {"items": [
        {"type": "organic", "domain": "a.com", "url": "https://a.com", "title": "=A", "description": "d"},
        {"type": "paid", "domain": "ad.com"},
        {"type": "people_also_ask", "items": [
            {"type": "people_also_ask_element", "title": "Why?",
             "expanded_element": [{"title": "Because", "description": "Reasons"}]},
            {"type": "people_also_ask_element", "title": "How?", "expanded_element": None},
        ]},
    ]},
}


class FakeWorksheet:
    """Records writes. Calls are checked against real gspread signatures."""

    def __init__(self, values):
        self.values = [list(row) for row in values]
        self.appended = []
        self.updates = []

    def get_all_values(self):
        return self.values

    def row_values(self, row):
        return self.values[row - 1]

    def append_rows(self, values, **kwargs):
        inspect.signature(gspread.Worksheet.append_rows).bind(self, values, **kwargs)
        self.appended.append((values, kwargs))

    def batch_update(self, data, **kwargs):
        inspect.signature(gspread.Worksheet.batch_update).bind(self, data, **kwargs)
        self.updates.append((data, kwargs))


class FakeSpreadsheet:
    def __init__(self, main_rows, overrides=None):
        self.tabs = {kr.MAIN_TAB: FakeWorksheet([MAIN_HEADERS, *main_rows])}
        for tab, columns in kr.OUTPUT_COLUMNS.items():
            self.tabs[tab] = FakeWorksheet([columns])
        for tab, columns in (overrides or {}).items():
            self.tabs[tab] = FakeWorksheet([columns])

    def worksheet(self, title):
        if title not in self.tabs:
            raise gspread.exceptions.WorksheetNotFound(title)
        return self.tabs[title]


READY = ["Ready", "shoes", "United States", "English", "100", "", "shoes|US"]
DONE = ["Success", "old", "United States", "English", "100", "2026-01-01", "old|US"]


def fake_post(calls=None, fail=None):
    def post(path, task):
        if calls is not None:
            calls.append((path, task))
        if fail and fail in path:
            raise kr.WorkflowError(f"{path}: boom")
        return RESULTS[path]
    return post


def rows_as_dicts(worksheet):
    headers = worksheet.values[0]
    return [dict(zip(headers, row)) for values, _ in worksheet.appended for row in values]


def test_request_bodies_match_the_workflow():
    calls = []
    kr.run(FakeSpreadsheet([READY]), fake_post(calls))
    bodies = dict(calls)
    target = {"keyword": "shoes", "location_name": "United States", "language_name": "English"}
    assert bodies == {
        "dataforseo_labs/google/related_keywords/live": {
            **target, "limit": 100, "include_serp_info": True, "include_seed_keyword": True, "depth": 2},
        "dataforseo_labs/google/keyword_suggestions/live": {
            **target, "limit": 100, "include_serp_info": True, "include_seed_keyword": False,
            "ignore_synonyms": True},
        "serp/google/autocomplete/live/advanced": {**target, "client": "gws-wiz-serp"},
        "serp/google/organic/live/advanced": {**target, "depth": 20, "people_also_ask_click_depth": 1},
        "content_generation/generate_sub_topics/live": {"topic": "shoes"},
    }


def test_rows_land_in_the_right_tabs():
    sheet = FakeSpreadsheet([READY])
    kr.run(sheet, fake_post())
    base = {"Main Keyword": "shoes", "Location": "United States"}
    metrics = {**base, "Keyword": "best shoes", "MSV": 100, "Search Intent": "commercial",
               "KW Difficulty": 42, "Competition": "LOW", "CPC": 1.5}

    assert rows_as_dicts(sheet.tabs["Related KW"]) == [{**metrics, "Type": "Related KW"}]
    assert rows_as_dicts(sheet.tabs["KW Suggestion"]) == [{**metrics, "Type": "KW Suggestions"}]
    assert [r["Type"] for r in rows_as_dicts(sheet.tabs["All Results"])] == ["Related KW", "KW Suggestions"]
    assert rows_as_dicts(sheet.tabs["Autocomplete Suggestion"]) == [
        {**base, "Type": "Autocomplete Suggestion", "Autocomplete": "shoes near me"}]
    assert [r["Subtopics"] for r in rows_as_dicts(sheet.tabs["Content Idea"])] == ["Fit", "Price"]
    assert rows_as_dicts(sheet.tabs["SERP"]) == [
        {**base, "Type": "organic", "Domain": "a.com", "URL": "https://a.com", "Title": "=A", "Description": "d"}]
    assert rows_as_dicts(sheet.tabs["PAA"]) == [
        {**base, "Type": "people_also_ask_element", "PAA": "Why?", "Related Topic": "Because",
         "Related Topic Description": "Reasons"},
        {**base, "Type": "people_also_ask_element", "PAA": "How?", "Related Topic": "",
         "Related Topic Description": ""},
    ]


def test_values_follow_header_order_and_extra_columns_stay_blank():
    reordered = ["Notes", "Autocomplete", "Type", "Location", "Main Keyword"]
    sheet = FakeSpreadsheet([READY], {"Autocomplete Suggestion": reordered})
    kr.run(sheet, fake_post())
    values, kwargs = sheet.tabs["Autocomplete Suggestion"].appended[0]
    assert values == [["", "shoes near me", "Autocomplete Suggestion", "United States", "shoes"]]
    assert kwargs == {"value_input_option": "RAW", "table_range": "A1"}


def test_success_updates_only_the_first_ready_row():
    sheet = FakeSpreadsheet([DONE, READY, READY])
    kr.run(sheet, fake_post())
    (data, kwargs), = sheet.tabs[kr.MAIN_TAB].updates
    assert data[0] == {"range": "A3", "values": [["Success"]]}
    assert data[1]["range"] == "F3"
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC", data[1]["values"][0][0])
    assert kwargs == {"value_input_option": "RAW"}


def test_no_ready_row_does_nothing():
    calls = []
    sheet = FakeSpreadsheet([DONE])
    kr.run(sheet, fake_post(calls))
    assert calls == []
    assert sheet.tabs[kr.MAIN_TAB].updates == []


def test_api_failure_writes_nothing():
    sheet = FakeSpreadsheet([READY])
    with pytest.raises(kr.WorkflowError, match="generate_sub_topics"):
        kr.run(sheet, fake_post(fail="generate_sub_topics"))
    assert all(not tab.appended and not tab.updates for tab in sheet.tabs.values())


def test_skip_content_ideas_still_succeeds():
    calls = []
    sheet = FakeSpreadsheet([READY])
    del sheet.tabs["Content Idea"]
    kr.run(sheet, fake_post(calls, fail="generate_sub_topics"), skip_content_ideas=True)
    assert not any("generate_sub_topics" in path for path, _ in calls)
    assert sheet.tabs[kr.MAIN_TAB].updates


@pytest.mark.parametrize("case", ["missing_tab", "missing_column", "bad_limit", "blank_location"])
def test_bad_setup_fails_before_any_paid_call(case):
    row = list(READY)
    sheet = FakeSpreadsheet([row])
    if case == "missing_tab":
        del sheet.tabs["PAA"]
    elif case == "missing_column":
        sheet.tabs["SERP"] = FakeWorksheet([["Main Keyword"]])
    elif case == "bad_limit":
        sheet.tabs[kr.MAIN_TAB].values[1][4] = "lots"
    else:
        sheet.tabs[kr.MAIN_TAB].values[1][2] = " "
    calls = []
    with pytest.raises(kr.WorkflowError):
        kr.run(sheet, fake_post(calls))
    assert calls == []


def test_null_nested_metrics_become_blank():
    row = kr.metric_row({}, "Related KW", {"keyword": "x", "keyword_info": None, "search_intent_info": None})
    assert row["Keyword"] == "x"
    assert row["MSV"] == row["CPC"] == row["Search Intent"] == row["KW Difficulty"] == ""


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


@pytest.mark.parametrize("body, expected", [
    ({"status_code": 20000, "tasks": [{"status_code": 20000, "result": [{"items": [1]}]}]}, {"items": [1]}),
    ({"status_code": 20000, "tasks": [{"status_code": 20000, "result": None}]}, {}),
    ({"status_code": 20000, "tasks": [{"status_code": 40102, "status_message": "No Search Results."}]}, {}),
    ({"status_code": 20000, "tasks": [{"status_code": 40106, "result": [{"items": [2]}]}]}, {"items": [2]}),
])
def test_client_returns_first_result(monkeypatch, body, expected):
    sent = {}

    def post(url, **kwargs):
        sent.update(kwargs, url=url)
        return FakeResponse(body)

    monkeypatch.setattr(kr.requests, "post", post)
    keyword = 'men\'s "running" shoes'
    assert kr.make_post("me", "pw")("some/path", {"keyword": keyword}) == expected
    assert sent["url"] == "https://api.dataforseo.com/v3/some/path"
    assert sent["auth"] == ("me", "pw")
    assert sent["json"] == [{"keyword": keyword}]


@pytest.mark.parametrize("body", [
    {"status_code": 40100, "status_message": "Not authorized"},
    {"status_code": 20000, "tasks": [{"status_code": 40501, "status_message": "Invalid Field"}]},
    {"status_code": 20000, "tasks": []},
])
def test_client_raises_on_errors_inside_a_200(monkeypatch, body):
    monkeypatch.setattr(kr.requests, "post", lambda url, **kwargs: FakeResponse(body))
    with pytest.raises(kr.WorkflowError):
        kr.make_post("me", "pw")("some/path", {})
