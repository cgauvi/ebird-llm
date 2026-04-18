# eBird LangChain Birding Assistant

![Unit Tests](https://github.com/cgauvi/ebird-llm/actions/workflows/tests.yml/badge.svg)

An agentic Python application that lets you explore bird sightings through natural language.  
A LangChain agent backed by a **HuggingFace Inference API** model queries the **eBird API v2**
and renders interactive maps and charts inside a **Streamlit** split-panel UI.

> For AWS deployment instructions see [infra/README.md](infra/README.md).

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Streamlit app.py  (split layout: chat left, visualization right)   │
│                                                                     │
│  LangChain AgentExecutor                                            │
│  ├── LLM: ChatHuggingFace  (selectable model — see .env.example)   │
│  ├── Memory: ConversationBufferWindowMemory (k=10)                  │
│  └── Tools                                                          │
│       ├── eBird tools (src/tools/ebird_tools.py)                    │
│       │    ├── get_recent_observations_by_location                  │
│       │    ├── get_recent_observations_by_region                    │
│       │    ├── get_historic_observations                            │
│       │    ├── get_nearby_hotspots                                  │
│       │    ├── get_region_list                                      │
│       │    └── get_notable_observations_by_location                 │
│       └── Visualization tools (src/tools/viz_tools.py)              │
│            ├── create_sightings_map  →  folium map                  │
│            └── create_historical_chart  →  plotly bar/line          │
│                                                                     │
│  src/utils/ebird_client.py  ──►  https://api.ebird.org/v2           │
│  src/utils/state.py          ──►  VizBuffer (side-channel to UI)    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
ebird-llm/
├── app.py                      # Streamlit entry point (UI only)
├── src/                        # All application logic
│   ├── __init__.py
│   ├── agent.py                # LangChain agent + executor setup
│   ├── config.py               # HuggingFace model catalog + build_llm()
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── ebird_tools.py      # 6 eBird API @tool functions
│   │   └── viz_tools.py        # map + chart @tool functions
│   └── utils/
│       ├── __init__.py
│       ├── ebird_client.py     # Thin HTTP client for eBird API v2
│       └── state.py            # VizBuffer shared state
├── environment.yml             # Conda environment specification (local dev)
├── Dockerfile                  # Multi-stage production image
├── requirements-docker.txt     # Pip deps for Docker build
├── infra/                      # Terraform — AWS ECS/Fargate deployment
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
docker build --target runtime -t ebird-llm .
docker run --rm -p 8501:8501 --env-file .env ebird-llm
```

Run the test suite inside Docker (uses the same deps as production):

```bash
docker build --target test -t ebird-llm:test .
docker run --rm --env-file .env ebird-llm:test
```

The app opens at `http://localhost:8501`.

---

## Model Selection

Use the **Model** dropdown in the Streamlit sidebar to switch models at runtime.
Changing the selection resets the conversation and rebuilds the LLM automatically.

| Alias | Model | Notes |
|---|---|---|
| `qwen2.5-72b` | Qwen/Qwen2.5-72B-Instruct | **default** |
| `gemma-3-27b` | google/gemma-3-27b-it | requires Google licence |

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

### Visualization Tools (`src/tools/viz_tools.py`)

| Tool | Output | Parameters |
|---|---|---|
| `create_sightings_map` | Interactive folium map (circle markers, tooltips) | `observations_json` |
| `create_historical_chart` | Plotly bar or line chart | `observations_json`, `chart_type`, `top_n_species` |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `EBIRD_API_KEY` | Yes | eBird API key — https://ebird.org/api/keygen |
| `HUGGINGFACE_API_TOKEN` | Yes | HuggingFace token for Inference API access |
| `HF_MODEL_ID` | No | Short alias or full repo ID (default: `qwen2.5-72b`) |

---

## eBird API Reference

Full documentation: https://documenter.getpostman.com/view/664302/S1ENwy59  
Authentication: `x-ebirdapitoken` header, injected automatically by `src/utils/ebird_client.py`.
