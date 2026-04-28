# eBird LangChain Birding Assistant

![Unit Tests](https://github.com/cgauvi/ebird-llm/actions/workflows/tests.yml/badge.svg)

An agentic Python application that lets you explore bird sightings through natural language.  
A LangChain agent backed by a **HuggingFace Inference API** model queries the **eBird API v2**
and renders interactive maps and charts inside a **Streamlit** split-panel UI.

> For AWS deployment instructions see [infra/README.md](infra/README.md).

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│  Streamlit app.py  (split layout: chat left, visualization right)     │
│                                                                       │
│  Authentication gate (AWS Cognito User Pool)                          │
│  ├── Sign-up / Sign-in / Email verification                          │
│  └── Usage tracking + rate limiting (DynamoDB)                        │
│       ├── 10 sessions / month (configurable)                          │
│       └── 30 prompts  / month (configurable)                          │
│                                                                       │
│  LangChain ReAct Agent (langgraph)                                    │
│  ├── LLM: ChatHuggingFace  (selectable model — see .env.example)     │
│  ├── Memory: stateless — rolling-summary compression of history       │
│  └── Tools                                                            │
│       ├── eBird tools (src/tools/ebird_tools.py)                      │
│       │    ├── get_recent_observations_by_location                    │
│       │    ├── get_recent_observations_by_region                      │
│       │    ├── get_historic_observations                              │
│       │    ├── get_nearby_hotspots                                    │
│       │    ├── get_region_list                                        │
│       │    ├── get_notable_observations_by_location                   │
│       │    ├── get_region_info                                        │
│       │    ├── get_top100_contributors                                │
│       │    ├── get_species_list                                       │
│       │    ├── get_region_stats                                       │
│       │    └── validate_species                                       │
│       ├── Visualization tools (src/tools/viz_tools.py)                │
│       │    ├── create_sightings_map  →  folium map                    │
│       │    └── create_historical_chart  →  plotly bar/line            │
│       └── Summarizer tool (src/tools/summarizer_tool.py)              │
│            └── summarize_output  →  condenses large text outputs      │
│                                                                       │
│  src/utils/ebird_client.py   ──►  https://api.ebird.org/v2            │
│  src/utils/auth.py           ──►  AWS Cognito (sign-up / sign-in)     │
│  src/utils/usage_tracker.py  ──►  DynamoDB (rate limits + LLM audit)  │
│  src/utils/state.py          ──►  VizBuffer (side-channel to UI)      │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
ebird-llm/
├── app.py                      # Streamlit entry point (auth gate + UI)
├── src/
│   ├── __init__.py
│   ├── agent.py                # LangChain ReAct agent (langgraph)
│   ├── config.py               # HuggingFace model catalog + build_llm()
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── ebird_tools.py      # 11 eBird API @tool functions
│   │   ├── viz_tools.py        # map + chart @tool functions
│   │   └── summarizer_tool.py  # condense large tool outputs
│   └── utils/
│       ├── __init__.py
│       ├── auth.py             # AWS Cognito sign-up / sign-in
│       ├── ebird_client.py     # Thin HTTP client for eBird API v2
│       ├── logging_config.py   # Structured logging + log buffer
│       ├── region_cache.py     # Region code auto-correction cache
│       ├── state.py            # VizBuffer shared state
│       ├── summarizer.py       # Text summarization utilities
│       └── usage_tracker.py    # DynamoDB usage tracking + rate limits
├── tests/                      # pytest test suite
├── environment.yml             # Conda environment specification (local dev)
├── Dockerfile                  # Multi-stage production image
├── requirements-docker.txt     # Pip deps for Docker build
├── requirements-test.txt       # Extra test deps (pytest, mocking)
├── infra/                      # Terraform — AWS ECS/Fargate + Cognito + DynamoDB
│   └── README.md               # Deployment guide
├── .env                        # API keys — local only, never committed
├── .env.example                # API key template (safe to commit)
└── .gitignore
```

---

## Prerequisites

- [Conda](https://docs.conda.io/en/latest/miniconda.html) (miniconda or anaconda)
- An [eBird API key](https://ebird.org/api/keygen) — free, requires an eBird account
- A [HuggingFace API token](https://huggingface.co/settings/tokens) with Inference API access

---

## Local Setup

### 1. Create the Conda environment

```bash
conda env create -f environment.yml
conda activate ebird_llm_env
```

#### Lock the environment

Generate lock-files that pin every resolved package version so the environment
can be reproduced exactly:

```bash
# Conda lock-file (cross-platform, build hashes omitted)
conda env export --no-builds > environment-lock.yml

# Pip lock-file used by the Docker build
pip freeze > requirements-docker.lock.txt
```

Commit both files. `environment.yml` is the editable source of intent (loose
bounds); the lock-files are what teammates and CI actually install from.

**Recreate from the lock-files:**

```bash
# Recreate the Conda environment exactly
conda env create -f environment-lock.yml

# Or recreate just the pip layer (e.g. inside Docker)
pip install -r requirements-docker.lock.txt
```

> To update dependencies: edit `environment.yml`, recreate the env, then
> re-export the lock-files and commit them.

### 2. Configure API keys

The `.env` file lives at the **project root** (same level as `app.py`).
It is listed in `.gitignore` and will never be committed.

```bash
cp .env.example .env
```

Edit `.env` and fill in your keys:

```dotenv
EBIRD_API_KEY=your_ebird_api_key_here
HUGGINGFACE_API_TOKEN=your_huggingface_token_here
# HF_MODEL_ID=qwen2.5-72b    # optional — see .env.example for all aliases
```

> `load_dotenv()` is called once in `app.py` before any `src.*` imports, so all
> env vars are available to every module without repeated `load_dotenv()` calls.

### 3. Run the app

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`.

### 4. Run with Docker (optional)

Build and run the production image locally, using the same `.env` file for API keys:

```bash
docker build --target runtime -t ebird-llm-local \
  --build-arg BUILD_VERSION=$(git describe --tags --always) .
docker run --rm -p 8501:8501 --env-file .env --no-healthcheck --privileged ebird-llm-local
```

> `--privileged` is required for local dev on systems where the cgroup v1 pids
> subsystem is not fully mounted (common on Ubuntu with newer kernels or WSL2).
> It is not used in production — ECS Fargate manages resource limits separately.

Run the test suite inside Docker (uses the same deps as production):

```bash
docker build --target test -t ebird-llm-local:test .
docker run --rm --env-file .env --no-healthcheck --privileged ebird-llm-local:test
```

The app opens at `http://localhost:8501`.


---

## Track / Monitor Usage

Query DynamoDB session logs directly with the AWS CLI:

```bash
# All logs for a specific session
aws dynamodb query \
  --table-name ebird-llm-dev-session-logs \
  --key-condition-expression "session_id = :sid" \
  --expression-attribute-values '{":sid": {"S": "<session-uuid>"}}'

# All logs for a user (via GSI)
aws dynamodb query \
  --table-name ebird-llm-dev-session-logs \
  --index-name user-log-index \
  --key-condition-expression "user_id = :uid" \
  --expression-attribute-values '{":uid": {"S": "user@example.com"}}'
```

---

## Model Selection

Use the **Model** dropdown in the Streamlit sidebar to switch models at runtime.
Changing the selection resets the conversation and rebuilds the LLM automatically.

| Alias | Model | Notes |
|---|---|---|
| `qwen2.5-72b` | Qwen/Qwen2.5-72B-Instruct | **default** |
| `gemma-3-27b` | google/gemma-3-27b-it | requires Google licence |
| `gpt-oss-120b` | openai/gpt-oss-120b | high reasoning, harmony response format |
| `gpt-oss-20b` | openai/gpt-oss-20b | lower latency, harmony response format |

All models are served through the HuggingFace Inference API and must support tool/function calling.

---

## Usage

Type natural language queries in the chat panel. Examples:

| Query | What happens |
|---|---|
| *Show recent bird sightings near lat 48.85, lng 2.35* | Fetches observations, renders a folium map |
| *Map notable birds near lat 51.5, lng -0.12 in the last 14 days* | Fetches rare sightings, renders a map |
| *Historic observations for US-NY on 2024-05-01, then chart them* | Fetches historic data, renders a bar chart |
| *List the states in the US* | Returns a text list of US state region codes |
| *Find hotspots within 10 km of lat 40.71, lng -74.01* | Returns nearby eBird hotspot list |
| *Show me a line chart of observations in CA-ON for 2024-06-15* | Historic data + time-series line chart |

The right panel updates automatically after each agent turn that produces a visualization.  
Click **New Conversation** in the sidebar to reset chat history and memory.

---

## Tools Reference

### eBird API Tools (`src/tools/ebird_tools.py`)

| Tool | eBird Endpoint | Key Parameters |
|---|---|---|
| `get_recent_observations_by_location` | `GET /data/obs/geo/recent` | `lat`, `lng`, `dist_km`, `days_back`, `species_code` |
| `get_recent_observations_by_region` | `GET /data/obs/{regionCode}/recent` | `region_code`, `days_back`, `species_code` |
| `get_historic_observations` | `GET /data/obs/{regionCode}/historic/{y}/{m}/{d}` | `region_code`, `year`, `month`, `day` |
| `get_nearby_hotspots` | `GET /ref/hotspot/geo` | `lat`, `lng`, `dist_km` |
| `get_region_list` | `GET /ref/region/list/{type}/{parent}` | `region_type`, `parent_region_code` |
| `get_notable_observations_by_location` | `GET /data/obs/geo/recent/notable` | `lat`, `lng`, `dist_km`, `days_back` |
| `get_region_info` | `GET /ref/region/info/{regionCode}` | `region_code` |
| `get_top100_contributors` | `GET /product/top100/{regionCode}/{y}/{m}/{d}` | `region_code`, `year`, `month`, `day` |
| `get_species_list` | `GET /product/spplist/{regionCode}` | `region_code` |
| `get_region_stats` | `GET /product/stats/{regionCode}/{y}/{m}/{d}` | `region_code`, `year`, `month`, `day` |
| `validate_species` | *(local cache / species list lookup)* | `species_name`, `region_code` |

### Visualization Tools (`src/tools/viz_tools.py`)

| Tool | Output | Parameters |
|---|---|---|
| `create_sightings_map` | Interactive folium map (circle markers, tooltips) | `observations_json` or `observations_file` |
| `create_historical_chart` | Plotly bar or line chart | `observations_json` or `observations_file`, `chart_type`, `top_n_species` |

### Summarizer Tool (`src/tools/summarizer_tool.py`)

| Tool | Output | Parameters |
|---|---|---|
| `summarize_output` | Condensed text summary | `raw_output` |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `EBIRD_API_KEY` | Yes | eBird API key — https://ebird.org/api/keygen |
| `HUGGINGFACE_API_TOKEN` | Yes | HuggingFace token for Inference API access |
| `HF_MODEL_ID` | No | Short alias or full repo ID (default: `qwen2.5-72b`) |
| `COGNITO_USER_POOL_ID` | No* | AWS Cognito User Pool ID (enables auth gate) |
| `COGNITO_CLIENT_ID` | No* | Cognito App Client ID |
| `DYNAMODB_TABLE_PREFIX` | No* | DynamoDB table name prefix (default: `ebird-llm-dev`) |
| `AWS_REGION` | No | AWS region for Cognito + DynamoDB (default: `us-east-2`) |
| `MAX_LLM_CALLS_PER_MONTH` | No | LLM-call limit per user per month (default: `40`) |

\* Authentication is optional. When `COGNITO_USER_POOL_ID` and `COGNITO_CLIENT_ID`
are unset the app runs without login (useful for local development). Set them to
enable the full auth + rate-limiting flow.

---

## eBird API Reference

Full documentation: https://documenter.getpostman.com/view/664302/S1ENwy59  
Authentication: `x-ebirdapitoken` header, injected automatically by `src/utils/ebird_client.py`.
