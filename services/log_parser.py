# -*- coding: utf-8 -*-
"""
Log Parser Service: Handles DSLS log paths, available date discovery, time-slot parsing,
and automated Korean legal/substitute holiday and weekend filtering (Business Days Only).
"""
import os
import time
import datetime
from typing import List, Optional, Union

try:
    import holidays
    HAS_HOLIDAYS = True
except ImportError:
    HAS_HOLIDAYS = False

from services.config import LIC_LOG_DIR, LIC_LOG_1MIN_DIR

class LogParserService:
    def __init__(self):
        self._available_dates_cache: List[str] = []
        self._available_dates_loaded_at: float = 0.0
        self._available_dates_sig: tuple = ()
        # Dynamic Korean Legal & Substitute Holidays (auto-expands for any future year)
        if HAS_HOLIDAYS:
            cur_y = datetime.datetime.now().year
            self._kr_holidays = holidays.KR(years=range(2020, max(2036, cur_y + 5)))
        else:
            self._kr_holidays = set()
        # Daily Parsed Log Cache: date_ymd -> Dict containing slot_hosts, host_ip, and host_usage
        self._day_cache: dict = {}
        self._user_file_stats: dict = {}

    def get_day_log_data(self, target_dates_ymd: List[str]) -> dict:
        """
        Loads and caches parsed log data per business day.
        Returns a dict mapping date_ymd to:
          {
            "slot_hosts": {slot: set(hostnames)},
            "host_ip": {hostname: ip},
            "host_usage": {hostname: {"ips": set, "slots": set, "min_time": str, "max_time": str}}
          }
        Avoids redundant disk I/O and full file scans across all services.
        """
        from services.config import WORK_HOURS_SLOTS_15M
        
        current_log_dir = LIC_LOG_DIR
        if getattr(self, "_last_lic_log_dir", "") != current_log_dir:
            self._day_cache.clear()
            self._user_file_stats.clear()
            self._last_lic_log_dir = current_log_dir

        today_ymd = datetime.datetime.now().strftime("%Y%m%d")
        missing_dates = set()

        target_years = sorted(list(set(d[:4] for d in target_dates_ymd if len(d) >= 4 and d[:4].isdigit())))
        changed_years = set()
        for y in target_years:
            log_file = self.get_log_file_path(int(y))
            if os.path.exists(log_file):
                try:
                    st = os.stat(log_file)
                    sig = (st.st_mtime, st.st_size)
                    if self._user_file_stats.get(y) != sig:
                        changed_years.add(y)
                        self._user_file_stats[y] = sig
                except Exception:
                    changed_years.add(y)

        for d in target_dates_ymd:
            if d not in self._day_cache or d == today_ymd or d[:4] in changed_years:
                missing_dates.add(d)

        if missing_dates:
            years = sorted(list(set(d[:4] for d in missing_dates)))
            for y in years:
                log_file = self.get_log_file_path(int(y) if str(y).isdigit() else None)
                if not os.path.exists(log_file):
                    continue

                init_dates = set()
                try:
                    with open(log_file, "r", encoding="utf-8-sig", errors="ignore") as f:
                        f.readline()
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            parts = [p.strip() for p in line.split(",")]
                            if len(parts) < 3:
                                continue

                            d_val, t_val, comp_prod = parts[0], parts[1], parts[2]
                            prod_val = parts[3].upper() if len(parts) >= 4 else "MD2"
                            if "MD2" not in prod_val:
                                continue

                            slot = self.time_str_to_slot_15m(t_val)
                            if not slot:
                                continue

                            if "/" in comp_prod:
                                host_clean, ip_part = comp_prod.split("/", 1)
                            else:
                                host_clean, ip_part = comp_prod, "-"

                            host_clean = host_clean.strip().upper()
                            ip_part = ip_part.strip()
                            if not host_clean:
                                continue

                            if d_val not in init_dates:
                                self._day_cache[d_val] = {
                                    "slot_hosts": {s: set() for s in WORK_HOURS_SLOTS_15M},
                                    "slot_counts": {s: 0 for s in WORK_HOURS_SLOTS_15M},
                                    "host_ip": {},
                                    "host_usage": {}
                                }
                                init_dates.add(d_val)

                            day_entry = self._day_cache[d_val]
                            day_entry["slot_hosts"][slot].add(host_clean)
                            if host_clean not in day_entry["host_ip"] and ip_part != "-":
                                day_entry["host_ip"][host_clean] = ip_part

                            # Host usage detail for pivot
                            hu = day_entry["host_usage"]
                            if host_clean not in hu:
                                hu[host_clean] = {
                                    "ips": {ip_part} if ip_part != "-" else set(),
                                    "slots": {slot},
                                    "min_time": None,
                                    "max_time": None
                                }
                            else:
                                if ip_part != "-":
                                    hu[host_clean]["ips"].add(ip_part)
                                hu[host_clean]["slots"].add(slot)

                            # Track first/last seen
                            raw_t_int = int(t_val) if t_val.isdigit() else 0
                            fmt_time = f"{raw_t_int // 100:02d}:{raw_t_int % 100:02d}"
                            h_rec = hu[host_clean]
                            if h_rec["min_time"] is None or fmt_time < h_rec["min_time"]:
                                h_rec["min_time"] = fmt_time
                            if h_rec["max_time"] is None or fmt_time > h_rec["max_time"]:
                                h_rec["max_time"] = fmt_time

                    for d_val in init_dates:
                        de = self._day_cache[d_val]
                        for s in WORK_HOURS_SLOTS_15M:
                            de["slot_counts"][s] = len(de["slot_hosts"][s])

                    usage_file = self.get_usage_file_path(int(y) if str(y).isdigit() else None)
                    if os.path.exists(usage_file):
                        with open(usage_file, "r", encoding="utf-8-sig", errors="ignore") as uf:
                            for uline in uf:
                                uline = uline.strip()
                                if not uline:
                                    continue
                                uparts = [p.strip() for p in uline.split(",")]
                                if len(uparts) >= 3 and len(uparts[0]) == 8 and uparts[0].isdigit() and uparts[2].isdigit():
                                    ud_val, ut_val, u_cnt = uparts[0], uparts[1], int(uparts[2])
                                    uslot = self.time_str_to_slot_15m(ut_val)
                                    if uslot and ud_val in self._day_cache:
                                        de = self._day_cache[ud_val]
                                        if len(de["slot_hosts"][uslot]) == 0 and u_cnt > 0:
                                            de["slot_counts"][uslot] = u_cnt

                except Exception:
                    continue

        for d in target_dates_ymd:
            if d not in self._day_cache:
                self._day_cache[d] = {
                    "slot_hosts": {s: set() for s in WORK_HOURS_SLOTS_15M},
                    "slot_counts": {s: 0 for s in WORK_HOURS_SLOTS_15M},
                    "host_ip": {},
                    "host_usage": {}
                }

        return {d: self._day_cache[d] for d in target_dates_ymd if d in self._day_cache}

    def is_business_day(self, date_val: Union[str, datetime.date, datetime.datetime]) -> bool:
        """
        Determines whether a given date is a normal business day:
        - Excludes Weekends (Saturday, Sunday)
        - Excludes Korean Legal Holidays, Lunar Holidays (Seollal, Chuseok, etc.), and Substitute Holidays
        """
        try:
            if isinstance(date_val, (datetime.datetime, datetime.date)):
                dt = date_val
                iso_str = dt.strftime("%Y-%m-%d")
            else:
                clean_s = str(date_val).replace("-", "").replace("/", "").strip()
                if len(clean_s) != 8 or not clean_s.isdigit():
                    return False
                dt = datetime.datetime.strptime(clean_s, "%Y%m%d")
                iso_str = f"{clean_s[:4]}-{clean_s[4:6]}-{clean_s[6:]}"

            # 1. Weekend check (5=Saturday, 6=Sunday)
            if dt.weekday() >= 5:
                return False

            # 2. Korean Legal & Substitute Holiday check
            if iso_str in self._kr_holidays:
                return False

            return True
        except Exception:
            return False

    def get_log_file_path(self, year: Optional[int] = None) -> str:
        if year is None:
            year = datetime.datetime.now().year
        return os.path.join(LIC_LOG_DIR, f"{year}_USER.dat")

    def get_usage_file_path(self, year: Optional[int] = None) -> str:
        if year is None:
            year = datetime.datetime.now().year
        return os.path.join(LIC_LOG_DIR, f"{year}_USAGE.dat")

    def get_1min_log_file_path(self, year: Optional[int] = None) -> str:
        if year is None:
            year = datetime.datetime.now().year
        return os.path.join(LIC_LOG_1MIN_DIR, f"{year}_USER.dat")

    def get_available_dates(self) -> List[str]:
        """
        Returns a sorted list of available business day dates (YYYY-MM-DD) from all log files.
        Automatically discovers newly created yearly/daily files and excludes weekends and public holidays.
        """
        now = time.time()
        current_log_dir = LIC_LOG_DIR

        today_year = datetime.datetime.now().year
        candidate_years = {today_year - 1, today_year}
        server_log_dates = set()
        if os.path.exists(current_log_dir):
            try:
                for fname in os.listdir(current_log_dir):
                    if fname.endswith("_USER.dat") and len(fname) == 13 and fname[:4].isdigit():
                        candidate_years.add(int(fname[:4]))
                    elif fname.endswith("_ServerLog.dat") and len(fname) == 22 and fname[:8].isdigit():
                        d_str = fname[:8]
                        iso_val = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:]}"
                        if self.is_business_day(iso_val):
                            server_log_dates.add(iso_val)
            except Exception:
                pass

        log_files = []
        file_sigs = []
        for y in sorted(candidate_years):
            p = self.get_log_file_path(y)
            if os.path.exists(p):
                log_files.append(p)
                try:
                    st = os.stat(p)
                    file_sigs.append((y, st.st_mtime, st.st_size))
                except Exception:
                    file_sigs.append((y, 0.0, 0))

        current_sig = (
            current_log_dir,
            tuple(file_sigs),
            len(server_log_dates),
            max(server_log_dates, default=""),
        )
        if (
            self._available_dates_cache
            and self._available_dates_sig == current_sig
            and (now - self._available_dates_loaded_at < 600)
        ):
            return self._available_dates_cache

        if not log_files and not server_log_dates:
            today = datetime.datetime.now()
            today_iso = today.strftime("%Y-%m-%d")
            return [today_iso]

        dates_set = set()
        valid_dates_ymd = set()
        non_biz_dates_ymd = set()
        for log_file in log_files:
            try:
                with open(log_file, "r", encoding="utf-8-sig", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        comma_idx = line.find(",")
                        if comma_idx == -1:
                            continue
                        d_str = line[:comma_idx].strip()
                        if d_str in valid_dates_ymd or d_str in non_biz_dates_ymd:
                            continue
                        if len(d_str) != 8 or not d_str.isdigit():
                            continue
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) < 3:
                            continue
                        prod_val = parts[3].upper() if len(parts) >= 4 else "MD2"
                        if "MD2" not in prod_val:
                            continue
                        if self.time_str_to_slot_15m(parts[1]) is None:
                            continue
                        iso_val = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:]}"
                        if self.is_business_day(iso_val):
                            valid_dates_ymd.add(d_str)
                            dates_set.add(iso_val)
                        else:
                            non_biz_dates_ymd.add(d_str)
            except Exception:
                pass

        if not dates_set and server_log_dates:
            dates_set = set(server_log_dates)

        if not dates_set:
            dates_set.add(datetime.datetime.now().strftime("%Y-%m-%d"))

        sorted_dates = sorted(list(dates_set), reverse=True)
        self._available_dates_cache = sorted_dates
        self._available_dates_loaded_at = now
        self._available_dates_sig = current_sig
        return sorted_dates

    def resolve_date_range(self, start_date: Optional[str] = None, end_date: Optional[str] = None, single_date: Optional[str] = None) -> List[str]:
        """
        Resolves input date parameters into a sorted list of YYYYMMDD strings.
        Strictly filters to normal business days (excluding weekends and public/substitute holidays).
        """
        avail_dates = self.get_available_dates()
        avail_ymd_sorted = sorted([d.replace("-", "") for d in avail_dates])

        today_ymd = datetime.datetime.now().strftime("%Y%m%d")

        if single_date:
            target_ymd = single_date.replace("-", "").replace("/", "")
            if target_ymd > today_ymd:
                target_ymd = today_ymd
            return [target_ymd]

        if not start_date and not end_date:
            if avail_ymd_sorted:
                return [avail_ymd_sorted[-1]]
            return [today_ymd]

        s_ymd = start_date.replace("-", "").replace("/", "") if start_date else (avail_ymd_sorted[0] if avail_ymd_sorted else today_ymd)
        e_ymd = end_date.replace("-", "").replace("/", "") if end_date else (avail_ymd_sorted[-1] if avail_ymd_sorted else today_ymd)

        # Cap future dates strictly to today
        if s_ymd > today_ymd:
            s_ymd = today_ymd
        if e_ymd > today_ymd:
            e_ymd = today_ymd

        if s_ymd > e_ymd:
            s_ymd, e_ymd = e_ymd, s_ymd

        selected = [d for d in avail_ymd_sorted if s_ymd <= d <= e_ymd and self.is_business_day(d)]
        if not selected:
            # If no logs exist in the requested range, generate valid business days within calendar range
            try:
                s_dt = datetime.datetime.strptime(s_ymd, "%Y%m%d")
                e_dt = datetime.datetime.strptime(e_ymd, "%Y%m%d")
                cal_dates = []
                cur = s_dt
                while cur <= e_dt:
                    if self.is_business_day(cur):
                        cal_dates.append(cur.strftime("%Y%m%d"))
                    cur += datetime.timedelta(days=1)
                return cal_dates if cal_dates else [s_ymd]
            except Exception:
                return [s_ymd]

        return selected

    def time_str_to_slot_15m(self, t_raw: str, round_early_1m: bool = True) -> Optional[str]:
        t_clean = t_raw.strip()
        if not t_clean.isdigit():
            return None
        t_val = int(t_clean)
        hour = t_val // 100
        minute = t_val % 100

        if round_early_1m and minute % 15 == 14:
            minute += 1
            if minute >= 60:
                hour += 1
                minute -= 60

        if hour < 8 or hour > 17:
            return None
        if hour == 17 and minute >= 15:
            return None

        slot_min = (minute // 15) * 15
        return f"{hour:02d}:{slot_min:02d}"

# Singleton instance
log_parser_service = LogParserService()
