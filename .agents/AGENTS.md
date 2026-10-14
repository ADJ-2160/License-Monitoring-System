# **CATIA DSLS License Monitoring & Analytics Dashboard - Developer Guidelines**

This document serves as the primary behavioral, architectural, and technical guideline for AI assistants and developers maintaining the CATIA DSLS (Dassault Systèmes License Server) License Monitoring & Analytics Dashboard.

---

## **1. Core Architectural & Technical Principles**
- **System Identity:** Production-grade DSLS CATIA license concurrency monitoring, usage statistics analytics, denial request tracking, and seat expansion simulation dashboard.
- **Python-First Backend (FastAPI):**
  - Use FastAPI with Uvicorn on Windows (PowerShell).
  - High performance, asynchronous endpoints, typed request/response contracts.
  - Do NOT use Streamlit, Node.js, React, Vue, or Next.js. Keep backend and frontend cleanly decoupled.
- **Vanilla Frontend (Modern HTML5 / CSS / Vanilla JS):**
  - Single-page application served via `templates/index.html`.
  - Frontend styling in `static/css/style.css` (Glassmorphism, responsive layout, Dark/Light modes).
  - Client logic in `static/js/main.js` (Event delegation, dynamic DOM rendering, Chart.js integration).
- **Strict Layer Separation:**
  - `app.py` is strictly a routing and presentation layer (endpoint definitions, query parameter validation, static file mounting).
  - All domain business logic, log parsing, caching, data transformation, and simulations **MUST reside in dedicated modules under `services/`**.

---

## **2. Workspace Architecture & Module Responsibilities**

```text
workspace_root/
├── .agents/
│   └── AGENTS.md             # Developer & Agent Technical Guidelines (This file)
├── conf/                     # Configuration and dynamic IP/hostname cache
│   ├── equipment_override.json
│   └── observed_ips_cache.json
├── services/                 # Modular Business Logic Layer (11 Core Services)
│   ├── anomaly_service.py    # Long-idle usage & abnormal concurrent seat occupancy detection
│   ├── cache.py              # In-memory TTL cache store (avoids repetitive disk I/O)
│   ├── config.py             # Global constants, paths, default limits (51 base / 60 max)
│   ├── export_service.py     # CSV and Excel (.xlsx) data export generation
│   ├── log_parser.py         # DSLS 2026_USER.dat parsing, time-slot binning, holiday filtering
│   ├── pivot_service.py      # User/Host 15-minute time-slot utilization heatmap matrix
│   ├── realtime_service.py   # 5-minute interval live license concurrency status
│   ├── requests_service.py   # ServerLog.dat denial request parsing and user master matching
│   ├── simulation_service.py # Seat addition scenarios, peak demand, and optimal seat recommendation
│   ├── stats_service.py      # 15-minute & Daily time-series aggregation, 50+ slot metrics
│   └── user_master.py        # HKT_DSLS_USER_LIST.csv employee mapping with IP/hostname fallback
├── static/                   # Web Static Assets
│   ├── css/
│   │   └── style.css         # Styling, theme variables, table matrices, modal styles
│   └── js/
│       └── main.js           # Client-side state, tab navigation, fetch APIs, Chart.js binding
├── templates/
│   └── index.html            # Main dashboard HTML template (tab panels, cards, modals)
├── app.py                    # FastAPI application entry point & API route definitions
├── verify_services.py        # 4-Phase Post-Edit Integrity & Runtime Verification Suite
├── requirements.txt          # Python dependencies (fastapi, uvicorn, jinja2, pandas, openpyxl, holidays)
└── GEMINI.md                 # Root behavioral & domain guideline
```

---

## **3. Domain Business Rules & Calculation Specifications**

