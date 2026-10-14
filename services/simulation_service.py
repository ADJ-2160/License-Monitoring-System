# -*- coding: utf-8 -*-
"""
Simulation Service: What-If Capacity Planner for License Expansion Decision-Making.
Calculates potential unconstrained demand, denial resolution rates, peak alleviation,
and SLA-based optimal seat recommendation.
"""
import math
import datetime
from typing import Dict, Any, List, Optional

from services.config import (
    TOTAL_MD2_LIMIT,
    WORK_HOURS_SLOTS_15M,
    CACHE_TTL_STATS
)
from services.cache import cache_store
from services.log_parser import log_parser_service
from services.stats_service import stats_service
from services.requests_service import requests_service


def _percentile(data: List[float], p: float) -> float:
    """Calculates the p-th percentile of a list of numbers using linear interpolation."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_data[int(k)])
    d0 = sorted_data[int(f)] * (c - k)
    d1 = sorted_data[int(c)] * (k - f)
    return float(d0 + d1)


def _time_str_to_seconds(t_str: Optional[str]) -> Optional[int]:
    """Parses HH:MM:SS, HH:MM, or HHMM string to seconds from midnight."""
    if not t_str:
        return None
    try:
        parts = str(t_str).strip().split(":")
        if len(parts) >= 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 3600 + int(parts[1]) * 60
        clean = str(t_str).replace(":", "").strip()
        if len(clean) >= 4 and clean[:4].isdigit():
            return int(clean[:2]) * 3600 + int(clean[2:4]) * 60
    except Exception:
        return None
    return None


class SimulationService:
    def run_simulation(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        single_date: Optional[str] = None,
        add_seats: int = 1,
        base_limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Runs License capacity expansion simulation across requested date range with configurable base limit.
        Strictly based on completed past denial and usage logs (up to yesterday).
        """
        dates_ymd = log_parser_service.resolve_date_range(start_date, end_date, single_date)
        
        # Simulation is strictly based on completed past denial and usage logs (up to yesterday / prior to today)
        today_ymd = datetime.datetime.now().strftime("%Y%m%d")
        avail_dates = log_parser_service.get_available_dates()
        system_today_ymd = avail_dates[0].replace("-", "") if avail_dates else today_ymd
        effective_max = min(today_ymd, system_today_ymd) if (avail_dates and len(avail_dates) > 1) else today_ymd

        dates_ymd = [d for d in dates_ymd if d < effective_max]
        if not dates_ymd:
            past_avail = [d.replace("-", "") for d in avail_dates if d.replace("-", "") < effective_max]
            if past_avail:
                dates_ymd = [past_avail[0]]
            else:
                yesterday_dt = datetime.datetime.now() - datetime.timedelta(days=1)
                dates_ymd = [yesterday_dt.strftime("%Y%m%d")]

        dates_iso = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates_ymd]
        is_multi_date = len(dates_ymd) > 1
        date_label = f"{dates_iso[0]} ~ {dates_iso[-1]}" if is_multi_date else dates_iso[0]

        try:
            add_seats = max(0, min(50, int(add_seats)))
        except (ValueError, TypeError):
            add_seats = 1

        try:
            if base_limit is not None:
                base_limit = max(1, min(200, int(base_limit)))
            else:
                base_limit = TOTAL_MD2_LIMIT
        except (ValueError, TypeError):
            base_limit = TOTAL_MD2_LIMIT

        cache_key = f"simulation_v2_{dates_ymd[0]}_{dates_ymd[-1]}_{len(dates_ymd)}_{base_limit}_{add_seats}"
        cached = cache_store.get(cache_key)
        if cached:
            return cached

        time_stats = stats_service.get_multi_date_time_stats(start_date, end_date, single_date)
        req_stats = requests_service.get_server_requests(start_date, end_date, single_date)

        day_logs = log_parser_service.get_day_log_data(dates_ymd)

        # Precompute slot time ranges (in seconds from midnight) for 15m working hours slots
        slot_sec_ranges = []
        for s in WORK_HOURS_SLOTS_15M:
            s_h, s_m = map(int, s.split(":"))
            s_start_sec = s_h * 3600 + s_m * 60
            slot_sec_ranges.append((s, s_start_sec, s_start_sec + 900))

        # Map each valid denial session (item) to its primary occurrence 15-minute slot
        # to ensure strict 1-to-1 parity with RequestsService (e.g. exactly 54 sessions = 54 denial events)
        # without double-counting cross-slot retry boundaries.
        # Map each valid denial session (item) to its primary occurrence 15-minute slot.
        # Uses a list so that separate sessions from the same host in the same slot (e.g. >5 min apart)
        # are each preserved, ensuring strict 1-to-1 parity with RequestsService (54 sessions).
        denials_per_slot: Dict[str, Dict[str, List[Dict[str, Any]]]] = {d: {s: [] for s in WORK_HOURS_SLOTS_15M} for d in dates_ymd}
        for item in req_stats.get("items", []):
            d_ymd = item.get("date_ymd")
            if d_ymd not in denials_per_slot:
                continue

            host_val = (item.get("hostname") or item.get("user_name") or "").strip().upper()
            if not host_val:
                continue

            raw_t = item.get("raw_time") or item.get("request_time", "")
            start_sec = _time_str_to_seconds(raw_t)

            target_slot = None
            if start_sec is not None:
                for s, s_start, s_end in slot_sec_ranges:
                    if s_start <= start_sec < s_end:
                        target_slot = s
                        break

            if not target_slot:
                clean_t = (raw_t or "").replace(":", "")[:4]
                target_slot = log_parser_service.time_str_to_slot_15m(clean_t)

            if target_slot and target_slot in denials_per_slot[d_ymd]:
                denials_per_slot[d_ymd][target_slot].append(item)

        # 2. Extract actual measured concurrency and pure unconstrained demand per slot
        slot_data_map: Dict[str, Dict[str, int]] = {d: {} for d in dates_ymd}
        for s_info in time_stats.get("time_series_15m", []):
            slot = s_info["time"]
            by_date = s_info.get("by_date", {})
            for d in dates_ymd:
                cnt = by_date.get(d)
                if cnt is not None:
                    slot_data_map[d][slot] = cnt

        all_demands: List[int] = []
        all_actuals: List[int] = []
        total_raw_denials = req_stats.get("total_requests", 0)
        total_unique_denial_events = sum(len(denials_per_slot[d][s]) for d in dates_ymd for s in WORK_HOURS_SLOTS_15M)
        max_d_in_single_slot = max((len(denials_per_slot[d][s]) for d in dates_ymd for s in WORK_HOURS_SLOTS_15M), default=0)

        # 3. Build unconstrained potential demand matrix: Q(t) = |Occupied_hosts(t) ∪ Denied_hosts(t)|
        slot_demand_matrix: Dict[str, Dict[str, Dict[str, Any]]] = {d: {} for d in dates_ymd}
        for d in dates_ymd:
            d_day_log = day_logs.get(d, {})
            slot_hosts_map = d_day_log.get("slot_hosts", {})
            for s in WORK_HOURS_SLOTS_15M:
                active_hosts = slot_hosts_map.get(s, set())
                c_val = len(active_hosts) if active_hosts else slot_data_map[d].get(s, 0)
                slot_denials = denials_per_slot[d][s]
                denied_hosts = set((it.get("hostname") or it.get("user_name") or "").strip().upper() for it in slot_denials if (it.get("hostname") or it.get("user_name")))
                
                # Deduplicate: if a user was denied at 10:01 but acquired license at 10:05 in same slot, count once
                combined_demand_hosts = active_hosts.union(denied_hosts)
                q_val = len(combined_demand_hosts) if active_hosts else (c_val + len(denied_hosts))
                pure_denial_count = len(slot_denials)

                slot_demand_matrix[d][s] = {
                    "concurrency": c_val,
                    "denials": pure_denial_count,
                    "demand": q_val
                }
                all_demands.append(q_val)
                all_actuals.append(c_val)

        max_demand = max(all_demands) if all_demands else base_limit
        max_actual = max(all_actuals) if all_actuals else 0

        # Calculate average potential demand and concurrency across active working dates
        calc_dates = [d for d in dates_ymd if any(slot_demand_matrix[d][s]["demand"] > 0 for s in WORK_HOURS_SLOTS_15M)] or dates_ymd
        active_demands = [slot_demand_matrix[d][s]["demand"] for d in calc_dates for s in WORK_HOURS_SLOTS_15M]
        active_actuals = [slot_demand_matrix[d][s]["concurrency"] for d in calc_dates for s in WORK_HOURS_SLOTS_15M]
        avg_demand = round(sum(active_demands) / len(active_demands), 1) if active_demands else 0.0
        avg_actual = round(sum(active_actuals) / len(active_actuals), 1) if active_actuals else 0.0

        # Calculate excess demand beyond base_limit (평균 초과 수요)
        excess_all = [max(0, slot_demand_matrix[d][s]["demand"] - base_limit) for d in calc_dates for s in WORK_HOURS_SLOTS_15M]
        excess_pos = [e for e in excess_all if e > 0]
        avg_excess_when_short = round(sum(excess_pos) / len(excess_pos), 1) if excess_pos else 0.0
        avg_excess_overall = round(sum(excess_all) / len(excess_all), 2) if excess_all else 0.0
        excess_slots_count = len(excess_pos)

        # Percentile metrics (P95, P99)
        if all_demands:
            p99_demand = float(_percentile(all_demands, 99))
            p95_demand = float(_percentile(all_demands, 95))
        else:
            p99_demand = float(base_limit)
            p95_demand = float(base_limit)

        opt_seats_recommended = max(0, math.ceil(p99_demand) - base_limit)
        zero_denial_seats = max(0, max_d_in_single_slot)

        # 4. Scenario Evaluation Matrix (Dynamically sized up to max needed + buffer)
        max_scenario = min(50, max(10, zero_denial_seats + 2, opt_seats_recommended + 2, add_seats + 1))
        scenarios = []
        for s_add in range(0, max_scenario + 1):
            s_limit = base_limit + s_add
            s_peak_thresh = int(math.floor(0.95 * s_limit))

            # Remaining denials: denied users in slot that cannot be resolved by +s_add capacity
            s_rem_denials = sum(max(0, len(denials_per_slot[d][s]) - s_add) for d in dates_ymd for s in WORK_HOURS_SLOTS_15M)
            s_res_denials = total_unique_denial_events - s_rem_denials
            s_res_rate = round((s_res_denials / total_unique_denial_events * 100), 1) if total_unique_denial_events > 0 else 100.0

            s_peak_slots = 0
            for d in dates_ymd:
                for s in WORK_HOURS_SLOTS_15M:
                    q = slot_demand_matrix[d][s]["demand"]
                    sim_c = min(q, s_limit)
                    if sim_c >= s_peak_thresh:
                        s_peak_slots += 1

            s_peak_hours = round(s_peak_slots * 15 / 60, 1)
            sla_met = (s_limit >= p99_demand)

            # Professional Corporate Tags
            if s_add == 0:
                rec_tag = "현재 상태 유지 (No Expansion)" if opt_seats_recommended == 0 else "현재 기준 상태 (Baseline)"
                rec_class = "badge-neutral"
            elif s_add == opt_seats_recommended and s_add == zero_denial_seats and opt_seats_recommended > 0:
                rec_tag = "최적 권장 (100% 해소)"
                rec_class = "badge-green"
            elif s_add == opt_seats_recommended and opt_seats_recommended > 0:
                rec_tag = "최적 권장 (99% SLA Target)"
                rec_class = "badge-green"
            elif s_add == zero_denial_seats and zero_denial_seats > 0:
                rec_tag = "100% 완전 해소 (Zero Denial)"
                rec_class = "badge-purple"
            elif s_add < opt_seats_recommended:
                rec_tag = "부분 해소 (Partial Relief)"
                rec_class = "badge-yellow"
            else:
                rec_tag = "초과 증설 (Surplus Capacity)"
                rec_class = "badge-blue"

            scenarios.append({
                "add_seats": s_add,
                "total_limit": s_limit,
                "peak_threshold": s_peak_thresh,
                "remaining_denials": s_rem_denials,
                "resolved_denials": s_res_denials,
                "resolution_rate": s_res_rate,
                "peak_slots": s_peak_slots,
                "peak_hours": s_peak_hours,
                "sla_met": sla_met,
                "recommendation_tag": rec_tag,
                "recommendation_class": rec_class
            })

        # 5. Extract Current User-Selected Scenario
        curr_sim_limit = base_limit + add_seats
        curr_peak_thresh = int(math.floor(0.95 * curr_sim_limit))

        baseline_scenario = scenarios[0]
        curr_scenario = next((sc for sc in scenarios if sc["add_seats"] == add_seats), None)
        if not curr_scenario:
            c_rem = sum(max(0, len(denials_per_slot[d][s]) - add_seats) for d in dates_ymd for s in WORK_HOURS_SLOTS_15M)
            c_res = total_unique_denial_events - c_rem
            c_rate = round((c_res / total_unique_denial_events * 100), 1) if total_unique_denial_events > 0 else 100.0
            c_peak_slots = 0
            for d in dates_ymd:
                for s in WORK_HOURS_SLOTS_15M:
                    q = slot_demand_matrix[d][s]["demand"]
                    sim_c = min(q, curr_sim_limit)
                    if sim_c >= curr_peak_thresh:
                        c_peak_slots += 1
            c_peak_h = round(c_peak_slots * 15 / 60, 1)
            curr_scenario = {
                "add_seats": add_seats,
                "total_limit": curr_sim_limit,
                "peak_threshold": curr_peak_thresh,
                "remaining_denials": c_rem,
                "resolved_denials": c_res,
                "resolution_rate": c_rate,
                "peak_slots": c_peak_slots,
                "peak_hours": c_peak_h,
                "sla_met": curr_sim_limit >= p99_demand,
                "recommendation_tag": "사용자 지정 Scenario",
                "recommendation_class": "badge-blue"
            }

        peak_hours_reduced = max(0.0, round(baseline_scenario["peak_hours"] - curr_scenario["peak_hours"], 1))

        # 6. Build 15-Minute Detailed Timeseries for Charting
        chart_series_15m = []
        for s in WORK_HOURS_SLOTS_15M:
            s_actuals = [slot_demand_matrix[d][s]["concurrency"] for d in calc_dates]
            s_demands = [slot_demand_matrix[d][s]["demand"] for d in calc_dates]
            s_sims = [min(slot_demand_matrix[d][s]["demand"], curr_sim_limit) for d in calc_dates]
            s_denials = [slot_demand_matrix[d][s]["denials"] for d in dates_ymd]
            s_excesses = [max(0, slot_demand_matrix[d][s]["demand"] - base_limit) for d in calc_dates]

            avg_act = round(sum(s_actuals) / len(s_actuals), 1) if s_actuals else 0.0
            peak_act = max([slot_demand_matrix[d][s]["concurrency"] for d in dates_ymd], default=0)
            avg_dem = round(sum(s_demands) / len(s_demands), 1) if s_demands else 0.0
            peak_dem = max([slot_demand_matrix[d][s]["demand"] for d in dates_ymd], default=0)
            avg_sim = round(sum(s_sims) / len(s_sims), 1) if s_sims else 0.0
            peak_sim = max([min(slot_demand_matrix[d][s]["demand"], curr_sim_limit) for d in dates_ymd], default=0)
            tot_den = sum(s_denials)
            avg_exc = round(sum(s_excesses) / len(s_excesses), 1) if s_excesses else 0.0
            peak_exc = max([max(0, slot_demand_matrix[d][s]["demand"] - base_limit) for d in dates_ymd], default=0)

            chart_series_15m.append({
                "time": s,
                "avg_actual": avg_act,
                "peak_actual": peak_act,
                "avg_demand": avg_dem,
                "peak_demand": peak_dem,
                "avg_simulated": avg_sim,
                "peak_simulated": peak_sim,
                "total_denials": tot_den,
                "avg_excess": avg_exc,
                "peak_excess": peak_exc
            })

        over_limit_cnt = max(0, max_demand - base_limit)
        notice_msg = (
            f"※ {date_label} 기간 동안 관측된 최대 잠재 수요는 {max_demand}개(기준 보유량 {base_limit}개 대비 {over_limit_cnt}개 초과)입니다. "
            f"+{add_seats} Copy 증설 시 Denial 해소율은 {curr_scenario['resolution_rate']}%, 잔여 Denial은 {curr_scenario['remaining_denials']}건입니다."
        )

        res = {
            "status": "success",
            "is_multi_date": is_multi_date,
            "target_date": date_label,
            "start_date": dates_iso[0],
            "end_date": dates_iso[-1],
            "base_limit": base_limit,
            "add_seats": add_seats,
            "simulated_limit": curr_sim_limit,
            "peak_threshold": curr_peak_thresh,
            "total_denials_observed": total_unique_denial_events,
            "total_raw_requests": total_raw_denials,
            "max_actual_concurrency": max_actual,
            "max_potential_demand": max_demand,
            "avg_potential_demand": avg_demand,
            "avg_demand": avg_demand,
            "avg_excess_demand": avg_excess_when_short,
            "avg_excess_overall": avg_excess_overall,
            "excess_slots_count": excess_slots_count,
            "max_excess": over_limit_cnt,
            "avg_actual_concurrency": avg_actual,
            "p95_demand": round(p95_demand, 1),
            "p99_demand": round(p99_demand, 1),
            "optimal_seats_recommended": opt_seats_recommended,
            "zero_denial_seats": zero_denial_seats,
            "current_scenario": curr_scenario,
            "baseline_scenario": baseline_scenario,
            "peak_hours_reduced": peak_hours_reduced,
            "scenarios": scenarios,
            "chart_series_15m": chart_series_15m,
            "notice": notice_msg
        }

        cache_store.set(cache_key, res, CACHE_TTL_STATS)
        return res


simulation_service = SimulationService()