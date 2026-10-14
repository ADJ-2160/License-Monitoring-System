# -*- coding: utf-8 -*-
"""
Realtime Service: Monitors real-time snapshot (1-minute granularity), active users, module-level breakdown, and current KPI status.
"""
import os
import datetime
from typing import Dict, Any, List
from services.config import (
    TOTAL_MD2_LIMIT,
    PEAK_WARNING_THRESHOLD,
    CRITICAL_THRESHOLD,
    WARNING_THRESHOLD,
    WORK_HOURS_SLOTS_15M,
    CACHE_TTL_REALTIME,
    MODULE_CONFIG
)
from services.cache import cache_store
from services.user_master import user_master_service
from services.log_parser import log_parser_service

class RealtimeService:
    def get_realtime_license_status(self) -> Dict[str, Any]:
        cache_key = "realtime_license_status"
        cached = cache_store.get(cache_key)
        if cached:
            return cached

        today_year = datetime.datetime.now().year
        log_file_15m = log_parser_service.get_log_file_path(today_year)
        if not os.path.exists(log_file_15m) or os.path.getsize(log_file_15m) == 0:
            prev_15m = log_parser_service.get_log_file_path(today_year - 1)
            if os.path.exists(prev_15m) and os.path.getsize(prev_15m) > 0:
                log_file_15m = prev_15m

        log_file_1m = log_parser_service.get_1min_log_file_path(today_year)
        if not os.path.exists(log_file_1m) or os.path.getsize(log_file_1m) == 0:
            prev_1m = log_parser_service.get_1min_log_file_path(today_year - 1)
            if os.path.exists(prev_1m) and os.path.getsize(prev_1m) > 0:
                log_file_1m = prev_1m

        if not os.path.exists(log_file_15m) and not os.path.exists(log_file_1m):
            return self._empty_realtime_response()

        # 1. Parse 15m cumulative slot usage and first/last seen for today
        today_slot_hosts: Dict[str, set] = {s: set() for s in WORK_HOURS_SLOTS_15M}
        today_user_slots: Dict[str, set] = {}
        today_user_min_time: Dict[str, str] = {}

        if os.path.exists(log_file_15m):
            try:
                file_size_15m = os.path.getsize(log_file_15m)
                buffer_size = min(file_size_15m, 3 * 1024 * 1024)
                with open(log_file_15m, "rb") as f:
                    if file_size_15m > buffer_size:
                        f.seek(file_size_15m - buffer_size)
                        f.readline()
                    raw_bytes = f.read()

                content = raw_bytes.decode("utf-8-sig", errors="ignore")
                lines = content.splitlines()

                # Find latest date in 15m file
                latest_15m_date = ""
                for line in lines:
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 2 and parts[1].isdigit():
                        if parts[0] > latest_15m_date:
                            latest_15m_date = parts[0]

                for line in lines:
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3 and parts[0] == latest_15m_date and parts[1].isdigit():
                        t_str, comp_prod = parts[1], parts[2]
                        prod_val = parts[3].upper() if len(parts) >= 4 else "MD2"

                        if "MD2" not in prod_val:
                            continue

                        slot = log_parser_service.time_str_to_slot_15m(t_str)
                        if not slot:
                            continue

                        t_int = int(t_str)
                        fmt_time = f"{t_int // 100:02d}:{t_int % 100:02d}"

                        host_part = comp_prod.split("/", 1)[0].strip().upper() if "/" in comp_prod else comp_prod.strip().upper()

                        if host_part not in today_user_slots:
                            today_user_slots[host_part] = set()
                            today_user_min_time[host_part] = fmt_time
                        elif fmt_time < today_user_min_time[host_part]:
                            today_user_min_time[host_part] = fmt_time

                        today_slot_hosts[slot].add(host_part)
                        today_user_slots[host_part].add(slot)
            except Exception:
                pass

        # 2. Extract latest 1-Minute snapshot & session timelines (Priority: 1MIN log file, Fallback: 15m log file)
        latest_date_str = ""
        latest_time_val = -1
        # Map: product_code -> Dict[host_clean, ip_part]
        snap_module_users: Dict[str, Dict[str, str]] = {}
        # Map: product_code -> Dict[time_int, Set[host_clean]]
        module_time_hosts: Dict[str, Dict[int, set]] = {}
        # Map: product_code -> Dict[host_clean, min_time_int]
        today_user_first_seen_1m: Dict[str, Dict[str, int]] = {}

        # Map: product_code -> Dict[host_clean, Set[time_int]]
        module_user_minutes: Dict[str, Dict[str, set]] = {}

        target_snap_file = log_file_1m if os.path.exists(log_file_1m) and os.path.getsize(log_file_1m) > 0 else log_file_15m

        if target_snap_file and os.path.exists(target_snap_file):
            try:
                f_sz = os.path.getsize(target_snap_file)
                # Read up to 10MB to cover entire current business day in 1-min resolution
                buf_sz = min(f_sz, 10 * 1024 * 1024)
                with open(target_snap_file, "rb") as f:
                    if f_sz > buf_sz:
                        f.seek(f_sz - buf_sz)
                        f.readline()
                    raw_snap = f.read().decode("utf-8-sig", errors="ignore")

                snap_lines = raw_snap.splitlines()

                # Pass 1: Discover latest date & time in snap file
                for line in snap_lines:
                    line = line.strip()
                    if not line:
                        continue
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 2 and parts[1].isdigit():
                        d_str, t_int = parts[0], int(parts[1])
                        if d_str > latest_date_str:
                            latest_date_str = d_str
                            latest_time_val = t_int
                        elif d_str == latest_date_str and t_int > latest_time_val:
                            latest_time_val = t_int

                # Pass 2: Collect timeline, today's first seen, and 1-minute occupied duration for latest date
                for line in snap_lines:
                    line = line.strip()
                    if not line:
                        continue
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3 and parts[0] == latest_date_str and parts[1].isdigit():
                        t_int = int(parts[1])
                        comp_prod = parts[2]
                        prod = parts[3].upper().strip() if len(parts) >= 4 else "MD2"
                        if not prod or prod == "PRODUCT" or prod in ("HD2", "FTA"):
                            continue

                        if "/" in comp_prod:
                            host_part, ip_part = comp_prod.split("/", 1)
                        else:
                            host_part, ip_part = comp_prod, "-"
                        host_clean = host_part.strip().upper()

                        if prod not in module_time_hosts:
                            module_time_hosts[prod] = {}
                        if t_int not in module_time_hosts[prod]:
                            module_time_hosts[prod][t_int] = set()
                        module_time_hosts[prod][t_int].add(host_clean)

                        # Track 1-min day first seen
                        if prod not in today_user_first_seen_1m:
                            today_user_first_seen_1m[prod] = {}
                        if host_clean not in today_user_first_seen_1m[prod] or t_int < today_user_first_seen_1m[prod][host_clean]:
                            today_user_first_seen_1m[prod][host_clean] = t_int

                        # Track 1-min total occupied minutes
                        if prod not in module_user_minutes:
                            module_user_minutes[prod] = {}
                        if host_clean not in module_user_minutes[prod]:
                            module_user_minutes[prod][host_clean] = set()
                        module_user_minutes[prod][host_clean].add(t_int)

                        # Collect users at latest timestamp
                        if t_int == latest_time_val:
                            if prod not in snap_module_users:
                                snap_module_users[prod] = {}
                            snap_module_users[prod][host_clean] = ip_part.strip()
            except Exception:
                pass

        fmt_snap_time = f"{latest_time_val // 100:02d}:{latest_time_val % 100:02d}" if latest_time_val >= 0 else "-"
        fmt_snap_date = f"{latest_date_str[:4]}-{latest_date_str[4:6]}-{latest_date_str[6:]}" if len(latest_date_str) == 8 else (latest_date_str or datetime.datetime.now().strftime("%Y-%m-%d"))

        # 3. Build Module-level Breakdown with full user list for each module (Exclude HD2, FTA)
        modules_list: List[Dict[str, Any]] = []
        all_module_codes = [m for m in MODULE_CONFIG.keys() if m not in ("HD2", "FTA")]
        md2_users_list: List[Dict[str, Any]] = []
        is_1m = (target_snap_file == log_file_1m)
        max_allowed_gap = 5 if is_1m else 20
        host_user_cache: Dict[tuple, Dict[str, Any]] = {}

        for m_code in all_module_codes:
            cfg = MODULE_CONFIG.get(m_code, {
                "name": f"CATIA {m_code} License",
                "limit": max(5, len(snap_module_users.get(m_code, {}))),
                "warning_threshold": max(4, len(snap_module_users.get(m_code, {}))),
                "icon": "fa-solid fa-cube",
                "color": "#64748b"
            })

            m_snap_hosts = snap_module_users.get(m_code, {})
            m_users: List[Dict[str, Any]] = []
            timeline = module_time_hosts.get(m_code, {})
            distinct_times = sorted([t for t in timeline.keys() if t <= latest_time_val], reverse=True)

            for host_clean, ip_part in m_snap_hosts.items():
                cache_k = (host_clean, ip_part)
                if cache_k not in host_user_cache:
                    host_user_cache[cache_k] = user_master_service.get_user_info(host_clean, ip_part)
                m_info = host_user_cache[cache_k]
                user_name = m_info.get("USER_NAME", "미등록")
                emp_no = m_info.get("EMP_NO", "-")
                dept_name = m_info.get("DEPT_NAME", "미등록")
                master_ip = m_info.get("IP", "-")
                final_ip = master_ip if master_ip and master_ip not in ["-", "미등록", ""] else (ip_part if ip_part != "-" else "-")

                # Calculate cumulative occupied duration with 1-minute resolution
                slots_count = len(today_user_slots.get(host_clean, set()))
                user_min_set = module_user_minutes.get(m_code, {}).get(host_clean, set())
                if is_1m and user_min_set:
                    total_minutes = len(user_min_set)
                    hours_occ = round(total_minutes / 60.0, 2)
                else:
                    total_minutes = slots_count * 15
                    hours_occ = round(slots_count * 0.25, 2)

                hours_part = total_minutes // 60
                mins_part = total_minutes % 60
                time_str = f"{hours_part}h {mins_part:02d}m" if hours_part > 0 else f"{mins_part}m"

                # Trace continuous session start time (when connection was re-established)
                session_start_int = latest_time_val
                for idx in range(len(distinct_times) - 1):
                    curr_t = distinct_times[idx]
                    prev_t = distinct_times[idx + 1]
                    curr_min = (curr_t // 100) * 60 + (curr_t % 100)
                    prev_min = (prev_t // 100) * 60 + (prev_t % 100)
                    gap = curr_min - prev_min
                    if gap > max_allowed_gap or gap < 0:
                        break
                    if host_clean in timeline.get(prev_t, set()):
                        session_start_int = prev_t
                    else:
                        break

                fmt_session_start = f"{session_start_int // 100:02d}:{session_start_int % 100:02d}" if session_start_int >= 0 else fmt_snap_time
                first_seen_int = today_user_first_seen_1m.get(m_code, {}).get(host_clean, session_start_int)
                fmt_first_seen_day = f"{first_seen_int // 100:02d}:{first_seen_int % 100:02d}" if first_seen_int >= 0 else fmt_session_start

                u_obj = {
                    "USER_NAME": user_name,
                    "EMP_NO": emp_no,
                    "DEPT_NAME": dept_name,
                    "HOSTNAME": host_clean,
                    "IP": final_ip,
                    "PRODUCT": m_code,
                    "TIME": fmt_snap_time,
                    "FIRST_SEEN": fmt_session_start,
                    "SESSION_START": fmt_session_start,
                    "FIRST_SEEN_DAY": fmt_first_seen_day,
                    "USED_SLOTS_COUNT": slots_count if not is_1m else round(total_minutes / 15.0, 1),
                    "OCCUPIED_MINUTES": total_minutes,
                    "OCCUPIED_TIME_STR": time_str,
                    "OCCUPIED_HOURS": hours_occ,
                    "TOTAL_HOURS": hours_occ
                }
                m_users.append(u_obj)

            # Sort users by Department, Name
            m_users.sort(key=lambda x: (
                1 if x["USER_NAME"] == "미등록" or x["DEPT_NAME"] == "미등록" else 0,
                x["DEPT_NAME"],
                x["USER_NAME"]
            ))

            if m_code == "MD2":
                md2_users_list = m_users

            m_limit = cfg.get("limit", TOTAL_MD2_LIMIT)
            m_used = len(m_users)
            m_avail = max(0, m_limit - m_used)
            m_rate = round((m_used / m_limit) * 100, 1) if m_limit > 0 else 0.0

            m_crit = cfg.get("critical_threshold", CRITICAL_THRESHOLD)
            m_warn = cfg.get("warning_threshold", WARNING_THRESHOLD)

            if m_used >= m_limit:
                status_level = "CRITICAL"
                status_label = "포화 (만석 0석)"
                badge_class = "badge-danger"
            elif m_avail <= 2 or m_used >= m_warn:
                status_level = "WARNING"
                status_label = f"혼잡 (잔여 {m_avail}석)"
                badge_class = "badge-warning"
            else:
                status_level = "NORMAL"
                status_label = f"즉시 접속 가능 (잔여 {m_avail}석)"
                badge_class = "badge-success"

            modules_list.append({
                "module_code": m_code,
                "module_name": cfg.get("name", m_code),
                "icon": cfg.get("icon", "fa-solid fa-cube"),
                "color": cfg.get("color", "#38bdf8"),
                "limit": m_limit,
                "total": m_limit,
                "used": m_used,
                "available": m_avail,
                "rate_pct": m_rate,
                "critical_threshold": m_crit,
                "warning_threshold": m_warn,
                "status_level": status_level,
                "status_label": status_label,
                "badge_class": badge_class,
                "users": m_users
            })

        # 4. Global KPI (MD2 is primary core package)
        md2_limit = TOTAL_MD2_LIMIT
        md2_used = len(snap_module_users.get("MD2", {}))
        md2_avail = max(0, md2_limit - md2_used)
        md2_rate = round((md2_used / md2_limit) * 100, 1) if md2_limit > 0 else 0.0

        server_status = "정상" if md2_used < WARNING_THRESHOLD else ("피크 경고 (주의)" if md2_used < md2_limit else "포화 (한도 도달)")
        rounded_slot_str = log_parser_service.time_str_to_slot_15m(f"{latest_time_val:04d}", round_early_1m=True)
        exact_slot_str = log_parser_service.time_str_to_slot_15m(f"{latest_time_val:04d}", round_early_1m=False)
        if rounded_slot_str and len(today_slot_hosts.get(rounded_slot_str, set())) > 0:
            latest_slot_str = rounded_slot_str
        else:
            latest_slot_str = exact_slot_str or (
                "00:00" if 0 <= latest_time_val < 800 else "17:00"
            )

        # Build today_time_series for 15m historical chart
        today_time_series = []
        for slot in WORK_HOURS_SLOTS_15M:
            if slot <= latest_slot_str:
                u = len(today_slot_hosts.get(slot, set()))
                today_time_series.append({
                    "time": slot,
                    "usage": u,
                    "avg_usage": u,
                    "peak_usage": u,
                    "rate_pct": round((u / TOTAL_MD2_LIMIT) * 100, 1),
                    "is_future": False
                })
            else:
                today_time_series.append({
                    "time": slot,
                    "usage": None,
                    "avg_usage": None,
                    "peak_usage": None,
                    "rate_pct": None,
                    "is_future": True
                })

        from services.stats_service import stats_service
        hist_stats = stats_service.get_historical_average_time_stats()

        res = {
            "status": "success",
            "server_status": server_status,
            "target_date": fmt_snap_date,
            "snapshot_time": fmt_snap_time,
            "total": md2_limit,
            "used": md2_used,
            "available": md2_avail,
            "rate_pct": md2_rate,
            "critical_threshold": CRITICAL_THRESHOLD,
            "warning_threshold": WARNING_THRESHOLD,
            "updated_at": datetime.datetime.now().strftime("%H:%M:%S"),
            "modules": modules_list,
            "licenses": {
                m["module_code"]: {
                    "total": m["limit"],
                    "used": m["used"],
                    "available": m["available"],
                    "rate_pct": m["rate_pct"]
                }
                for m in modules_list
            },
            "today_time_series": today_time_series,
            "historical_time_series": hist_stats.get("time_series_15m", []),
            "historical_days_count": hist_stats.get("base_days_count", 0),
            "historical_period": hist_stats.get("period", ""),
            "active_users": md2_users_list
        }
        cache_store.set(cache_key, res, CACHE_TTL_REALTIME)
        return res

    def _empty_realtime_response(self) -> Dict[str, Any]:
        return {
            "status": "warning",
            "server_status": "경고 (데이터 없음)",
            "target_date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "snapshot_time": "-",
            "total": TOTAL_MD2_LIMIT,
            "used": 0,
            "available": TOTAL_MD2_LIMIT,
            "rate_pct": 0.0,
            "critical_threshold": CRITICAL_THRESHOLD,
            "warning_threshold": WARNING_THRESHOLD,
            "updated_at": datetime.datetime.now().strftime("%H:%M:%S"),
            "modules": [],
            "licenses": {
                "MD2": {"total": TOTAL_MD2_LIMIT, "used": 0, "available": TOTAL_MD2_LIMIT, "rate_pct": 0.0}
            },
            "active_users": []
        }

# Singleton instance
realtime_service = RealtimeService()