1. **License Limits & Dynamic Relative Thresholds (Single Source of Truth):**
   - **Base Limit (`TOTAL_MD2_LIMIT`):** Defined in [`services/config.py`](file:///C:/Users/HANTA/Desktop/VSCode/License-Monitoring-System/services/config.py) (default 51 seats). Changing this single variable dynamically updates all thresholds, APIs, and frontend displays.
   - **Master Limit:** 60 seats (Configured capacity cap).
   - **Critical / Over-capacity Threshold:** `CRITICAL_THRESHOLD = TOTAL_MD2_LIMIT - CRITICAL_BUFFER` (`CRITICAL_BUFFER = 1`, default >= 50 seats).
   - **Warning Threshold:** `WARNING_THRESHOLD = TOTAL_MD2_LIMIT - WARNING_BUFFER` (`WARNING_BUFFER = 6`, default >= 45 seats).
   - **Dynamic Color & Status Binding:** Realtime API, header occupancy badges, module cards, sparklines, daily matrix heatmap cells, and table legends dynamically adapt to these relative thresholds.
2. **Time Slots & Lunch Hour Exclusion Rule (Mandatory):**
   - **Business Day Range:** 08:30 ~ 17:45 (15-minute bins, 45 total daily slots).
   - **Lunch Hours:** 12:00 ~ 13:00 (3 slots: `12:15`, `12:30`, `12:45`).
   - **Calculation Rule:** In [`services/stats_service.py`](file:///C:/Users/HANTA/Desktop/VSCode/License-Monitoring-System/services/stats_service.py), all statistical denominator calculations (e.g., `slots_over_50_pct = slots_over_50_count / total_slots * 100`) **MUST exclude the 3 lunch slots**.
   - **Standard Daily Slot Count:** Exactly **42 slots** (45 total - 3 lunch slots).
   - **Peak Segregation:** Peak usage is segmented into `morning_peak` (08:30~12:00), `lunch_peak` (12:00~13:00), and `afternoon_peak` (13:00~17:45).
3. **Log Parsing & Holiday Filtering:**
   - Statutory Korean holidays and weekends are dynamically excluded using `holidays.KR(years=range(2020, 2036))` in [`services/log_parser.py`](file:///C:/Users/HANTA/Desktop/VSCode/License-Monitoring-System/services/log_parser.py).
   - Logs older than 18:00 or before 08:00 are classified as off-hours.
4. **User Master & IP Fallback:**
   - User Master CSV (`HKT_DSLS_USER_LIST.csv`) maps IP and Hostname to 사원명, 사번, 직급, 부서명.
   - Unregistered IPs/Hosts are stored in `conf/observed_ips_cache.json` and resolved as `미등록_호스트` or `IP 기반 가상사용자`.

---

## **4. Runtime & Development Commands (Windows PowerShell)**

- **Start Local Server:**
  ```powershell
  uvicorn app:app --reload --host 127.0.0.1 --port 8000
  ```
- **Execute 4-Phase Integrity Verification:**
  ```powershell
  python verify_services.py
  ```

---

## **5. Mandatory Post-Edit Verification & Anti-Regression Rules (Side-Effect Prevention)**

To prevent cascading regressions, undefined variable bugs (`NameError`), broken API contracts, or dead code accumulation:

1. **Mandatory 4-Phase Health Check:**
   After modifying any Python file (`app.py`, `services/*.py`) or frontend files (`main.js`, `index.html`), the assistant **MUST** run:
   ```powershell
   python verify_services.py
   ```
   All 4 phases must return `[PASS]` before reporting completion:
   - **Phase 1 (Zero-State Fallback):** Baseline integrity across all 5 core dashboard services with empty/zero inputs.
   - **Phase 2 (Golden Data Scenario):** Live-data simulation verifying lunch exclusion (42 slots, 4.8%), peak segregation, pivot matrix, requests join, and seat simulation.
   - **Phase 3 (DOM & Contract Integrity):** 100% match between JS DOM lookups (`getElementById`, `querySelector`) and HTML element IDs (0 missing), plus verification of required statistical keys.
   - **Phase 4 (Dead Code & Resource Audit):** AST-driven audit ensuring 0 unused imports (with safe auto-cleaning), 0 unreachable blocks post-termination statements, 0 unused local variables, and 100% `requirements.txt` package synchronization.
2. **No Partial Variable Loss:**
   When modifying calculation blocks in any service module, ensure all variables referenced in return dictionaries and downstream consumers remain properly defined.
3. **Frontend-Backend Contract Preservation:**
   Do not alter existing API response keys or data structures without simultaneously updating all consuming frontend handlers in `static/js/main.js`.
4. **Boundary & Duplicate Code Verification (Anti-Dead-Code):**
   Immediately after any partial file edit (`replace_file_content`), inspect the context (±5~10 lines) around the replacement boundaries (`view_file`). Ensure that escape statements (`return`, `raise`, `break`, `continue`) do not duplicate or trap code into unreachable blocks.
5. **Zero Dead Code & Resource Sync:**
   Never leave unreachable statements, unused imports, unused local variables, or unreferenced packages in `requirements.txt`. Clean up hidden or removed UI elements in templates to prevent ghost listeners.
