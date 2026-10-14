# -*- coding: utf-8 -*-
"""
Requests Service: Parses ServerLog.dat files for license shortage/denial events.
"""
import os
import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional, Tuple
from services.config import LIC_LOG_DIR, CACHE_TTL_STATS, WORK_HOURS_SLOTS_15M, LUNCH_HOURS_SLOTS_15M
from services.cache import cache_store
from services.user_master import user_master_service
from services.log_parser import log_parser_service

class RequestsService:
    def __init__(self):
        self._day_raw_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._day_file_stats: Dict[str, Tuple[float, int]] = {}
        self._last_lic_log_dir: str = ""

    def _parse_single_day_log(self, d: str) -> Tuple[str, List[Dict[str, Any]]]:
        current_log_dir = LIC_LOG_DIR
        log_file = os.path.join(current_log_dir, f"{d}_ServerLog.dat")
        if not os.path.exists(log_file) or os.path.getsize(log_file) == 0:
            return d, []

        events = []
        try:
            with open(log_file, "r", encoding="utf-8-sig", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 4:
                        ip_val = parts[0]
                        date_raw = parts[1].replace("/", "").replace("-", "")
                        time_val = parts[2]
                        host_val = parts[3].upper()

                        t_parts = time_val.split(":")
                        sec = 0
                        if len(t_parts) >= 2:
                            try:
                                h_int = int(t_parts[0])
                                m_int = int(t_parts[1])
                                s_int = int(t_parts[2]) if len(t_parts) >= 3 else 0
                                slot_str = f"{h_int:02d}:{(m_int // 15) * 15:02d}"
                                if slot_str not in WORK_HOURS_SLOTS_15M:
                                    continue
                                sec = h_int * 3600 + m_int * 60 + s_int
                            except ValueError:
                                continue
                        else:
                            continue

                        events.append({
                            "date_raw": date_raw,
                            "time_val": time_val,
                            "sec": sec,
                            "host_val": host_val,
                            "ip_val": ip_val
                        })
        except Exception:
            return d, []
        return d, events

    def get_server_requests(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        single_date: Optional[str] = None
    ) -> Dict[str, Any]:
        user_master = user_master_service.get_user_master()
        dates_ymd = log_parser_service.resolve_date_range(start_date, end_date, single_date)
        
        # Requests history is strictly limited to past dates (up to yesterday / prior to today)
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

        # Check if LIC_LOG_DIR changed (e.g. during test suites or mock runs)
        current_log_dir = LIC_LOG_DIR
        if self._last_lic_log_dir != current_log_dir:
            self._day_raw_cache.clear()
            self._day_file_stats.clear()
            self._last_lic_log_dir = current_log_dir

        missing_dates = []
        for d in dates_ymd:
            log_file = os.path.join(current_log_dir, f"{d}_ServerLog.dat")
            sig = (0.0, 0)
            if os.path.exists(log_file):
                try:
                    st = os.stat(log_file)
                    sig = (st.st_mtime, st.st_size)
                except Exception:
                    pass
            if d not in self._day_raw_cache or self._day_file_stats.get(d) != sig:
                missing_dates.append(d)
                self._day_file_stats[d] = sig

        cache_key = f"server_requests_{dates_ymd[0]}_{dates_ymd[-1]}_{len(dates_ymd)}"
        if not missing_dates:
            cached = cache_store.get(cache_key)
            if cached:
                return cached

        # Parallel load missing or updated days into day-level cache
        if missing_dates:
            workers = min(16, len(missing_dates))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for d, evs in executor.map(self._parse_single_day_log, missing_dates):
                    self._day_raw_cache[d] = evs

        day_logs = log_parser_service.get_day_log_data(dates_ymd)
        host_ip_map: Dict[str, str] = {}
        for d in dates_ymd:
            d_hip = day_logs.get(d, {}).get("host_ip", {})
            for h_k, ip_v in d_hip.items():
                if h_k not in host_ip_map and ip_v != "-":
                    host_ip_map[h_k] = ip_v

        # Host user lookup memoization cache
        host_user_cache: Dict[str, Dict[str, str]] = {}

        raw_count = 0
        raw_events_by_date_host = {}

        for d in dates_ymd:
            ev_list = self._day_raw_cache.get(d, [])
            raw_count += len(ev_list)
            for ev in ev_list:
                key = (ev["date_raw"], ev["host_val"])
                if key not in raw_events_by_date_host:
                    raw_events_by_date_host[key] = []
                raw_events_by_date_host[key].append(ev)

        items = []
        dates_with_requests = set()
        dept_stats = {}
        user_stats = {}
        unique_users_set = set()

        for ev_list in raw_events_by_date_host.values():
            ev_list.sort(key=lambda x: x["sec"])
            cur_cluster = [ev_list[0]]
            for ev in ev_list[1:]:
                # Merge consecutive retries within 300 seconds (5 minutes) into the same shortage session
                if ev["sec"] - cur_cluster[-1]["sec"] <= 300:
                    cur_cluster.append(ev)
                else:
                    items.append(self._build_session_record(cur_cluster, user_master, host_user_cache, host_ip_map))
                    cur_cluster = [ev]
            if cur_cluster:
                items.append(self._build_session_record(cur_cluster, user_master, host_user_cache, host_ip_map))

        for it in items:
            dates_with_requests.add(it["date"])
            dept_stats[it["dept_name"]] = dept_stats.get(it["dept_name"], 0) + 1
            user_stats[it["user_name"]] = user_stats.get(it["user_name"], 0) + 1
            unique_users_set.add(it.get("user_key") or it["user_name"])

        items.sort(key=lambda x: (x["date_ymd"], x["raw_time"]))

        raw_items = []
        for ev_list in raw_events_by_date_host.values():
            for ev in ev_list:
                raw_items.append(self._build_session_record([ev], user_master, host_user_cache, host_ip_map))
        raw_items.sort(key=lambda x: (x["date_ymd"], x["raw_time"]))

        deduped_count = max(0, raw_count - len(items))
        dedup_rate = round((deduped_count / raw_count * 100), 1) if raw_count > 0 else 0.0

        # Build 15-minute slot mapping partitioned by (date_ymd, slot)
        # Prevents cross-date user accumulation distortions across multi-day date ranges
        day_slot_items: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
            d: {s: [] for s in WORK_HOURS_SLOTS_15M} for d in dates_ymd
        }
        for it in items:
            d_ymd = it.get("date_ymd")
            if d_ymd not in day_slot_items:
                continue
            raw_t = it.get("raw_time", "00:00:00")
            t_parts = raw_t.split(":")
            if len(t_parts) >= 2:
                try:
                    h = int(t_parts[0])
                    m = int(t_parts[1])
                    slot_m = (m // 15) * 15
                    slot_str = f"{h:02d}:{slot_m:02d}"
                    if slot_str in day_slot_items[d_ymd]:
                        day_slot_items[d_ymd][slot_str].append(it)
                except ValueError:
                    pass

        # Calculate exact simultaneous concurrent waiters per (date, slot)
        all_concurrent_waits: List[int] = []
        positive_waits: List[int] = []

        for d in dates_ymd:
            for s in WORK_HOURS_SLOTS_15M:
                slot_its = day_slot_items[d][s]
                # Unique users waiting simultaneously in this specific 15-minute window on this specific date
                u_set = set(it.get("user_key") or it.get("user_name") or it.get("hostname") for it in slot_its if (it.get("user_key") or it.get("user_name") or it.get("hostname")))
                w_count = len(u_set)
                all_concurrent_waits.append(w_count)
                if w_count > 0:
                    positive_waits.append(w_count)

        # True Peak Waiters: The absolute highest simultaneous waiting count observed in any single 15-minute slot
        peak_waiters = max(all_concurrent_waits, default=0)

        # True Average Waiters: The average number of simultaneous waiting users during shortage occurrences
        avg_waiters = round(sum(positive_waits) / len(positive_waits), 1) if positive_waits else 0.0

        # Build 15-minute timeseries for the chart
        timeseries_15m = []
        for s in WORK_HOURS_SLOTS_15M:
            denial_cnt = sum(len(day_slot_items[d][s]) for d in dates_ymd)
            raw_reqs = sum(sum(it.get("retry_count", 1) for it in day_slot_items[d][s]) for d in dates_ymd)

            daily_waiters_for_s = [
                len(set(it.get("user_key") or it.get("user_name") or it.get("hostname") for it in day_slot_items[d][s] if (it.get("user_key") or it.get("user_name") or it.get("hostname"))))
                for d in dates_ymd
            ]
            peak_u_in_slot = max(daily_waiters_for_s, default=0)
            active_daily_waiters = [w for w in daily_waiters_for_s if w > 0]
            avg_u_in_slot = round(sum(active_daily_waiters) / len(active_daily_waiters), 1) if active_daily_waiters else 0.0

            timeseries_15m.append({
                "time": s,
                "denial_count": denial_cnt,
                "unique_users": peak_u_in_slot,
                "avg_waiters": avg_u_in_slot,
                "raw_requests": raw_reqs
            })

        # 1. Denial slot rate (08:00~17:00 excluding lunch slots 12:15, 12:30, 12:45: 34 slots/day)
        non_lunch_work_slots = [s for s in WORK_HOURS_SLOTS_15M if s not in LUNCH_HOURS_SLOTS_15M]
        total_work_slots = len(dates_ymd) * len(non_lunch_work_slots)
        denial_slots_count = sum(
            1 for d in dates_ymd for s in non_lunch_work_slots if len(day_slot_items[d][s]) > 0
        )
        denial_slot_rate_pct = round((denial_slots_count / total_work_slots) * 100, 1) if total_work_slots > 0 else 0.0
        daily_avg_sessions = round(len(items) / len(dates_ymd), 1) if dates_ymd else 0.0

        # 2. Affected user rate relative to total active CATIA users in the same period
        active_users_set = set()
        for d in dates_ymd:
            h_usage = day_logs.get(d, {}).get("host_usage", {})
            for h_clean in h_usage.keys():
                if h_clean in host_user_cache:
                    m_info = host_user_cache[h_clean]
                else:
                    ip_v = host_ip_map.get(h_clean, "-")
                    m_info = user_master_service.get_user_info(h_clean, ip_v)
                    host_user_cache[h_clean] = m_info
                active_users_set.add(user_master_service.get_canonical_user_key(h_clean, m_info))

        total_active_users = len(active_users_set)
        affected_user_rate_pct = (
            round((len(unique_users_set) / total_active_users) * 100, 1)
            if total_active_users > 0 else 0.0
        )

        top_dept = sorted(dept_stats.items(), key=lambda x: x[1], reverse=True)[0][0] if dept_stats else "-"
        top_user = sorted(user_stats.items(), key=lambda x: x[1], reverse=True)[0][0] if user_stats else "-"
        top_dept_count = dept_stats.get(top_dept, 0)
        top_dept_pct = round(top_dept_count / len(items) * 100, 1) if (items and top_dept_count > 0) else 0.0
        date_label = f"{dates_iso[0]} ~ {dates_iso[-1]}" if is_multi_date else dates_iso[0]

        res = {
            "status": "success",
            "is_multi_date": is_multi_date,
            "target_date": date_label,
            "start_date": dates_iso[0],
            "end_date": dates_iso[-1],
            "total_requests": raw_count,
            "total_sessions": len(items),
            "daily_avg_sessions": daily_avg_sessions,
            "denial_slots_count": denial_slots_count,
            "total_work_slots": total_work_slots,
            "denial_slot_rate_pct": denial_slot_rate_pct,
            "deduped_count": deduped_count,
            "dedup_rate": dedup_rate,
            "unique_users_count": len(unique_users_set),
            "total_active_users": total_active_users,
            "affected_user_rate_pct": affected_user_rate_pct,
            "active_days_count": len(dates_with_requests),
            "peak_waiters": peak_waiters,
            "avg_waiters": avg_waiters,
            "top_dept": top_dept,
            "top_dept_count": top_dept_count,
            "top_dept_pct": top_dept_pct,
            "top_user": top_user,
            "dept_stats": dept_stats,
            "timeseries_15m": timeseries_15m,
            "notice": (
                f"※ {date_label} 기간 동안 DSLS License 풀 초과로 인한 Denial 이력 총 {raw_count}회 시도(단기 재시도 보정 유효 부족 {len(items)}건, 영향 사용자 {len(unique_users_set)}명)가 집계되었습니다."
                if items else
                f"※ {date_label} 기간 동안 License 부족으로 인한 Denial 이력이 없습니다. (License 정상 운영)"
            ),
            "items": items,
            "raw_items": raw_items
        }
        cache_store.set(cache_key, res, CACHE_TTL_STATS)
        return res

    def _build_session_record(
        self,
        cluster: List[Dict[str, Any]],
        user_master: Dict[str, Any],
        host_info_cache: Optional[Dict[str, Any]] = None,
        host_ip_map: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        first_ev = cluster[0]
        last_ev = cluster[-1]
        host_val = first_ev["host_val"]
        d_raw = first_ev["date_raw"]
        retry_count = len(cluster)

        # 0열의 공인/게이트웨이 IP 대신 장비명(HOSTNAME) 및 USER.dat 관측 사내 IP 기준으로 마스터에서 사용자 조회
        if host_info_cache is not None and host_val in host_info_cache:
            m_info = host_info_cache[host_val]
        else:
            ip_v = host_ip_map.get(host_val, "-") if host_ip_map else "-"
            m_info = user_master_service.get_user_info(host_val, ip_v)
            if host_info_cache is not None:
                host_info_cache[host_val] = m_info

        user_name = m_info.get("USER_NAME", "미등록")
        emp_no = m_info.get("EMP_NO", "-")
        dept_name = m_info.get("DEPT_NAME", "미등록")
        master_ip = m_info.get("IP", "-")
        final_ip = master_ip if master_ip and master_ip not in ["미등록", ""] else "-"
        user_key = user_master_service.get_canonical_user_key(host_val, m_info)

        t_first = first_ev["time_val"]
        t_last = last_ev["time_val"]
        if len(t_first.split(":")) == 4:
            t_first = ":".join(t_first.split(":")[:3])
        if len(t_last.split(":")) == 4:
            t_last = ":".join(t_last.split(":")[:3])

        if retry_count > 1 and t_first != t_last:
            time_display = f"{t_first} ~ {t_last}"
        else:
            time_display = t_first

        d_display = f"{d_raw[:4]}-{d_raw[4:6]}-{d_raw[6:]}" if len(d_raw) == 8 else d_raw

        return {
            "date": d_display,
            "date_ymd": d_raw,
            "request_time": time_display,
            "raw_time": first_ev["time_val"],
            "end_time": last_ev["time_val"],
            "retry_count": retry_count,
            "user_key": user_key,
            "user_name": user_name,
            "emp_no": emp_no,
            "dept_name": dept_name,
            "lic_name": "MD2",
            "hostname": host_val,
            "ip": final_ip,
            "status": "Denial (License 풀 초과)"
        }

# Singleton instance
requests_service = RequestsService()
