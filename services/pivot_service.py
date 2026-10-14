# -*- coding: utf-8 -*-
"""
Pivot Service: Generates user-by-timeslot pivot matrices and departmental work hour summaries.
"""
from typing import Dict, Any, List, Optional
from services.config import WORK_HOURS_SLOTS_15M, CACHE_TTL_STATS
from services.cache import cache_store
from services.user_master import user_master_service
from services.log_parser import log_parser_service

class PivotService:
    def get_multi_date_user_pivot(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        single_date: Optional[str] = None,
        dept_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        dates_ymd = log_parser_service.resolve_date_range(start_date, end_date, single_date)
        dates_iso = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates_ymd]
        is_multi_date = len(dates_ymd) > 1

        import datetime
        today_ymd = datetime.datetime.now().strftime("%Y%m%d")
        includes_today = (today_ymd in dates_ymd)

        cache_key = f"user_pivot_range_{dates_ymd[0]}_{dates_ymd[-1]}_{len(dates_ymd)}_{dept_filter}"
        if not includes_today:
            cached = cache_store.get(cache_key)
            if cached:
                return cached

        day_logs = log_parser_service.get_day_log_data(dates_ymd)

        users_usage: Dict[str, Dict[str, Any]] = {}
        date_slot_usage: Dict[str, Dict[str, int]] = {d: {s: 0 for s in WORK_HOURS_SLOTS_15M} for d in dates_ymd}
        host_ip_map: Dict[str, str] = {}
        for d in dates_ymd:
            d_hip = day_logs.get(d, {}).get("host_ip", {})
            for h, ip in d_hip.items():
                if h not in host_ip_map and ip != "-":
                    host_ip_map[h] = ip

        # Dynamic memoization: cache master lookups per unique (host, ip) to eliminate duplicate lookups
        host_user_cache: Dict[tuple, Dict[str, str]] = {}

        for d_val in dates_ymd:
            d_data = day_logs.get(d_val, {})
            h_usage = d_data.get("host_usage", {})
            h_ip_map = d_data.get("host_ip", {})

            for hostname_clean, uinfo in h_usage.items():
                ip_lookup = host_ip_map.get(hostname_clean, "-")
                cache_k = (hostname_clean, ip_lookup)
                if cache_k not in host_user_cache:
                    host_user_cache[cache_k] = user_master_service.get_user_info(hostname_clean, ip_lookup)
                m_info = host_user_cache[cache_k]
                u_name = m_info.get("USER_NAME", "미등록")
                e_no = m_info.get("EMP_NO", "-")
                d_name = m_info.get("DEPT_NAME", "미등록")
                master_ip = m_info.get("IP", "-")
                log_ip = next(iter(uinfo["ips"])) if uinfo["ips"] else h_ip_map.get(hostname_clean, "-")
                final_ip = master_ip if master_ip and master_ip not in ["-", "미등록", ""] else (log_ip if log_ip != "-" else "-")

                if dept_filter and dept_filter != "ALL" and d_name != dept_filter:
                    continue

                user_key = user_master_service.get_canonical_user_key(hostname_clean, m_info)

                if user_key not in users_usage:
                    users_usage[user_key] = {
                        "hostnames": {hostname_clean},
                        "hostname": hostname_clean,
                        "primary_hostname": m_info.get("PRIMARY_HOSTNAME", hostname_clean),
                        "user_name": u_name,
                        "emp_no": e_no,
                        "dept_name": d_name,
                        "ips": set(uinfo["ips"]) if uinfo["ips"] else ({final_ip} if final_ip != "-" else set()),
                        "ip": final_ip,
                        "date_slots": {d: set() for d in dates_ymd},
                        "date_slot_counts": {d: 0 for d in dates_ymd},
                        "min_times": {},
                        "max_times": {}
                    }
                else:
                    u_entry = users_usage[user_key]
                    u_entry["hostnames"].add(hostname_clean)
                    if uinfo["ips"]:
                        u_entry["ips"].update(uinfo["ips"])
                    elif final_ip != "-":
                        u_entry["ips"].add(final_ip)
                    if u_entry["user_name"] in ["미등록", "-", hostname_clean] and u_name not in ["미등록", "-", ""]:
                        u_entry["user_name"] = u_name
                    if u_entry["emp_no"] in ["-", "미등록", ""] and e_no not in ["-", "미등록", ""]:
                        u_entry["emp_no"] = e_no
                    if u_entry["dept_name"] in ["미등록", "-", ""] and d_name not in ["미등록", "-", ""]:
                        u_entry["dept_name"] = d_name

                u_entry = users_usage[user_key]
                u_entry["date_slots"][d_val].update(uinfo["slots"])
                u_entry["date_slot_counts"][d_val] += len(uinfo["slots"])
                for s in uinfo["slots"]:
                    date_slot_usage[d_val][s] = date_slot_usage[d_val].get(s, 0) + 1

                if uinfo.get("min_time"):
                    u_entry["min_times"][d_val] = min(u_entry["min_times"].get(d_val, uinfo["min_time"]), uinfo["min_time"])
                if uinfo.get("max_time"):
                    u_entry["max_times"][d_val] = max(u_entry["max_times"].get(d_val, uinfo["max_time"]), uinfo["max_time"])

            if not dept_filter or dept_filter == "ALL":
                slot_counts_map = d_data.get("slot_counts", {})
                for s, s_cnt in slot_counts_map.items():
                    if s_cnt > date_slot_usage[d_val].get(s, 0):
                        date_slot_usage[d_val][s] = s_cnt

        # Build rows & details
        rows = []
        daily_details: Dict[str, List[Dict[str, Any]]] = {d: [] for d in dates_ymd}
        dept_user_count: Dict[str, int] = {}
        dept_hour_sum: Dict[str, float] = {}

        # Exclude server-issue zero dates when calculating per-day averages across multiple dates
        valid_active_dates = [d for d in dates_ymd if sum(date_slot_usage[d].values()) > 0]
        target_dates_ymd = valid_active_dates if (is_multi_date and valid_active_dates) else dates_ymd
        effective_days_count = len(target_dates_ymd)

        latest_date_key = target_dates_ymd[-1]

        for hinfo in users_usage.values():
            dept_name = hinfo["dept_name"]
            user_name = hinfo["user_name"]
            emp_no = hinfo["emp_no"]
            disp_host = ", ".join(sorted(hinfo.get("hostnames", {hinfo.get("hostname", "")})))
            disp_ip = ", ".join(sorted(hinfo.get("ips", set()))) if hinfo.get("ips") else hinfo.get("ip", "-")

            total_slots_count = 0
            active_days = 0
            hours_by_date: Dict[str, float] = {}

            for d_val in target_dates_ymd:
                day_slots = hinfo["date_slots"][d_val]
                day_slots_count = hinfo["date_slot_counts"][d_val]
                day_hours = round(day_slots_count * 0.25, 2)
                hours_by_date[d_val] = day_hours

                if day_slots_count > 0:
                    active_days += 1
                    total_slots_count += day_slots_count

                slot_map_day = {s: (1 if s in day_slots else 0) for s in WORK_HOURS_SLOTS_15M}
                if day_slots_count > 0 or not is_multi_date:
                    daily_details[d_val].append({
                        "USER_NAME": user_name,
                        "EMP_NO": emp_no,
                        "DEPT_NAME": dept_name,
                        "HOSTNAME": disp_host,
                        "IP": disp_ip,
                        "USED_SLOTS_COUNT": day_slots_count,
                        "HOURS_OCCUPIED": day_hours,
                        "TOTAL_HOURS": day_hours,
                        "OCCUPANCY_RATE": round((len(day_slots) / len(WORK_HOURS_SLOTS_15M)) * 100, 1),
                        "FIRST_SEEN": hinfo["min_times"].get(d_val, "-"),
                        "LAST_SEEN": hinfo["max_times"].get(d_val, "-"),
                        "SLOTS": slot_map_day
                    })

            total_hours_sum = round(total_slots_count * 0.25, 2)
            avg_hours_per_day = round(total_hours_sum / effective_days_count, 2)

            slot_map_latest = {s: (1 if s in hinfo["date_slots"][latest_date_key] else 0) for s in WORK_HOURS_SLOTS_15M}
            slot_frequency = {s: sum(1 for d in target_dates_ymd if s in hinfo["date_slots"][d]) for s in WORK_HOURS_SLOTS_15M}

            rows.append({
                "USER_NAME": user_name,
                "EMP_NO": emp_no,
                "DEPT_NAME": dept_name,
                "HOSTNAME": disp_host,
                "IP": disp_ip,
                "TOTAL_SLOTS_COUNT": total_slots_count,
                "TOTAL_HOURS": total_hours_sum,
                "ACTIVE_DAYS": active_days,
                "AVG_HOURS_DAY": avg_hours_per_day,
                "HOURS_BY_DATE": hours_by_date,
                "SLOT_FREQUENCY": slot_frequency,
                "FIRST_SEEN": hinfo["min_times"].get(latest_date_key, "-"),
                "LAST_SEEN": hinfo["max_times"].get(latest_date_key, "-"),
                "SLOTS": slot_map_latest
            })

            dept_user_count[dept_name] = dept_user_count.get(dept_name, 0) + 1
            dept_hour_sum[dept_name] = dept_hour_sum.get(dept_name, 0.0) + total_hours_sum

        rows.sort(key=lambda x: (
            1 if x["USER_NAME"] == "미등록" or x["DEPT_NAME"] == "미등록" else 0,
            -x["TOTAL_HOURS"],
            x["DEPT_NAME"],
            x["USER_NAME"]
        ))

        for d_val in target_dates_ymd:
            daily_details[d_val].sort(key=lambda x: (
                1 if x["USER_NAME"] == "미등록" or x["DEPT_NAME"] == "미등록" else 0,
                x["DEPT_NAME"],
                x["USER_NAME"]
            ))

        dept_summary = []
        for dname, ucount in sorted(dept_user_count.items(), key=lambda x: (1 if x[0] == "미등록" else 0, -dept_hour_sum.get(x[0], 0))):
            dept_summary.append({
                "dept_name": dname,
                "user_count": ucount,
                "total_hours": round(dept_hour_sum.get(dname, 0.0), 2),
                "avg_hours": round(dept_hour_sum.get(dname, 0.0) / ucount, 2) if ucount > 0 else 0
            })

        target_dates_iso = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in target_dates_ymd]
        date_label = f"{dates_iso[0]} ~ {dates_iso[-1]}" if is_multi_date else dates_iso[0]

        result = {
            "status": "success",
            "is_multi_date": is_multi_date,
            "target_date": date_label,
            "start_date": dates_iso[0],
            "end_date": dates_iso[-1],
            "selected_dates": target_dates_iso,
            "selected_dates_ymd": target_dates_ymd,
            "total_users": len(rows),
            "time_slots": WORK_HOURS_SLOTS_15M,
            "date_slot_usage": date_slot_usage,
            "rows": rows,
            "daily_details": daily_details,
            "dept_summary": dept_summary
        }
        ttl = 5 if includes_today else CACHE_TTL_STATS
        cache_store.set(cache_key, result, ttl)
        return result

# Singleton instance
pivot_service = PivotService()
