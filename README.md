# DataForSEO Keyword Research

Researches one keyword at a time with [DataForSEO](https://dataforseo.com/) and saves the results to a Google Sheet. This script replaces the n8n workflow "DataForSEO Keyword Research".

Each run:

1. Finds the first row in the `Main Keyword` tab with `Status` set to `Ready`.
2. Gets related keywords, keyword suggestions, autocomplete suggestions, content ideas, organic search results and People Also Ask questions.
3. Adds the results to the matching tabs.
4. Sets the row's `Status` to `Success` and fills in `Last Run`.

## Known issue: Content Ideas

DataForSEO's sub-topics endpoint currently returns this error:

```text
50304 This function temporarily unavailable. Please contact support for more information.
```

Until it works again, run the script with `--skip-content-ideas`. Without the flag, the run stops with this error and writes nothing, and you may still be charged for the other API calls.

## Setup

You need Python 3 (tested on 3.13), a DataForSEO account with credit, and a Google account that can create a service account.

### 1. Install the dependencies

Run these from this folder:

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

### 2. Create your settings file

```bash
cp .env.example .env
```

Fill in `.env`. Don't commit it or share it.

| Setting | Value |
|---|---|
| `DATAFORSEO_LOGIN` | API login from [your DataForSEO API access page](https://app.dataforseo.com/api-access) |
| `DATAFORSEO_PASSWORD` | API password from the same page, which is not your website password |
| `GOOGLE_SHEET_ID` | The part of the spreadsheet URL between `/d/` and `/edit` |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Path to your service account key, for example `service-account.json` |

### 3. Give the script access to Google Sheets

1. In the [Google Cloud console](https://console.cloud.google.com/), create or choose a project.
2. Enable the **Google Sheets API** and the **Google Drive API**.
3. Create a service account, add a JSON key to it, and download the key. Don't commit or share this file either.
4. Save the key in this folder as `service-account.json`, or point `GOOGLE_SERVICE_ACCOUNT_FILE` to where you saved it.
5. Open your spreadsheet, click **Share**, and add the `client_email` from the key file as an **Editor**.

### 4. Set up the spreadsheet

The spreadsheet needs these tabs, with these column names in row 1. Names are case-sensitive. Column order doesn't matter, and any extra columns are left blank.

| Tab | Columns |
|---|---|
| `Main Keyword` | `Main Keyword`, `Location`, `Language`, `Limit`, `Status`, `Last Run` |
| `Related KW` | `Main Keyword`, `Location`, `Type`, `Keyword`, `MSV`, `Search Intent`, `KW Difficulty`, `Competition`, `CPC` |
| `KW Suggestion` | Same as `Related KW` |
| `All Results` | Same as `Related KW` |
| `Autocomplete Suggestion` | `Main Keyword`, `Location`, `Type`, `Autocomplete` |
| `Content Idea` | `Main Keyword`, `Location`, `Type`, `Subtopics` |
| `SERP` | `Main Keyword`, `Location`, `Type`, `Domain`, `URL`, `Title`, `Description` |
| `PAA` | `Main Keyword`, `Location`, `Type`, `PAA`, `Related Topic`, `Related Topic Description` |

`Content Idea` is optional if you always use `--skip-content-ideas`.

## Usage

### 1. Add a keyword

Add a row to the `Main Keyword` tab:

| Main Keyword | Location | Language | Limit | Status | Last Run |
|---|---|---|---|---|---|
| gift marathon runner | United States | English | 100 | Ready | |

- **Main Keyword:** the keyword to research.
- **Location** and **Language:** full names that DataForSEO recognizes, such as `United States` and `English`.
- **Limit:** the maximum number of related keywords and the maximum number of keyword suggestions to return.
- **Status:** must be exactly `Ready`.
- **Last Run:** leave blank. The script fills it in with a UTC time, such as `2026-01-31 09:30:00 UTC`.

### 2. Run the script

Activate the environment if it isn't already active:

```bash
source .venv/bin/activate
```

While the Content Ideas issue continues, run:

```bash
python keyword_research.py --skip-content-ideas
```

Once the endpoint works again, run:

```bash
python keyword_research.py
```

Each run handles one `Ready` row. If you have several, run the script once for each. If no row is `Ready`, it exits without making any API calls.

### 3. Read the results

| Tab | What it contains |
|---|---|
| `Related KW` | Related keywords with search volume, intent, difficulty, competition and CPC |
| `KW Suggestion` | Keyword suggestions with the same metrics |
| `All Results` | The `Related KW` and `KW Suggestion` rows combined, labelled by `Type` |
| `Autocomplete Suggestion` | Google autocomplete suggestions |
| `Content Idea` | Suggested subtopics |
| `SERP` | Organic results from the search results page |
| `PAA` | People Also Ask questions, with the first expanded answer |

## Costs

DataForSEO charges for API calls, and a normal run makes five (four with `--skip-content-ideas`). See [DataForSEO pricing](https://dataforseo.com/pricing) for current rates.

The script checks your settings, tabs, columns and keyword row before it makes any API calls, so those mistakes don't cost credits. Results aren't saved between runs, so a re-run after a failed API call pays for every call again.

## When something goes wrong

| What you see | What to do |
|---|---|
| `Missing setting ...` | Fill in that value in `.env`. |
| `The spreadsheet has no tab named "..."` | Create the tab or correct its name. See [Set up the spreadsheet](#4-set-up-the-spreadsheet). |
| `Tab "..." is missing column(s): ...` | Add or rename those columns in row 1. |
| `Row N ... has no value for: ...` | Fill in those cells in the `Main Keyword` tab. |
| `Limit must be a whole number` or `Limit must be at least 1` | Change `Limit` to a whole number of 1 or more. |
| `content_ideas: ... 50304 ...` | Use `--skip-content-ideas`. See [Known issue](#known-issue-content-ideas). |
| `40100` | Check the DataForSEO login and password in `.env`. |
| `40200` or `40210` | Add funds to your DataForSEO account. |
| `DataForSEO request(s) failed, so nothing was written ...` | Fix the error listed under it and run again. No rows were added, and `Status` is still `Ready`. |
| `Writing to the sheet failed ...` | Remove the rows from the tabs listed in the message, then run again. Otherwise they will be duplicated. |
| A Google permission error (`403`) | Share the spreadsheet with the service account's `client_email` as an Editor, and check that both Google APIs are enabled. |

DataForSEO lists all of its codes on its [errors page](https://docs.dataforseo.com/v3/appendix/errors).

## Good to know

- **Re-running a keyword:** setting a finished row back to `Ready` adds a second set of rows. The script doesn't remove duplicates.
- **No results:** if DataForSEO has no data for a request, that tab gets no new rows and the run can still finish as `Success`.
- **Partial results:** if DataForSEO returns only some results, the script prints a warning and keeps what it received.
- **Failed runs:** the script doesn't set a failed status. The row stays `Ready`, and the error appears in the terminal.

## Running the tests

The tests run offline, so they spend no credits and don't touch a real spreadsheet.

```bash
pip install pytest
```

```bash
pytest
```

## Credits

Thanks to [Rank Expand Academy](https://www.skool.com/rank-expand-academy).

## License

MIT. See [LICENSE](LICENSE).
