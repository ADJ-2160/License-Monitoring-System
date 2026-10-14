# -*- coding: utf-8 -*-
"""
CATIA MD2 License Monitoring & Analytics Dashboard Backend
FastAPI-based high-performance server providing real-time DSLS license status,
daily user-by-timeslot pivot matrices, and 08:00 ~ 17:00 time usage analytics.
Zero Hardcoding: Modular Service Architecture.
"""

import os
import datetime
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from services.config import (
    STATIC_DIR,
    TEMPLATES_DIR,
    TOTAL_MD2_LIMIT
)
from services.user_master import user_master_service
from services.log_parser import log_parser_service
from services.realtime_service import realtime_service
from services.pivot_service import pivot_service
from services.stats_service import stats_service
from services.requests_service import requests_service
from services.anomaly_service import anomaly_service
from services.simulation_service import simulation_service
from services.export_service import export_service

# ---------------------------------------------------------
# FastAPI Application Setup
# ---------------------------------------------------------
app = FastAPI(
    title="CATIA MD2 License Monitoring System",
    description="Dynamic DSLS License Monitoring & Analytics Dashboard",
    version="2.0.0"
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ---------------------------------------------------------
# REST API Endpoints
# ---------------------------------------------------------
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    return JSONResponse(status_code=404, content={"detail": "Favicon not found"})


@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    available_dates = log_parser_service.get_available_dates()
    default_date = available_dates[0] if available_dates else datetime.datetime.now().strftime("%Y-%m-%d")

    # 당일 기준 이전 (최대 전일까지 선택 가능)
    today_ymd = datetime.datetime.now().strftime("%Y%m%d")
    system_today_ymd = available_dates[0].replace("-", "") if available_dates else today_ymd
    effective_max = min(today_ymd, system_today_ymd) if (available_dates and len(available_dates) > 1) else today_ymd
    past_dates = [d for d in available_dates if d.replace("-", "") < effective_max]
    if past_dates:
        max_past_date = past_dates[0]
    elif len(available_dates) > 1:
        max_past_date = available_dates[1]
    else:
        yesterday_dt = datetime.datetime.now() - datetime.timedelta(days=1)
        max_past_date = yesterday_dt.strftime("%Y-%m-%d")

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "available_dates": available_dates,
            "default_date": default_date,
            "max_past_date": max_past_date,
            "total_limit": TOTAL_MD2_LIMIT
        }
    )


@app.get("/api/v1/license/departments")
async def get_departments():
    departments = user_master_service.get_departments()
    return JSONResponse(content={
        "status": "success",
        "departments": departments
    })


@app.get("/api/v1/license/realtime")
async def get_realtime_license():
    return JSONResponse(content=realtime_service.get_realtime_license_status())


@app.get("/api/v1/license/daily-user-pivot")
async def get_daily_user_pivot(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    date: Optional[str] = Query(None, description="Single target date (YYYY-MM-DD)"),
    dept_name: Optional[str] = Query("ALL", description="Department filter")
):
    data = pivot_service.get_multi_date_user_pivot(
        start_date=start_date,
        end_date=end_date,
        single_date=date,
        dept_filter=dept_name
    )
    return JSONResponse(content=data)


@app.get("/api/v1/license/stats")
async def get_time_series_stats(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    date: Optional[str] = Query(None, description="Single target date (YYYY-MM-DD)")
):
    data = stats_service.get_multi_date_time_stats(
        start_date=start_date,
        end_date=end_date,
        single_date=date
    )
    return JSONResponse(content=data)


@app.get("/api/v1/license/available-dates")
async def get_available_dates():
    dates = log_parser_service.get_available_dates()
    return JSONResponse(content={"status": "success", "dates": dates})


@app.get("/api/v1/license/requests")
async def get_server_requests(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    date: Optional[str] = Query(None, description="Target date (YYYY-MM-DD)")
):
    data = requests_service.get_server_requests(
        start_date=start_date,
        end_date=end_date,
        single_date=date
    )
    return JSONResponse(content=data)


@app.get("/api/v1/license/anomalies")
async def get_license_anomalies(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    date: Optional[str] = Query(None, description="Target date (YYYY-MM-DD)"),
    dept_name: Optional[str] = Query(None, description="Department filter"),
    search: Optional[str] = Query(None, description="Search keyword")
):
    data = anomaly_service.get_idle_anomalies(
        start_date=start_date,
        end_date=end_date,
        single_date=date,
        department=dept_name,
        search_keyword=search
    )
    return JSONResponse(content=data)


@app.get("/api/v1/license/simulation")
async def get_license_simulation(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    date: Optional[str] = Query(None, description="Target date (YYYY-MM-DD)"),
    add_seats: int = Query(1, description="Additional license seats to simulate (+0 to +50)"),
    base_limit: Optional[int] = Query(None, description="Base license limit (default: 51)")
):
    data = simulation_service.run_simulation(
        start_date=start_date,
        end_date=end_date,
        single_date=date,
        add_seats=add_seats,
        base_limit=base_limit
    )
    return JSONResponse(content=data)


@app.get("/api/v1/license/export")
async def export_license_data(
    format: str = Query("csv", description="csv or excel"),
    type: str = Query("pivot", description="pivot, stats, requests, realtime, or simulation"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    date: Optional[str] = Query(None, description="Single target date (YYYY-MM-DD)"),
    view_mode: Optional[str] = Query(None, description="Current subview (15m, daily, hourly, summary, slots, dept)"),
    slot_date: Optional[str] = Query(None, description="Selected date for slots matrix (YYYY-MM-DD)"),
    dept: Optional[str] = Query(None, description="Department filter"),
    search: Optional[str] = Query(None, description="User or host search filter"),
    add_seats: Optional[int] = Query(None, description="Simulated additional seats"),
    base_limit: Optional[int] = Query(None, description="Base license limit")
):
    return export_service.export_data(
        format_type=format.lower(),
        data_type=type.lower(),
        start_date=start_date,
        end_date=end_date,
        date=date,
        view_mode=view_mode,
        slot_date=slot_date,
        dept=dept,
        search=search,
        add_seats=add_seats,
        base_limit=base_limit
    )


# ---------------------------------------------------------
# Server Execution Entry Point
# ---------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", 8000)),
        reload=True
    )

