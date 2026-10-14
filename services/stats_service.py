# -*- coding: utf-8 -*-
"""
Stats Service: Computes 08:00 ~ 17:00 15-minute usage time series, daily peaks, and historical benchmark.
"""
from typing import Dict, Any, List, Optional
from services.config import (
    TOTAL_MD2_LIMIT,
    PEAK_WARNING_THRESHOLD,
    CRITICAL_THRESHOLD,
    WARNING_THRESHOLD,
    WORK_HOURS_SLOTS_15M,
    LUNCH_HOURS_SLOTS_15M,
    CACHE_TTL_STATS
)
from services.cache import cache_store
from services.user_master import user_master_service
from services.log_parser import log_parser_service

class StatsService:
    def get_multi_date_time_stats(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        single_date: Optional[str] = None
    ) -> Dict[str, Any]:
        dates_ymd = log_parser_service.resolve_date_range(start_date, end_date, single_date)
        dates_iso = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates_ymd]
        is_multi_date = len(dates_ymd) > 1

        import datetime
        today_ymd = datetime.datetime.now().strftime("%Y%m%d")
        includes_today = (today_ymd in dates_ymd)

        cache_key = f"time_stats_range_{dates_ymd[0]}_{dates_ymd[-1]}_{len(dates_ymd)}"
        if not includes_today:
            cached = cache_store.get(cache_key)
            if cached:
                return cached

        day_logs = log_parser_service.get_day_log_data(dates_ymd)

        date_time_hosts: Dict[str, Dict[str, set]] = {
            d: day_logs.get(d, {}).get("slot_hosts", {s: set() for s in WORK_HOURS_SLOTS_15M})
            for d in dates_ymd
        }
        host_ip_map: Dict[str, str] = {}
        dept_counts: Dict[str, int] = {}
        dept_users: Dict[str, Dict[str, Dict[str, Any]]] = {}
        period_users_map: Dict[str, Dict[str, Any]] = {}

        for d in dates_ymd:
            d_hip = day_logs.get(d, {}).get("host_ip", {})
            for h, ip in d_hip.items():
                if h not in host_ip_map and ip != "-":
                    host_ip_map[h] = ip

        # Compute accurate department usage and unique user counts without raw log line duplicate multiplication
        host_user_info_cache: Dict[tuple, Dict[str, Any]] = {}
        for d in dates_ymd:
            for s in WORK_HOURS_SLOTS_15M:
                for h in date_time_hosts[d][s]:
                    ip_val = host_ip_map.get(h, "-")
                    cache_k = (h, ip_val)
                    if cache_k not in host_user_info_cache:
                        host_user_info_cache[cache_k] = user_master_service.get_user_info(h, ip_val)
                    m_info = host_user_info_cache[cache_k]
                    dept_name = m_info.get("DEPT_NAME", "미등록")
                    u_name = m_info.get("USER_NAME", "미등록")
                    e_no = m_info.get("EMP_NO", "-")
                    user_key = user_master_service.get_canonical_user_key(h, m_info)

                    if user_key not in period_users_map:
                        period_users_map[user_key] = {
                            "user_name": u_name,
                            "emp_no": e_no,
                            "dept_name": dept_name,
                            "hostnames": {h},
                            "slot_count": 1
                        }
                    else:
                        u_rec = period_users_map[user_key]
                        u_rec["hostnames"].add(h)
                        u_rec["slot_count"] += 1
                        if u_rec["user_name"] in ["미등록", "-", ""] and u_name not in ["미등록", "-", ""]:
                            u_rec["user_name"] = u_name
                        if u_rec["emp_no"] in ["-", "미등록", ""] and e_no not in ["-", "미등록", ""]:
                            u_rec["emp_no"] = e_no
                        if u_rec["dept_name"] in ["미등록", "-", ""] and dept_name not in ["미등록", "-", ""]:
                            u_rec["dept_name"] = dept_name

        for user_key, u_rec in period_users_map.items():
            d_name = u_rec["dept_name"]
            dept_counts[d_name] = dept_counts.get(d_name, 0) + u_rec["slot_count"]
            if d_name not in dept_users:
                dept_users[d_name] = {}
            dept_users[d_name][user_key] = u_rec

        # Convert sets to exact usage counts (with USAGE.dat fallback for missing USER.dat slots)
        date_time_counts: Dict[str, Dict[str, int]] = {
            d: {
                s: max(len(date_time_hosts[d][s]), day_logs.get(d, {}).get("slot_counts", {}).get(s, 0))
                for s in WORK_HOURS_SLOTS_15M
            }
            for d in dates_ymd
        }

        # Check if today (latest date) is in the queried date range
        avail_dates = log_parser_service.get_available_dates()
        latest_date_iso = avail_dates[0] if avail_dates else ""
        latest_date_ymd = latest_date_iso.replace("-", "") if latest_date_iso else ""

        latest_measured_slot = "17:00"
        if latest_date_ymd in dates_ymd:
            # Import dynamically to prevent circular dependencies
            from services.realtime_service import realtime_service
            rt_data = realtime_service.get_realtime_license_status()
            snap_raw = rt_data.get("snapshot_time", "17:00").replace(":", "").strip()
            rounded_slot = log_parser_service.time_str_to_slot_15m(snap_raw, round_early_1m=True)
            exact_slot = log_parser_service.time_str_to_slot_15m(snap_raw, round_early_1m=False)
            if rounded_slot and date_time_counts[latest_date_ymd].get(rounded_slot, 0) > 0:
                latest_measured_slot = rounded_slot
            else:
                latest_measured_slot = exact_slot or (
                    "00:00" if snap_raw.isdigit() and int(snap_raw) < 800 else "17:00"
                )

        # Identify valid dates with active measurements (exclude zero-data days caused by server downtime from averages)
        valid_dates_for_avg = [
            d for d in dates_ymd
            if any(date_time_counts[d][s] > 0 for s in WORK_HOURS_SLOTS_15M if (d != latest_date_ymd or s <= latest_measured_slot))
        ]
        # Fallback to all dates if all queried dates are zero to prevent empty division
        target_avg_dates = valid_dates_for_avg if (is_multi_date and valid_dates_for_avg) else dates_ymd

        series_15m = []
        for slot in WORK_HOURS_SLOTS_15M:
            slot_usages_for_avg = []
            all_measured_for_slot = []
            slot_by_date = {}
            for d in target_avg_dates:
                # If today and slot is in the unmeasured future, exclude it from active usages
                if d == latest_date_ymd and slot > latest_measured_slot:
                    slot_by_date[d] = None
                else:
                    cnt = date_time_counts[d][slot]
                    slot_by_date[d] = cnt
                    all_measured_for_slot.append(cnt)
                    slot_usages_for_avg.append(cnt)

            avg_u = round(sum(slot_usages_for_avg) / len(slot_usages_for_avg), 1) if slot_usages_for_avg else None
            peak_u = max(all_measured_for_slot) if all_measured_for_slot else None
            min_u = min(slot_usages_for_avg) if slot_usages_for_avg else (min(all_measured_for_slot) if all_measured_for_slot else None)
            avail = max(0, TOTAL_MD2_LIMIT - int(avg_u)) if avg_u is not None else None
            rate = round((avg_u / TOTAL_MD2_LIMIT) * 100, 1) if avg_u is not None else None
            peak_rate = round((peak_u / TOTAL_MD2_LIMIT) * 100, 1) if peak_u is not None else None

            is_all_future = (len(all_measured_for_slot) == 0)

            series_15m.append({
                "time": slot,
                "usage": avg_u,
                "avg_usage": avg_u,
                "peak_usage": peak_u,
                "min_usage": min_u,
                "available": avail,
                "rate_pct": rate,
                "peak_rate_pct": peak_rate,
                "is_peak": False if is_all_future else ((peak_u or 0) >= PEAK_WARNING_THRESHOLD or (rate or 0) >= 96.0),
                "is_future": is_all_future,
                "by_date": slot_by_date
            })

        hourly_map: Dict[str, List[int]] = {}
        for slot_info in series_15m:
            hour_prefix = slot_info["time"].split(":")[0] + ":00"
            if hour_prefix not in hourly_map:
                hourly_map[hour_prefix] = []
            hourly_map[hour_prefix].extend([slot_info["by_date"][d] for d in target_avg_dates if slot_info["by_date"].get(d) is not None])

        hourly_summary = []
        for h_label in sorted(hourly_map.keys()):
            h_vals = hourly_map[h_label]
            h_avg = round(sum(h_vals) / len(h_vals), 1) if h_vals else None
            h_peak = max(h_vals) if h_vals else None
            h_min = min(h_vals) if h_vals else None
            hourly_summary.append({
                "hour": h_label,
                "avg_usage": h_avg,
                "peak_usage": h_peak,
                "min_usage": h_min,
                "rate_pct": round((h_avg / TOTAL_MD2_LIMIT) * 100, 1) if h_avg is not None else None,
                "is_future": len(h_vals) == 0
            })

        daily_summary = []
        for d in target_avg_dates:
            is_cur_today = (d == latest_date_ymd)
            d_slots = [date_time_counts[d][s] for s in WORK_HOURS_SLOTS_15M if (not is_cur_today or s <= latest_measured_slot)]
            d_avg = round(sum(d_slots) / len(d_slots), 1) if d_slots else 0
            d_peak = max(d_slots) if d_slots else 0
            d_peak_slot = next((s for s in WORK_HOURS_SLOTS_15M if date_time_counts[d][s] == d_peak and d_peak > 0 and (not is_cur_today or s <= latest_measured_slot)), "-")

            m_slots = [date_time_counts[d][s] for s in WORK_HOURS_SLOTS_15M if s <= "12:00" and (not is_cur_today or s <= latest_measured_slot)]
            a_slots = [date_time_counts[d][s] for s in WORK_HOURS_SLOTS_15M if s >= "13:00" and (not is_cur_today or s <= latest_measured_slot)]
            m_peak_val = max(m_slots) if m_slots else 0
            a_peak_val = max(a_slots) if a_slots else 0

            # 임계치(CRITICAL_THRESHOLD) 이상 슬롯 카운트 및 비율 계산 (12시~13시 사이 점심시간 12:15, 12:30, 12:45 슬롯은 분모, 분자 모두 제외)
            d_work_slots_for_50 = [
                date_time_counts[d][s]
                for s in WORK_HOURS_SLOTS_15M
                if s not in LUNCH_HOURS_SLOTS_15M and (not is_cur_today or s <= latest_measured_slot)
            ]
            over_50_slots = [v for v in d_work_slots_for_50 if v >= CRITICAL_THRESHOLD]
            count_over_50 = len(over_50_slots)
            total_slots = len(d_work_slots_for_50)
            rate_over_50 = round((count_over_50 / total_slots) * 100, 1) if total_slots > 0 else 0.0

            daily_summary.append({
                "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                "date_ymd": d,
                "avg_usage": d_avg,
                "peak_usage": d_peak,
                "peak_time": d_peak_slot,
                "morning_peak": m_peak_val,
                "afternoon_peak": a_peak_val,
                "avg_rate_pct": round((d_avg / TOTAL_MD2_LIMIT) * 100, 1),
                "peak_rate_pct": round((d_peak / TOTAL_MD2_LIMIT) * 100, 1),
                "slots_over_50_count": count_over_50,
                "total_slots": total_slots,
                "slots_over_50_pct": rate_over_50,
                "critical_threshold": CRITICAL_THRESHOLD,
                "warning_threshold": WARNING_THRESHOLD,
                "slots_over_threshold_count": count_over_50,
                "slots_over_threshold_pct": rate_over_50
            })

        morning_slots = [s for s in series_15m if s["time"] >= "08:00" and s["time"] <= "12:00" and s["peak_usage"] is not None]
        afternoon_slots = [s for s in series_15m if s["time"] >= "13:00" and s["time"] <= "17:00" and s["peak_usage"] is not None]

        m_peak_candidates = [s for s in morning_slots if (s["peak_usage"] or 0) > 0]
        m_peak = max(m_peak_candidates, key=lambda x: x["peak_usage"]) if m_peak_candidates else {"time": "-", "peak_usage": 0, "avg_usage": 0}

        a_peak_candidates = [s for s in afternoon_slots if (s["peak_usage"] or 0) > 0]
        a_peak = max(a_peak_candidates, key=lambda x: x["peak_usage"]) if a_peak_candidates else {"time": "-", "peak_usage": 0, "avg_usage": 0}

        # 정규 근무시간(08:00~17:00) 기준 평균 점유량 및 가동률 산출
        work_slots = [s for s in series_15m if "08:00" <= s["time"] <= "17:00" and s["avg_usage"] is not None]
        work_avgs = [s["avg_usage"] for s in work_slots]
        work_avg = round(sum(work_avgs) / len(work_avgs), 1) if work_avgs else 0
        work_rate = round((work_avg / TOTAL_MD2_LIMIT) * 100, 1) if work_avgs else 0

        valid_peaks = [s for s in series_15m if s["peak_usage"] is not None]
        abs_max_val = max([s["peak_usage"] for s in valid_peaks]) if valid_peaks else 0
        abs_max_slot = next((s for s in valid_peaks if s["peak_usage"] == abs_max_val and abs_max_val > 0), None)

        total_dept_logs = sum(dept_counts.values())
        dept_dist = []
        for dept_name, count in sorted(dept_counts.items(), key=lambda x: x[1], reverse=True):
            pct = round((count / total_dept_logs) * 100, 1) if total_dept_logs > 0 else 0
            u_map = dept_users.get(dept_name, {})
            users_list = sorted(
                [
                    {
                        "user_name": u_info["user_name"],
                        "emp_no": u_info["emp_no"],
                        "hostname": ", ".join(sorted(u_info["hostnames"])),
                        "total_hours": round(u_info["slot_count"] * 0.25, 2)
                    }
                    for u_info in u_map.values()
                ],
                key=lambda x: (-x["total_hours"], x["user_name"])
            )
            dept_dist.append({
                "dept_name": dept_name,
                "share_pct": pct,
                "count": count,
                "total_hours": round(count * 0.25, 2),
                "user_count": len(users_list),
                "users": users_list
            })

        period_over_50_count = sum(d["slots_over_50_count"] for d in daily_summary)
        period_total_slots = sum(d["total_slots"] for d in daily_summary)
        period_over_50_pct = round((period_over_50_count / period_total_slots) * 100, 1) if period_total_slots > 0 else 0.0

        days_over_60_pct_count = sum(1 for d in daily_summary if d["slots_over_50_pct"] >= 60.0)
        total_days_count = len(daily_summary)
        days_over_60_pct_rate = round((days_over_60_pct_count / total_days_count) * 100, 1) if total_days_count > 0 else 0.0

        unique_users_count = len(period_users_map)
        active_depts_count = len(dept_dist)

        active_dates_iso = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in target_avg_dates]
        date_label = f"{dates_iso[0]} ~ {dates_iso[-1]}" if is_multi_date else dates_iso[0]

        result = {
            "status": "success",
            "is_multi_date": is_multi_date,
            "target_date": date_label,
            "start_date": dates_iso[0],
            "end_date": dates_iso[-1],
            "selected_dates": active_dates_iso,
            "total_limit": TOTAL_MD2_LIMIT,
            "critical_threshold": CRITICAL_THRESHOLD,
            "warning_threshold": WARNING_THRESHOLD,
            "unique_users_count": unique_users_count,
            "active_depts_count": active_depts_count,
            "time_series_15m": series_15m,
            "hourly_summary": hourly_summary,
            "daily_summary": daily_summary,
            "dept_distribution": dept_dist,
            "peak_summary": {
                "period_max": {
                    "usage": abs_max_val,
                    "time": abs_max_slot["time"] if abs_max_slot else "-",
                    "rate_pct": round((abs_max_val / TOTAL_MD2_LIMIT) * 100, 1)
                },
                "morning_peak": m_peak,
                "afternoon_peak": a_peak,
                "work_hours_avg": work_avg,
                "work_hours_rate": work_rate,
                "period_over_50_count": period_over_50_count,
                "period_total_slots": period_total_slots,
                "period_over_50_pct": period_over_50_pct,
                "days_over_60_pct_count": days_over_60_pct_count,
                "total_days_count": total_days_count,
                "days_over_60_pct_rate": days_over_60_pct_rate,
                "unique_users_count": unique_users_count,
                "active_depts_count": active_depts_count,
                "top_dept": dept_dist[0] if dept_dist else None
            }
        }
        ttl = 5 if includes_today else CACHE_TTL_STATS
        cache_store.set(cache_key, result, ttl)
        return result

    def get_historical_average_time_stats(self) -> Dict[str, Any]:
        """
        Calculates average license usage per 15m slot across all historical log dates EXCLUDING today.
        Strictly counts unique MD2 license users per slot to prevent duplicate counts and exceeding limit 51.
        """
        cache_key = "historical_average_time_stats_excl_today_md2_unique"
        cached = cache_store.get(cache_key)
        if cached:
            return cached

        avail_dates = log_parser_service.get_available_dates()
        if not avail_dates:
            return {"status": "empty", "base_days_count": 0, "time_series_15m": []}

        # Exclude the latest (today) date
        past_dates_iso = avail_dates[1:] if len(avail_dates) > 1 else avail_dates
        past_ymd = [d.replace("-", "") for d in past_dates_iso]

        day_logs = log_parser_service.get_day_log_data(past_ymd)
        date_time_hosts: Dict[str, Dict[str, set]] = {
            d: day_logs.get(d, {}).get("slot_hosts", {s: set() for s in WORK_HOURS_SLOTS_15M})
            for d in past_ymd
        }
        date_time_counts: Dict[str, Dict[str, int]] = {
            d: {
                s: max(len(date_time_hosts[d][s]), day_logs.get(d, {}).get("slot_counts", {}).get(s, 0))
                for s in WORK_HOURS_SLOTS_15M
            }
            for d in past_ymd
        }

        # Filter out server downtime / zero occupancy dates from historical baseline
        valid_past_ymd = [
            d for d in past_ymd
            if any(date_time_counts[d][s] > 0 for s in WORK_HOURS_SLOTS_15M)
        ]
        target_past_ymd = valid_past_ymd if valid_past_ymd else past_ymd

        series_15m = []
        for slot in WORK_HOURS_SLOTS_15M:
            usages = [date_time_counts[d][slot] for d in target_past_ymd]
            avg_u = round(sum(usages) / len(usages), 1) if usages else 0
            peak_u = max(usages) if usages else 0
            rate = round((avg_u / TOTAL_MD2_LIMIT) * 100, 1)
            series_15m.append({
                "time": slot,
                "avg_usage": avg_u,
                "peak_usage": peak_u,
                "rate_pct": rate
            })

        res = {
            "status": "success",
            "base_days_count": len(target_past_ymd),
            "period": f"{past_dates_iso[-1]} ~ {past_dates_iso[0]}" if past_dates_iso else "",
            "time_series_15m": series_15m
        }
        cache_store.set(cache_key, res, CACHE_TTL_STATS)
        return res

# Singleton instance
stats_service = StatsService()
