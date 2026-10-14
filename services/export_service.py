# -*- coding: utf-8 -*-
"""
Export Service: Generates streamed Excel/CSV files for Pivot, Time Stats, and Denial Requests.
Features:
- Guaranteed UTF-8 BOM (utf-8-sig) encoded CSV for Microsoft Excel Korean compatibility.
- Precise WYSIWYG layout matching current view (15m slot matrix, summary pivot, snapshot, time stats tabs).
- RFC 5987 standard Content-Disposition for uncorrupted Korean filenames.
"""
import io
import urllib.parse
from datetime import datetime
import pandas as pd
from typing import Optional, List, Dict, Any
from fastapi.responses import StreamingResponse
from services.config import WORK_HOURS_SLOTS_15M, TOTAL_MD2_LIMIT, PEAK_WARNING_THRESHOLD
from services.pivot_service import pivot_service
from services.stats_service import stats_service
from services.requests_service import requests_service
from services.realtime_service import realtime_service
from services.anomaly_service import anomaly_service
from services.simulation_service import simulation_service

class ExportService:
    def _create_csv_response(self, df: pd.DataFrame, filename: str) -> StreamingResponse:
        """Create a StreamingResponse with guaranteed UTF-8-SIG (BOM) encoded bytes."""
        csv_str = df.to_csv(index=False, encoding="utf-8-sig")
        csv_bytes = csv_str.encode("utf-8-sig")
        
        quoted_filename = urllib.parse.quote(filename)
        headers = {
            "Content-Disposition": f'attachment; filename="{quoted_filename}"; filename*=UTF-8\'\'{quoted_filename}',
            "Content-Type": "text/csv; charset=utf-8-sig",
            "Cache-Control": "no-cache, no-store, must-revalidate"
        }
        return StreamingResponse(io.BytesIO(csv_bytes), headers=headers, media_type="text/csv; charset=utf-8-sig")

    def _create_excel_response(self, sheets: Dict[str, pd.DataFrame], filename: str) -> StreamingResponse:
        """Create a StreamingResponse with Excel workbook bytes containing specified sheet DataFrames."""
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            for sheet_name, df_sheet in sheets.items():
                sanitized_name = sheet_name.replace("/", "-").replace("\\", "-")[:31]
                if not df_sheet.empty:
                    df_sheet.to_excel(writer, index=False, sheet_name=sanitized_name)
                else:
                    pd.DataFrame([{"안내": "데이터가 없습니다."}]).to_excel(writer, index=False, sheet_name=sanitized_name)
        output.seek(0)

        quoted_filename = urllib.parse.quote(filename)
        headers = {
            "Content-Disposition": f'attachment; filename="{quoted_filename}"; filename*=UTF-8\'\'{quoted_filename}',
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Cache-Control": "no-cache, no-store, must-revalidate"
        }
        return StreamingResponse(output, headers=headers, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def _filter_rows(self, rows: List[Dict[str, Any]], dept: Optional[str], search: Optional[str]) -> List[Dict[str, Any]]:
        """Filter rows by department and search keyword."""
        res = rows
        if dept and dept != "ALL" and dept != "전체 부서":
            res = [r for r in res if r.get("DEPT_NAME") == dept]
        if search:
            s_lower = search.strip().lower()
            res = [
                r for r in res if (
                    s_lower in str(r.get("USER_NAME", "")).lower() or
                    s_lower in str(r.get("EMP_NO", "")).lower() or
                    s_lower in str(r.get("HOSTNAME", "")).lower() or
                    s_lower in str(r.get("IP", "")).lower() or
                    s_lower in str(r.get("DEPT_NAME", "")).lower()
                )
            ]
        return res

    def export_data(
        self,
        format_type: str,
        data_type: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        date: Optional[str] = None,
        view_mode: Optional[str] = None,
        slot_date: Optional[str] = None,
        dept: Optional[str] = None,
        search: Optional[str] = None,
        add_seats: Optional[int] = None,
        base_limit: Optional[int] = None
    ) -> StreamingResponse:
        format_type = (format_type or "csv").lower()
        data_type = (data_type or "pivot").lower()

        # ----------------------------------------------------
        # 1. Pivot Module Export (Accordion 1/2)
        # ----------------------------------------------------
        if data_type == "pivot":
            pivot_data = pivot_service.get_multi_date_user_pivot(start_date=start_date, end_date=end_date, single_date=date)
            rows = pivot_data.get("rows", [])
            selected_dates = pivot_data.get("selected_dates", [])
            daily_details = pivot_data.get("daily_details", {})
            is_multi = pivot_data.get("is_multi_date", False)
            target_label = pivot_data.get("target_date", "data").replace(" ", "_").replace("~", "_to_")

            # 1-A. Snapshot Subview
            if view_mode == "snapshot":
                return self.export_data(format_type=format_type, data_type="realtime")

            # 1-B. 15-Minute Detailed Slot Matrix Subview
            elif view_mode == "slots":
                # Determine target date for 15m slot view
                active_slot_date = slot_date or (selected_dates[0] if selected_dates else None) or date or pivot_data.get("target_date")
                active_slot_ymd = active_slot_date.replace("-", "") if active_slot_date else ""

                def build_slot_df(target_d_ymd: str, d_label: str) -> pd.DataFrame:
                    d_rows = daily_details.get(target_d_ymd, []) if is_multi else rows
                    filtered_d_rows = self._filter_rows(d_rows, dept, search)
                    export_list = []
                    for idx, r in enumerate(filtered_d_rows):
                        hours_val = r.get("HOURS_OCCUPIED", r.get("TOTAL_HOURS", 0))
                        entry = {
                            "No": idx + 1,
                            "일자": d_label,
                            "부서명": r.get("DEPT_NAME", "-"),
                            "성명": r.get("USER_NAME", "-"),
                            "사번": r.get("EMP_NO", "-"),
                            "장비명": r.get("HOSTNAME", "-"),
                            "IP": r.get("IP", "-"),
                            "점유시간(h)": hours_val,
                        }
                        slots_map = r.get("SLOTS", {})
                        for slot in WORK_HOURS_SLOTS_15M:
                            entry[slot] = "MD2" if slots_map.get(slot) == 1 else ""
                        export_list.append(entry)
                    return pd.DataFrame(export_list)

                df_active_slot = build_slot_df(active_slot_ymd, active_slot_date)

                if format_type == "csv":
                    return self._create_csv_response(df_active_slot, f"CATIA_License_15m_Slot_Matrix_{active_slot_date}.csv")
                else:
                    sheets = {}
                    sheets[f"{active_slot_date} Slot Matrix"] = df_active_slot
                    # If multi-date, include other dates in separate sheets
                    if is_multi:
                        for d_iso in selected_dates:
                            if d_iso != active_slot_date:
                                d_ymd = d_iso.replace("-", "")
                                sheets[f"{d_iso} 슬롯"] = build_slot_df(d_ymd, d_iso)
                    return self._create_excel_response(sheets, f"CATIA_License_15m_Slot_Matrix_{target_label}.xlsx")

            # 1-C. Multi-Date / Single-Date Summary Pivot Subview (Default)
            else:
                filtered_rows = self._filter_rows(rows, dept, search)
                export_rows = []
                for idx, r in enumerate(filtered_rows):
                    tot_hours = r.get("TOTAL_HOURS", 0)
                    if is_multi:
                        entry = {
                            "No": idx + 1,
                            "부서명": r.get("DEPT_NAME", "-"),
                            "성명": r.get("USER_NAME", "-"),
                            "사번": r.get("EMP_NO", "-"),
                            "장비명": r.get("HOSTNAME", "-"),
                            "IP": r.get("IP", "-"),
                            "총점유시간(h)": tot_hours,
                            "점유일수": r.get("ACTIVE_DAYS", 0),
                            "일평균점유(h)": r.get("AVG_HOURS_DAY", 0),
                        }
                        for d_iso in selected_dates:
                            d_ymd = d_iso.replace("-", "")
                            entry[f"{d_iso}(h)"] = r.get("HOURS_BY_DATE", {}).get(d_ymd, 0)
                    else:
                        rate_pct = round((tot_hours / (len(WORK_HOURS_SLOTS_15M) * 0.25)) * 100, 1)
                        entry = {
                            "No": idx + 1,
                            "부서명": r.get("DEPT_NAME", "-"),
                            "성명": r.get("USER_NAME", "-"),
                            "사번": r.get("EMP_NO", "-"),
                            "장비명": r.get("HOSTNAME", "-"),
                            "IP": r.get("IP", "-"),
                            "당일점유시간(h)": tot_hours,
                            "최초접속": r.get("FIRST_SEEN", "-"),
                            "최종점유": r.get("LAST_SEEN", "-"),
                            "점유율(%)": rate_pct
                        }
                    export_rows.append(entry)

                df_pivot = pd.DataFrame(export_rows)

                # Dept Summary DataFrame
                df_dept = pd.DataFrame()
                if pivot_data.get("dept_summary"):
                    df_dept = pd.DataFrame(pivot_data["dept_summary"])
                    if not df_dept.empty:
                        df_dept.columns = ["부서명", "사용자수", "총점유시간(h)", "인당평균점유(h)"]

                if format_type == "csv":
                    if view_mode == "dept" and not df_dept.empty:
                        return self._create_csv_response(df_dept, f"CATIA_License_Dept_Summary_{target_label}.csv")
                    else:
                        return self._create_csv_response(df_pivot, f"CATIA_License_User_Usage_Pivot_{target_label}.csv")
                else:
                    sheets = {}
                    if view_mode == "dept" and not df_dept.empty:
                        sheets["부서별 요약"] = df_dept
                        sheets["사용자별 점유 매트릭스"] = df_pivot
                    else:
                        sheets["사용자별 점유 매트릭스"] = df_pivot
                        if not df_dept.empty:
                            sheets["부서별 요약"] = df_dept
                    return self._create_excel_response(sheets, f"CATIA_License_User_Usage_Pivot_{target_label}.xlsx")

        # ----------------------------------------------------
        # 2. Stats Module Export (Accordion 3)
        # ----------------------------------------------------
        elif data_type == "stats":
            stats_data = stats_service.get_multi_date_time_stats(start_date=start_date, end_date=end_date, single_date=date)
            series_15m = stats_data.get("time_series_15m", [])
            hourly_summary = stats_data.get("hourly_summary", [])
            daily_summary = stats_data.get("daily_summary", [])
            dept_dist = stats_data.get("dept_distribution", [])

            df_15m = pd.DataFrame([
                {
                    "시간대": s.get("time", "-"),
                    "평균점유수량": s.get("avg_usage", 0),
                    "최대(Peak)": s.get("peak_usage", 0),
                    "최소점유수량": s.get("min_usage", 0),
                    "총보유한도": TOTAL_MD2_LIMIT,
                    "평균가동률(%)": s.get("rate_pct", 0),
                    "Peak 가동률(%)": s.get("peak_rate_pct", 0),
                    "Peak Warning": f"경고({PEAK_WARNING_THRESHOLD}개 이상)" if s.get("is_peak") else "정상"
                } for s in series_15m if not s.get("is_future", False)
            ])

            df_daily = pd.DataFrame([
                {
                    "일자": d.get("date", "-"),
                    "08~17시 일평균": d.get("avg_usage", 0),
                    "일일 최대 Peak": d.get("peak_usage", 0),
                    "Peak 발생시각": d.get("peak_time", "-"),
                    "오전 Peak": d.get("morning_peak", 0),
                    "오후 Peak": d.get("afternoon_peak", 0),
                    "평균가동률(%)": d.get("avg_rate_pct", 0)
                } for d in daily_summary
            ])

            df_hourly = pd.DataFrame([
                {
                    "시간대": h.get("hour", "-"),
                    "평균점유수량": h.get("avg_usage", 0),
                    "최대 Peak": h.get("peak_usage", 0),
                    "최소점유수량": h.get("min_usage", 0),
                    "가동률(%)": h.get("rate_pct", 0)
                } for h in hourly_summary
            ])

            df_dept = pd.DataFrame([
                {
                    "순위": i + 1,
                    "부서명": d.get("dept_name", "-"),
                    "사용인원(명)": d.get("user_count", 0),
                    "누적점유시간(h)": d.get("total_hours", round(d.get("count", 0) * 0.25, 2)),
                    "점유비중(%)": d.get("share_pct", d.get("pct", 0))
                } for i, d in enumerate(dept_dist)
            ])

            # Build Daily Matrix DataFrame (matching '일자별 사용량' sheet)
            matrix_rows = []
            for d in daily_summary:
                d_ymd = d.get("date_ymd", d.get("date", "").replace("-", ""))
                row = {
                    "일자": d.get("date", "-")
                }
                for s in series_15m:
                    t = s.get("time")
                    by_d = s.get("by_date", {})
                    val = by_d.get(d_ymd)
                    row[t] = val if val is not None else "-"
                row["일일Peak"] = d.get("peak_usage", 0)
                row["일일평균"] = d.get("avg_usage", 0)
                row["오전Peak"] = d.get("morning_peak", 0)
                row["오후Peak"] = d.get("afternoon_peak", 0)
                row["50개이상슬롯수"] = d.get("slots_over_50_count", 0)
                row["50개이상비율(%)"] = d.get("slots_over_50_pct", 0.0)
                matrix_rows.append(row)
            df_matrix = pd.DataFrame(matrix_rows)

            target_label = stats_data.get("target_date", "data").replace(" ", "_").replace("~", "_to_")

            if format_type == "csv":
                if view_mode == "daily" and not df_daily.empty:
                    return self._create_csv_response(df_daily, f"CATIA_License_Daily_Stats_{target_label}.csv")
                elif view_mode == "matrix" and not df_matrix.empty:
                    return self._create_csv_response(df_matrix, f"CATIA_License_Daily_Matrix_{target_label}.csv")
                elif view_mode == "hourly" and not df_hourly.empty:
                    return self._create_csv_response(df_hourly, f"CATIA_License_Hourly_Stats_{target_label}.csv")
                elif view_mode == "dept" and not df_dept.empty:
                    return self._create_csv_response(df_dept, f"CATIA_License_Dept_Share_{target_label}.csv")
                else:
                    return self._create_csv_response(df_15m, f"CATIA_License_15m_Time_Stats_{target_label}.csv")
            else:
                sheets = {}
                if view_mode == "matrix" and not df_matrix.empty:
                    sheets["일자별 사용량"] = df_matrix
                    sheets["15분 단위 상세 시계열"] = df_15m
                    if not df_daily.empty:
                        sheets["일자별 통계 요약"] = df_daily
                    sheets["시간대별 집계"] = df_hourly
                elif view_mode == "daily" and not df_daily.empty:
                    sheets["일자별 통계 요약"] = df_daily
                    if not df_matrix.empty:
                        sheets["일자별 사용량"] = df_matrix
                    sheets["15분 단위 상세 시계열"] = df_15m
                    sheets["시간대별 집계"] = df_hourly
                elif view_mode == "hourly" and not df_hourly.empty:
                    sheets["시간대별 집계"] = df_hourly
                    if not df_matrix.empty:
                        sheets["일자별 사용량"] = df_matrix
                    sheets["15분 단위 상세 시계열"] = df_15m
                    if not df_daily.empty:
                        sheets["일자별 통계 요약"] = df_daily
                else:
                    sheets["15분 단위 상세 시계열"] = df_15m
                    if not df_matrix.empty:
                        sheets["일자별 사용량"] = df_matrix
                    if not df_daily.empty:
                        sheets["일자별 통계 요약"] = df_daily
                    sheets["시간대별 집계"] = df_hourly

                if not df_dept.empty:
                    sheets["부서별 점유 비중"] = df_dept

                return self._create_excel_response(sheets, f"CATIA_License_Stats_Summary_{target_label}.xlsx")

        # ----------------------------------------------------
        # 3. Requests Module Export (Accordion 4)
        # ----------------------------------------------------
        elif data_type == "requests":
            req_data = requests_service.get_server_requests(start_date=start_date, end_date=end_date, single_date=date)
            items = req_data.get("raw_items" if search == "raw" else "items", [])
            export_rows = [
                {
                    "No": i + 1,
                    "발생일자": it.get("date", "-"),
                    "요청시간대": it.get("request_time", "-"),
                    "연속재시도": it.get("retry_count", 1),
                    "소속부서": it.get("dept_name", "-"),
                    "성명": it.get("user_name", "-"),
                    "사번": it.get("emp_no", "-"),
                    "장비명": it.get("hostname", "-"),
                    "IP": it.get("ip", "-"),
                    "License": it.get("lic_name", "MD2"),
                    "처리상태": it.get("status", "Denial (License 풀 초과)")
                } for i, it in enumerate(items)
            ]
            df = pd.DataFrame(export_rows)
            target_label = req_data.get("target_date", "data").replace(" ", "_").replace("~", "_to_")

            if format_type == "csv":
                return self._create_csv_response(df, f"CATIA_License_Denial_Shortage_History_{target_label}.csv")
            else:
                return self._create_excel_response({"License 초과 요청 및 대기 현황": df}, f"CATIA_License_Denial_Shortage_History_{target_label}.xlsx")

        # ----------------------------------------------------
        # 4. Realtime Snapshot Export (Accordion 1 / 2 Snapshot)
        # ----------------------------------------------------
        elif data_type == "realtime":
            rt_data = realtime_service.get_realtime_license_status()
            users = rt_data.get("active_users", [])
            df = pd.DataFrame([
                {
                    "No": i + 1,
                    "Snapshot Time": u.get("TIME", "-"),
                    "License": u.get("PRODUCT", "MD2"),
                    "부서명": u.get("DEPT_NAME", "-"),
                    "성명": u.get("USER_NAME", "-"),
                    "사번": u.get("EMP_NO", "-"),
                    "장비명": u.get("HOSTNAME", "-"),
                    "IP": u.get("IP", "-"),
                    "당일점유시간(h)": u.get("OCCUPIED_HOURS", 0),
                    "당일누적가동": u.get("OCCUPIED_TIME_STR", f"{u.get('OCCUPIED_HOURS', 0)}h"),
                    "점유시작시각": u.get("SESSION_START", u.get("FIRST_SEEN", "-"))
                } for i, u in enumerate(users)
            ])
            target_label = rt_data.get("target_date", "today").replace("-", "")

            if format_type == "csv":
                return self._create_csv_response(df, f"CATIA_License_Realtime_Status_{target_label}.csv")
            else:
                return self._create_excel_response({"실시간 점유 목록": df}, f"CATIA_License_Realtime_Status_{target_label}.xlsx")

        # ----------------------------------------------------
        # 5. Realtime Anomaly Idle Traces Export (Accordion 2)
        # ----------------------------------------------------
        elif data_type in ("anomaly", "idle", "proctrace"):
            anomaly_data = anomaly_service.get_idle_anomalies(
                department=dept,
                search_keyword=search
            )
            items = anomaly_data.get("items", [])
            df = pd.DataFrame([
                {
                    "No": i + 1,
                    "발생일시": it.get("timestamp", "-"),
                    "소속부서": it.get("dept_name", "-"),
                    "사용자명": it.get("user_name", "-"),
                    "사번": it.get("emp_id", "-"),
                    "장비명": it.get("hostname", "-"),
                    "IP": it.get("ip", "-"),
                    "PID": it.get("pid", "-"),
                    "CPU": it.get("cpu", "-"),
                    "메모리": it.get("mem", "-"),
                    "탐지이벤트": it.get("event_code", "IDLE_DETECTED"),
                    "상태설명": it.get("description", "[유휴 진입]"),
                    "조치사항": it.get("action_recommend", "License 회수 권고")
                } for i, it in enumerate(items)
            ])
            now_str = datetime.now().strftime("%Y%m%d_%H%M%S")

            if format_type == "csv":
                return self._create_csv_response(df, f"CATIA_Realtime_Idle_Anomaly_{now_str}.csv")
            else:
                return self._create_excel_response({"실시간_이상점유_모니터링": df}, f"CATIA_Realtime_Idle_Anomaly_{now_str}.xlsx")

        # ----------------------------------------------------
        # 6. License Capacity Forecasting Export (Accordion 5)
        # ----------------------------------------------------
        elif data_type in ("simulation", "whatif", "capacity"):
            seat_val = add_seats if add_seats is not None else (int(search) if search and search.isdigit() else 1)
            sim_data = simulation_service.run_simulation(
                start_date=start_date,
                end_date=end_date,
                single_date=date,
                add_seats=seat_val,
                base_limit=base_limit
            )
            scenarios = sim_data.get("scenarios", [])
            df_scenarios = pd.DataFrame([
                {
                    "Scenario": f"+{sc.get('add_seats')} Copy 증설",
                    "증설 수량 (Copies)": sc.get("add_seats"),
                    "총 License 한도": sc.get("total_limit"),
                    "잔여 Denial 건수": sc.get("remaining_denials"),
                    "해소 Denial 건수": sc.get("resolved_denials"),
                    "Denial 해소율 (%)": f"{sc.get('resolution_rate')}%",
                    "99% SLA 충족": "충족" if sc.get("sla_met") else "미충족",
                    "의사결정 판정": sc.get("recommendation_tag")
                } for sc in scenarios
            ])

            series_15m = sim_data.get("chart_series_15m", [])
            df_series = pd.DataFrame([
                {
                    "Time Slot": it.get("time"),
                    "평균 실제 점유량": it.get("avg_actual"),
                    "최대 실제 점유량 (Peak)": it.get("peak_actual"),
                    "평균 잠재 수요량": it.get("avg_demand"),
                    "최대 잠재 수요량 (Peak Demand)": it.get("peak_demand"),
                    "평균 Simulation 점유량": it.get("avg_simulated"),
                    "최대 Simulation 점유량": it.get("peak_simulated"),
                    "총 Denial 발생 건수": it.get("total_denials")
                } for it in series_15m
            ])

            target_label = sim_data.get("target_date", "data").replace(" ", "_").replace("~", "_to_")

            if format_type == "csv":
                return self._create_csv_response(df_scenarios, f"CATIA_License_Capacity_Forecasting_{target_label}.csv")
            else:
                return self._create_excel_response({
                    "증설 시나리오 비교": df_scenarios,
                    "15분 시계열 잠재수요": df_series
                }, f"CATIA_License_Capacity_Forecasting_{target_label}.xlsx")

        else:
            return self._create_csv_response(pd.DataFrame([{"오류": "지원하지 않는 데이터 형식입니다."}]), "error.csv")

# Singleton instance
export_service = ExportService()
