# -*- coding: utf-8 -*-
r"""
Anomaly Monitoring Service:
Parses client-side CATIA process trace logs (HKTCATProcTrace_{HOSTNAME}.csv)
from H:\DSLS_CHK\HKT_PROCTRACE_LOG to detect idle license occupation ("유휴 진입").
"""
import os
import glob
import csv
import re
from typing import Dict, List, Any, Optional

from services.config import PROCTRACE_LOG_DIR
from services.user_master import user_master_service
from services.cache import cache_store

class AnomalyService:
    def __init__(self):
        pass

    def _extract_pid(self, desc: str, pid_col: str) -> Optional[str]:
        if pid_col and pid_col not in ("-", "0", ""):
            return str(pid_col).strip()
        m = re.search(r"PID[:\s]+(\d+)", desc, re.IGNORECASE)
        if m:
            return m.group(1)
        return None

    def get_idle_anomalies(
        self,
        department: Optional[str] = None,
        search_keyword: Optional[str] = None
    ) -> Dict[str, Any]:
        dept_filter = department or ""
        kw_filter = (search_keyword or "").strip().lower()

        cache_key = f"anomaly_idle_live:{dept_filter}:{kw_filter}"
        cached = cache_store.get(cache_key)
        if cached:
            return cached

        if not os.path.exists(PROCTRACE_LOG_DIR):
            res = {
                "status": "warning",
                "message": f"ProcTrace 디렉토리({PROCTRACE_LOG_DIR})가 존재하지 않습니다.",
                "mode": "realtime",
                "total_idle_events": 0,
                "unique_users_count": 0,
                "monitored_hosts_count": 0,
                "dept_summary": [],
                "items": []
            }
            return res

        trace_files = glob.glob(os.path.join(PROCTRACE_LOG_DIR, "*.csv"))
        host_user_cache: Dict[str, Dict[str, str]] = {}

        raw_events: List[Dict[str, Any]] = []

        for fpath in trace_files:
            fname = os.path.basename(fpath)
            # Default host from filename: HKTCATProcTrace_OCW2407004.csv -> OCW2407004
            default_host = fname.replace("HKTCATProcTrace_", "").replace(".csv", "").strip()

            encodings = ["utf-8-sig", "utf-8", "cp949", "euc-kr"]
            host_records: List[Dict[str, Any]] = []

            for enc in encodings:
                try:
                    with open(fpath, "r", encoding=enc, errors="ignore") as fp:
                        reader = csv.reader(fp)
                        for row in reader:
                            if not row or len(row) < 5:
                                continue

                            ts_raw = row[0].strip().replace('"', '')
                            if not ts_raw or len(ts_raw) < 10:
                                continue

                            host_raw = row[1].strip().replace('"', '') if len(row) > 1 and row[1].strip() else default_host
                            user_raw = row[2].strip().replace('"', '') if len(row) > 2 else ""
                            event_code = row[3].strip().replace('"', '') if len(row) > 3 else ""
                            desc = row[4].strip().replace('"', '') if len(row) > 4 else ""
                            pid_col = row[5].strip().replace('"', '') if len(row) > 5 else "-"
                            cpu = row[6].strip().replace('"', '') if len(row) > 6 else "-"
                            mem = row[7].strip().replace('"', '') if len(row) > 7 else "-"

                            pid = self._extract_pid(desc, pid_col)

                            host_records.append({
                                "ts": ts_raw,
                                "host": host_raw,
                                "user": user_raw,
                                "event_code": event_code,
                                "desc": desc,
                                "pid": pid,
                                "cpu": cpu,
                                "mem": mem
                            })
                    break
                except Exception:
                    continue

            if not host_records:
                continue

            # Sort chronological per host
            host_records.sort(key=lambda x: x["ts"])

            # Multi-PID Process State Machine for this host
            # Tracks all currently alive CATIA processes on this host
            active_procs: Dict[str, Dict[str, Any]] = {}
            last_user = ""
            last_host = default_host

            for r in host_records:
                ev_code = r["event_code"]
                ev_desc = r["desc"]
                ts = r["ts"]
                pid_val = r["pid"]

                if r["user"]:
                    last_user = r["user"]
                if r["host"]:
                    last_host = r["host"]

                # 1. Process Start / Service start already running
                if ev_code == "RUN_DETECTED" or "SERVICE_START_ALREADY_RUNNING" in ev_code:
                    if pid_val:
                        active_procs[pid_val] = {
                            "pid": pid_val,
                            "status": "ACTIVE",
                            "start_ts": ts,
                            "idle_ts": None,
                            "cpu": r["cpu"],
                            "mem": r["mem"],
                            "desc": ev_desc
                        }
                # 2. Watch Started (Active until idle)
                elif ev_code == "WATCH_STARTED":
                    if pid_val:
                        if pid_val in active_procs:
                            active_procs[pid_val]["cpu"] = r["cpu"]
                            active_procs[pid_val]["mem"] = r["mem"]
                            if not active_procs[pid_val].get("is_zombie"):
                                active_procs[pid_val]["status"] = "ACTIVE"
                        else:
                            active_procs[pid_val] = {
                                "pid": pid_val,
                                "status": "ACTIVE",
                                "start_ts": ts,
                                "idle_ts": None,
                                "cpu": r["cpu"],
                                "mem": r["mem"],
                                "desc": ev_desc,
                                "is_zombie": False
                            }
                # 3. Watch Cancelled (User interaction detected)
                elif ev_code == "WATCH_CANCELLED":
                    if pid_val and pid_val in active_procs:
                        active_procs[pid_val]["status"] = "ACTIVE"
                        active_procs[pid_val]["idle_ts"] = None
                        active_procs[pid_val]["is_zombie"] = False
                # 4. Zombie Skipped (License unconsumed zombie process - excluded from idle monitoring)
                elif (
                    ev_code == "ZOMBIE_SKIPPED"
                    or "ZOMBIE" in ev_code
                    or "라이선스 미점유" in ev_desc
                ):
                    if pid_val:
                        if pid_val in active_procs:
                            active_procs[pid_val]["status"] = "ZOMBIE"
                            active_procs[pid_val]["is_zombie"] = True
                            active_procs[pid_val]["desc"] = ev_desc
                            active_procs[pid_val]["cpu"] = r["cpu"]
                            active_procs[pid_val]["mem"] = r["mem"]
                        else:
                            active_procs[pid_val] = {
                                "pid": pid_val,
                                "status": "ZOMBIE",
                                "start_ts": ts,
                                "idle_ts": None,
                                "cpu": r["cpu"],
                                "mem": r["mem"],
                                "desc": ev_desc,
                                "is_zombie": True
                            }
                # 5. Idle Detected or Idle Heartbeat (유휴 진입 및 주기적 유휴 유지)
                elif (
                    ev_code in ("IDLE_DETECTED", "IDLE_HEARTBEAT")
                    or "IDLE_DETECTED" in ev_code
                    or "IDLE_HEARTBEAT" in ev_code
                    or "유휴 진입" in ev_desc
                    or "유휴 유지" in ev_desc
                    or "IDLE" in ev_code
                ):
                    if pid_val:
                        if pid_val not in active_procs:
                            active_procs[pid_val] = {
                                "pid": pid_val,
                                "status": "IDLE",
                                "event_code": "IDLE_DETECTED",
                                "start_ts": ts,
                                "idle_ts": ts,
                                "last_ts": ts,
                                "cpu": r["cpu"],
                                "mem": r["mem"],
                                "desc": ev_desc,
                                "is_zombie": False
                            }
                        else:
                            if not active_procs[pid_val].get("is_zombie"):
                                active_procs[pid_val]["status"] = "IDLE"
                                active_procs[pid_val]["event_code"] = "IDLE_DETECTED"
                                # 최초 유휴 진입 시점 보존 (IDLE_HEARTBEAT 반복 갱신 시에도 최초 유휴 시각 유지)
                                if not active_procs[pid_val].get("idle_ts"):
                                    active_procs[pid_val]["idle_ts"] = ts
                                active_procs[pid_val]["last_ts"] = ts
                                active_procs[pid_val]["desc"] = ev_desc
                            active_procs[pid_val]["cpu"] = r["cpu"]
                            active_procs[pid_val]["mem"] = r["mem"]
                    else:
                        for p in active_procs:
                            if not active_procs[p].get("is_zombie"):
                                active_procs[p]["status"] = "IDLE"
                                active_procs[p]["event_code"] = "IDLE_DETECTED"
                                if not active_procs[p].get("idle_ts"):
                                    active_procs[p]["idle_ts"] = ts
                                active_procs[p]["last_ts"] = ts
                                active_procs[p]["desc"] = ev_desc
                # 6. Active Detected (User resumed work on this process/host)
                elif (
                    ev_code == "ACTIVE_DETECTED"
                    or "활성 감지" in ev_desc
                    or "활성" in ev_desc
                    or "ACTIVE" in ev_code
                ):
                    if pid_val and pid_val in active_procs:
                        active_procs[pid_val]["status"] = "ACTIVE"
                        active_procs[pid_val]["event_code"] = "ACTIVE_DETECTED"
                        active_procs[pid_val]["idle_ts"] = None
                        active_procs[pid_val]["is_zombie"] = False
                        active_procs[pid_val]["cpu"] = r["cpu"]
                        active_procs[pid_val]["mem"] = r["mem"]
                    else:
                        for p in active_procs:
                            active_procs[p]["status"] = "ACTIVE"
                            active_procs[p]["event_code"] = "ACTIVE_DETECTED"
                            active_procs[p]["idle_ts"] = None
                            active_procs[p]["is_zombie"] = False
                # 7. Process Terminated (License returned)
                elif ev_code == "TERM_DETECTED" or "종료" in ev_desc:
                    if pid_val and pid_val in active_procs:
                        del active_procs[pid_val]
                    elif not pid_val:
                        active_procs.clear()
                # 8. System Shutdown or Service Stopped
                elif ev_code in ("SYSTEM_SHUTDOWN", "SERVICE_STOP"):
                    active_procs.clear()

            # Final Evaluation for this Host:
            # Exclude zombie processes (ZOMBIE_SKIPPED) that do not consume CATIA licenses
            valid_procs = {
                p: st for p, st in active_procs.items()
                if not st.get("is_zombie") and st.get("status") != "ZOMBIE"
            }

            # Rule:
            # 1. len(valid_procs) > 0 (At least 1 valid license-consuming process still alive)
            # 2. ALL alive valid processes have status == 'IDLE'
            # 3. NONE of the alive valid processes have status == 'ACTIVE'
            if len(valid_procs) > 0:
                all_idle = all(st["status"] == "IDLE" for st in valid_procs.values())
                if all_idle:
                    pids_list = sorted(valid_procs.keys())
                    pids_str = ", ".join(pids_list)

                    idle_timestamps = [st["idle_ts"] for st in valid_procs.values() if st.get("idle_ts")]
                    main_ts = min(idle_timestamps) if idle_timestamps else host_records[-1]["ts"]

                    date_part = main_ts[:10]
                    time_part = main_ts[11:19] if len(main_ts) >= 19 else ""

                    if last_host not in host_user_cache:
                        host_user_cache[last_host] = user_master_service.get_user_info(last_host)
                    u_info = host_user_cache[last_host]
                    user_name = u_info.get("USER_NAME") or last_user or last_host
                    emp_id = u_info.get("EMP_NO") or "-"
                    dept_name = u_info.get("DEPT_NAME") or "미등록"
                    ip_addr = u_info.get("IP") or "-"

                    cpus = [st["cpu"] for st in valid_procs.values() if st.get("cpu") and st["cpu"] != "-"]
                    mems = [st["mem"] for st in valid_procs.values() if st.get("mem") and st["mem"] != "-"]
                    cpu_str = ", ".join(cpus) if cpus else "0.0%"
                    mem_str = ", ".join(mems) if mems else "-"

                    proc_count_str = f"{len(valid_procs)}개 프로세스" if len(valid_procs) > 1 else "1개 프로세스"

                    desc_text = f"모든 CATIA({proc_count_str}) 유휴 진입 방치 중"
                    status_label = "유휴 진입 (전체 PID 방치)"

                    raw_events.append({
                        "timestamp": main_ts,
                        "date": date_part,
                        "time": time_part,
                        "hostname": last_host,
                        "user_name": user_name,
                        "emp_id": emp_id,
                        "dept_name": dept_name,
                        "ip": ip_addr,
                        "event_code": "IDLE_DETECTED",
                        "description": desc_text,
                        "pid": pids_str,
                        "cpu": cpu_str,
                        "mem": mem_str,
                        "process_count": len(valid_procs),
                        "status_label": status_label,
                        "action_recommend": "License 회수 권고 / 점유 상태 확인"
                    })

        # Sort chronological (Oldest at top, Newest at bottom)
        raw_events.sort(key=lambda x: x["timestamp"])

        filtered_events = []
        for ev in raw_events:
            if dept_filter and dept_filter != "전체" and ev["dept_name"] != dept_filter:
                continue
            if kw_filter:
                kw_str = f"{ev['user_name']} {ev['emp_id']} {ev['dept_name']} {ev['hostname']} {ev['ip']} {ev['pid']}".lower()
                if kw_filter not in kw_str:
                    continue
            filtered_events.append(ev)

        unique_users = set(f"{ev['hostname']}:{ev['user_name']}" for ev in filtered_events)
        unique_hosts = set(ev["hostname"] for ev in filtered_events)

        dept_counts: Dict[str, int] = {}
        for ev in filtered_events:
            d = ev["dept_name"]
            dept_counts[d] = dept_counts.get(d, 0) + 1

        dept_summary = [
            {"dept_name": k, "count": v}
            for k, v in sorted(dept_counts.items(), key=lambda x: x[1], reverse=True)
        ]

        res = {
            "status": "success",
            "mode": "realtime",
            "target_date": "실시간(Live) 모니터링",
            "total_idle_events": len(filtered_events),
            "unique_users_count": len(unique_users),
            "unique_hosts_count": len(unique_hosts),
            "monitored_hosts_count": len(trace_files),
            "dept_summary": dept_summary,
            "items": filtered_events
        }

        cache_store.set(cache_key, res, 5) # 5 seconds short TTL for realtime
        return res

# Singleton instance
anomaly_service = AnomalyService()
