import os
import json
import time
from typing import Dict, List
from services.config import (
    USER_MASTER_PATH,
    USER_MASTER_SUB_PATH,
    EQUIPMENT_OVERRIDE_CACHE_PATH,
    EMP_DIRECTORY_PATH,
    OBSERVED_IPS_CACHE_PATH
)

AUTOVT_HOSTS = {"OCW2112011", "OCW2206025", "OCW2206063", "10.82.79.106", "10.82.79.107", "10.82.63.69"}

# Romanized name mapping from legacy Sub CSV registrations
ROMANIZED_NAME_MAP = {
    "SHIN WOO JIN": ("신우진", "21600104", "EU 2 Project"),
    "KIM SEONG RAE": ("김성래", "21600592", "신기술 상품화 (25) Project"),
    "LEEHYUNBAE": ("이현배", "21500048", "TB Asia Project"),
    "SEONGJIN LEE": ("이성진", "21601364", "TB 성능혁신팀"),
    "SHIN HUN CHEOL": ("신훈철", "20700168", "Audi project"),
    "KIMDONGHYUN": ("김동현", "21600077", "SUV & LT 모델 Project"),
    "KIM TAESAN": ("김태산", "21600390", "EU1Project"),
    "HUNSIK CHOI": ("최훈식", "21600128", "ATC)OE Development Project"),
    "SANGHOON HWANG": ("황상훈", "21000130", "TB 성능혁신팀"),
}

# Typo and dialect correction map
TYPO_NAME_MAP = {
    ("백인제", "Winter & AW 확산팀"): ("백인재", "21600642", "Winter & AW 확산팀"),
    ("백인제", ""): ("백인재", "21600642", "Winter & AW 확산팀"),
    ("백인제", "미등록"): ("백인재", "21600642", "Winter & AW 확산팀"),
}

class UserMasterService:
    def __init__(self):
        self._cache: Dict[str, Dict[str, str]] = {}
        self._sub_cache: Dict[str, Dict[str, str]] = {}
        self._emp_dir: Dict[str, Dict[str, str]] = {}
        self._dir_name_to_emp_keys: Dict[str, set] = {}
        self._main_emp_profiles: Dict[str, Dict[str, str]] = {}
        self._loaded_at: float = 0.0
        self._last_main_mtime: float = 0.0
        self._last_sub_mtime: float = 0.0
        self._load_emp_directory()

    @staticmethod
    def _is_valid_emp_id(emp_k: str) -> bool:
        k_up = emp_k.strip().upper() if emp_k else ""
        if not k_up or k_up in ["-", "미등록", "AUTOVT SERVER"]:
            return False
        if k_up.startswith(("OCW", "ONT", "OCL", "DESKTOP-", "MYCOM", "NBM", "SUB")):
            return False
        return True

    def _rebuild_unique_name_index(self):
        combined_name_keys: Dict[str, set] = {
            k: set(v) for k, v in getattr(self, "_dir_name_to_emp_keys", {}).items()
        }
        for _, prof in getattr(self, "_main_emp_profiles", {}).items():
            u_name = prof.get("USER_NAME", "").strip()
            e_no = prof.get("EMP_NO", "").strip()
            if u_name and self._is_valid_emp_id(e_no):
                combined_name_keys.setdefault(u_name, set()).add(e_no)

        self._emp_by_name_unique = {}
        for name, keys in combined_name_keys.items():
            distinct_norm_ids = {k.lower() for k in keys}
            if len(distinct_norm_ids) == 1:
                chosen = next(iter(keys))
                for k in keys:
                    if k.upper() in getattr(self, "_main_emp_profiles", {}):
                        chosen = self._main_emp_profiles[k.upper()]["EMP_NO"]
                        break
                self._emp_by_name_unique[name] = chosen

    def _load_emp_directory(self):
        if os.path.exists(EMP_DIRECTORY_PATH):
            try:
                with open(EMP_DIRECTORY_PATH, "r", encoding="utf-8") as f:
                    self._emp_dir = json.load(f)
                # Build reverse lookup indexes (only for actual employee ID keys, never hostnames)
                self._emp_by_name_team = {}
                name_to_emp_keys: Dict[str, set] = {}
                for emp_k, val in self._emp_dir.items():
                    if not self._is_valid_emp_id(emp_k):
                        continue
                    name = val.get("NAME", "").strip()
                    team = val.get("TEAM", "").strip()
                    if name:
                        name_to_emp_keys.setdefault(name, set()).add(emp_k)
                        if team:
                            self._emp_by_name_team[(name, team)] = emp_k

                self._dir_name_to_emp_keys = name_to_emp_keys
                self._rebuild_unique_name_index()
            except Exception:
                self._emp_dir = {}
                self._dir_name_to_emp_keys = {}
                self._emp_by_name_team = {}
                self._emp_by_name_unique = {}

    def _resolve_emp_id(self, user_name: str, emp_no: str, host: str, dept_name: str, ip: str = "-"):
        """
        Resolves employee numbers / codes to real Korean names using the employee directory.
        Strictly maps AutoVT Server machines (OCW2112011, OCW2206025, OCW2206063, 10.82.79.106, 10.82.79.107).
        """
        if not self._emp_dir:
            self._load_emp_directory()

        if (
            host.upper() in AUTOVT_HOSTS
            or ip in ["10.82.79.106", "10.82.79.107", "10.82.63.69"]
            or user_name in ["AutoVT Server", "신희승"]
            or emp_no == "AutoVT Server"
        ):
            return "AutoVT Server", "AutoVT Server", "디지털인프라혁신팀"

        resolved_user = user_name.strip() if user_name else ""
        resolved_dept = dept_name.strip() if dept_name else ""
        resolved_emp = emp_no.strip() if emp_no else ""

        # 1. Check Romanized Name Map
        u_upper = resolved_user.upper()
        if u_upper in ROMANIZED_NAME_MAP:
            r_u, r_e, r_d = ROMANIZED_NAME_MAP[u_upper]
            resolved_user = r_u
            resolved_emp = r_e
            if not resolved_dept or resolved_dept in ["미등록", "-", ""]:
                resolved_dept = r_d

        # 2. Check Typo Map
        if (resolved_user, resolved_dept) in TYPO_NAME_MAP:
            r_u, r_e, r_d = TYPO_NAME_MAP[(resolved_user, resolved_dept)]
            resolved_user = r_u
            resolved_emp = r_e
            resolved_dept = r_d
        elif (resolved_user, "") in TYPO_NAME_MAP:
            r_u, r_e, r_d = TYPO_NAME_MAP[(resolved_user, "")]
            resolved_user = r_u
            resolved_emp = r_e
            resolved_dept = r_d

        # Clean broken replacement characters and check validity
        has_broken_user = (
            not resolved_user
            or "\ufffd" in resolved_user
            or resolved_user in ["미등록", "-", host.upper(), host, ""]
            or resolved_user == resolved_emp
            or resolved_user.isdigit()
        )
        has_broken_dept = (
            not resolved_dept
            or "\ufffd" in resolved_dept
            or resolved_dept in ["미등록", "-", "", None]
        )

        keys_to_try = [resolved_emp, resolved_user, host.upper()]
        for k in keys_to_try:
            if k and k in self._emp_dir:
                entry = self._emp_dir[k]
                # Only resolve name if CSV user_name was invalid, missing, or broken
                if has_broken_user and entry.get("NAME"):
                    resolved_user = entry["NAME"]
                # Only resolve dept if CSV dept_name was missing, unregistered, or broken
                if has_broken_dept and entry.get("TEAM"):
                    resolved_dept = entry["TEAM"]
                # If emp_no was missing, fill it from key only if key is a valid employee ID (not a hostname)
                if (not resolved_emp or resolved_emp in ["-", "미등록", ""]) and self._is_valid_emp_id(k):
                    resolved_emp = k
                break

        # If emp_no is still missing, resolve from name + dept or unique name across company
        if not resolved_emp or resolved_emp in ["-", "미등록", ""]:
            if (resolved_user, resolved_dept) in getattr(self, "_emp_by_name_team", {}):
                resolved_emp = self._emp_by_name_team[(resolved_user, resolved_dept)]
            elif resolved_user in getattr(self, "_emp_by_name_unique", {}):
                cand_emp = self._emp_by_name_unique[resolved_user]
                cand_team = self._emp_dir.get(cand_emp, {}).get("TEAM", "").strip()
                if (
                    has_broken_dept
                    or not cand_team
                    or cand_team == resolved_dept
                    or cand_emp.upper() in getattr(self, "_main_emp_profiles", {})
                ):
                    resolved_emp = cand_emp

        return resolved_user, resolved_emp, resolved_dept

    def _parse_csv_file(self, file_path: str) -> Dict[str, Dict[str, str]]:
        """
        Parses a DSLS user CSV file with automated BOM / encoding detection and delimiter parsing.
        When parsing the main CSV (USER_MASTER_PATH), also indexes employee profiles (EMP_NO -> USER_NAME, DEPT_NAME)
        even for rows where the hostname (컴퓨터명) column is blank.
        """
        is_main_csv = os.path.normcase(os.path.abspath(file_path)) == os.path.normcase(os.path.abspath(USER_MASTER_PATH))
        if is_main_csv:
            self._main_emp_profiles = {}

        if not os.path.exists(file_path):
            return {}

        mapping: Dict[str, Dict[str, str]] = {}

        # Detect encoding based on BOM / byte signature
        encodings_to_try = []
        try:
            with open(file_path, "rb") as bf:
                raw_head = bf.read(4)
                sample = bf.read(8192)
            if raw_head.startswith(b'\xff\xfe'):
                encodings_to_try = ["utf-16le", "utf-16"]
            elif raw_head.startswith(b'\xfe\xff'):
                encodings_to_try = ["utf-16be", "utf-16"]
            elif raw_head.startswith(b'\xef\xbb\xbf'):
                encodings_to_try = ["utf-8-sig", "utf-8"]
            elif b'\x00' in sample:
                encodings_to_try = ["utf-16le", "utf-16"]
            else:
                # Test UTF-8 strict on sample
                try:
                    sample.decode('utf-8')
                    encodings_to_try = ["utf-8-sig", "utf-8", "cp949", "euc-kr"]
                except UnicodeDecodeError:
                    # Non-UTF8 Korean Windows Excel CSV -> prioritize CP949 / EUC-KR
                    encodings_to_try = ["cp949", "euc-kr", "utf-8-sig", "utf-8"]
        except Exception:
            encodings_to_try = ["cp949", "euc-kr", "utf-8-sig", "utf-8", "utf-16le"]

        for enc in encodings_to_try:
            try:
                temp_mapping = {}
                temp_emp_profiles = {}
                with open(file_path, "r", encoding=enc, errors="strict") as f:
                    first_line = f.readline()
                    if not first_line:
                        continue

                    delim = "\t" if "\t" in first_line else ","
                    raw_headers = [h.strip().replace("\x00", "") for h in first_line.split(delim)]

                    col_user = 2
                    col_emp = 3
                    col_dept = 4
                    col_ip = 6
                    col_host = 7

                    for idx, col in enumerate(raw_headers):
                        c_clean = col.replace(" ", "").upper()
                        if "사용자명" in c_clean or "성명" in c_clean or "USER" in c_clean:
                            col_user = idx
                        elif "사번" in c_clean or "EMP" in c_clean or "ID" in c_clean:
                            col_emp = idx
                        elif "소속" in c_clean or "부서" in c_clean or "DEPT" in c_clean:
                            col_dept = idx
                        elif "IP" in c_clean:
                            col_ip = idx
                        elif "컴퓨터" in c_clean or "호스트" in c_clean or "HOST" in c_clean:
                            col_host = idx

                    for line in f:
                        parts = [p.strip().replace("\x00", "") for p in line.split(delim)]
                        if len(parts) > max(col_user, col_emp, col_dept):
                            hostname = parts[col_host] if col_host < len(parts) else ""
                            user_name = parts[col_user] if col_user < len(parts) else ""
                            emp_no = parts[col_emp] if col_emp < len(parts) else ""
                            dept_name = parts[col_dept] if col_dept < len(parts) else ""
                            ip = parts[col_ip] if col_ip < len(parts) else "-"

                            if hostname in ["컴퓨터명", "호스트명", "HOSTNAME"] or emp_no in ["사용자ID", "사번", "EMP_NO", "ID"]:
                                continue

                            raw_dept = dept_name.strip() if dept_name else ""

                            if not hostname:
                                if is_main_csv and (emp_no or user_name) and raw_dept not in ["-", "미등록", ""]:
                                    d_user = user_name if user_name else emp_no
                                    d_user, r_emp, _ = self._resolve_emp_id(
                                        d_user, emp_no, "", raw_dept, ip
                                    )
                                    if r_emp and r_emp not in ["-", "미등록", ""]:
                                        self._emp_by_name_team[(d_user, raw_dept)] = r_emp
                                        if r_emp.upper() not in temp_emp_profiles:
                                            temp_emp_profiles[r_emp.upper()] = {
                                                "USER_NAME": d_user,
                                                "EMP_NO": r_emp,
                                                "DEPT_NAME": raw_dept,
                                                "PRIMARY_HOSTNAME": ""
                                            }
                                continue

                            has_real_user = bool(
                                (emp_no and emp_no not in ["-", "미등록", ""])
                                or (user_name and user_name not in ["-", "미등록", "", hostname.upper(), hostname])
                            )
                            display_user = user_name if user_name else (emp_no if emp_no else hostname)
                            display_dept = raw_dept if raw_dept else "미등록"

                            # Resolve employee number to real name
                            display_user, emp_no, display_dept = self._resolve_emp_id(
                                display_user, emp_no, hostname, display_dept, ip
                            )

                            if raw_dept not in ["-", "미등록", ""] and emp_no and emp_no not in ["-", "미등록", ""]:
                                self._emp_by_name_team[(display_user, raw_dept)] = emp_no

                            temp_mapping[hostname.upper()] = {
                                "USER_NAME": display_user,
                                "EMP_NO": emp_no if emp_no else "-",
                                "DEPT_NAME": display_dept,
                                "RAW_DEPT": raw_dept,
                                "HAS_REAL_USER": has_real_user,
                                "HOSTNAME": hostname.upper(),
                                "IP": ip if ip else "-"
                            }
                            if is_main_csv and emp_no and emp_no not in ["-", "미등록", ""] and raw_dept not in ["-", "미등록", ""]:
                                temp_emp_profiles[emp_no.upper()] = {
                                    "USER_NAME": display_user,
                                    "EMP_NO": emp_no,
                                    "DEPT_NAME": raw_dept,
                                    "PRIMARY_HOSTNAME": hostname.upper()
                                }
                if temp_mapping:
                    mapping = temp_mapping
                    if is_main_csv:
                        self._main_emp_profiles = temp_emp_profiles
                        self._rebuild_unique_name_index()
                    break
            except Exception:
                continue

        # Fallback with errors="replace" if strict decoding failed for all encodings
        if not mapping:
            for enc in ["cp949", "utf-8-sig", "utf-8", "euc-kr"]:
                try:
                    temp_emp_profiles = {}
                    with open(file_path, "r", encoding=enc, errors="replace") as f:
                        first_line = f.readline()
                        if not first_line:
                            continue
                        delim = "\t" if "\t" in first_line else ","
                        raw_headers = [h.strip().replace("\x00", "") for h in first_line.split(delim)]
                        col_user = 2
                        col_emp = 3
                        col_dept = 4
                        col_ip = 6
                        col_host = 7
                        for idx, col in enumerate(raw_headers):
                            c_clean = col.replace(" ", "").upper()
                            if "사용자명" in c_clean or "성명" in c_clean or "USER" in c_clean:
                                col_user = idx
                            elif "사번" in c_clean or "EMP" in c_clean or "ID" in c_clean:
                                col_emp = idx
                            elif "소속" in c_clean or "부서" in c_clean or "DEPT" in c_clean:
                                col_dept = idx
                            elif "IP" in c_clean:
                                col_ip = idx
                            elif "컴퓨터" in c_clean or "호스트" in c_clean or "HOST" in c_clean:
                                col_host = idx

                        for line in f:
                            parts = [p.strip().replace("\x00", "") for p in line.split(delim)]
                            if len(parts) > max(col_user, col_emp, col_dept):
                                hostname = parts[col_host] if col_host < len(parts) else ""
                                user_name = parts[col_user] if col_user < len(parts) else ""
                                emp_no = parts[col_emp] if col_emp < len(parts) else ""
                                dept_name = parts[col_dept] if col_dept < len(parts) else ""
                                ip = parts[col_ip] if col_ip < len(parts) else "-"

                                if hostname in ["컴퓨터명", "호스트명", "HOSTNAME"] or emp_no in ["사용자ID", "사번", "EMP_NO", "ID"]:
                                    continue

                                raw_dept = dept_name.strip() if dept_name else ""

                                if not hostname:
                                    if is_main_csv and (emp_no or user_name) and raw_dept not in ["-", "미등록", ""]:
                                        d_user = user_name if user_name else emp_no
                                        d_user, r_emp, _ = self._resolve_emp_id(
                                            d_user, emp_no, "", raw_dept, ip
                                        )
                                        if r_emp and r_emp not in ["-", "미등록", ""]:
                                            self._emp_by_name_team[(d_user, raw_dept)] = r_emp
                                            if r_emp.upper() not in temp_emp_profiles:
                                                temp_emp_profiles[r_emp.upper()] = {
                                                    "USER_NAME": d_user,
                                                    "EMP_NO": r_emp,
                                                    "DEPT_NAME": raw_dept,
                                                    "PRIMARY_HOSTNAME": ""
                                                }
                                    continue

                                has_real_user = bool(
                                    (emp_no and emp_no not in ["-", "미등록", ""])
                                    or (user_name and user_name not in ["-", "미등록", "", hostname.upper(), hostname])
                                )
                                display_user = user_name if user_name else (emp_no if emp_no else hostname)
                                display_dept = raw_dept if raw_dept else "미등록"

                                display_user, emp_no, display_dept = self._resolve_emp_id(
                                    display_user, emp_no, hostname, display_dept, ip
                                )

                                if raw_dept not in ["-", "미등록", ""] and emp_no and emp_no not in ["-", "미등록", ""]:
                                    self._emp_by_name_team[(display_user, raw_dept)] = emp_no

                                mapping[hostname.upper()] = {
                                    "USER_NAME": display_user,
                                    "EMP_NO": emp_no if emp_no else "-",
                                    "DEPT_NAME": display_dept,
                                    "RAW_DEPT": raw_dept,
                                    "HAS_REAL_USER": has_real_user,
                                    "HOSTNAME": hostname.upper(),
                                    "IP": ip if ip else "-"
                                }
                                if is_main_csv and emp_no and emp_no not in ["-", "미등록", ""] and raw_dept not in ["-", "미등록", ""]:
                                    temp_emp_profiles[emp_no.upper()] = {
                                        "USER_NAME": display_user,
                                        "EMP_NO": emp_no,
                                        "DEPT_NAME": raw_dept,
                                        "PRIMARY_HOSTNAME": hostname.upper()
                                    }
                    if mapping:
                        if is_main_csv:
                            self._main_emp_profiles = temp_emp_profiles
                            self._rebuild_unique_name_index()
                        break
                except Exception:
                    continue

        return mapping

    def _get_sub_master(self, force_reload: bool = False) -> Dict[str, Dict[str, str]]:
        """
        Reads and caches HKT_DSLS_USER_LIST_SUB.csv.
        Persists a local JSON backup (equipment_override.json) for high availability.
        """
        sub_mtime = 0.0
        if os.path.exists(USER_MASTER_SUB_PATH):
            try:
                sub_mtime = os.path.getmtime(USER_MASTER_SUB_PATH)
            except OSError:
                sub_mtime = 0.0

        if not force_reload and self._sub_cache and sub_mtime == self._last_sub_mtime:
            return self._sub_cache

        records = self._parse_csv_file(USER_MASTER_SUB_PATH)

        # Apply AutoVT Server special mapping
        for host, info in records.items():
            if (
                host.upper() in AUTOVT_HOSTS
                or info.get("USER_NAME") in ["신희승", "AutoVT Server"]
                or info.get("EMP_NO") == "AutoVT Server"
                or info.get("IP") in ["10.82.79.106", "10.82.79.107", "10.82.63.69"]
            ):
                info["USER_NAME"] = "AutoVT Server"
                info["EMP_NO"] = "AutoVT Server"
                info["DEPT_NAME"] = "디지털인프라혁신팀"

        # If read successfully, persist local JSON snapshot
        if records:
            try:
                os.makedirs(os.path.dirname(EQUIPMENT_OVERRIDE_CACHE_PATH), exist_ok=True)
                with open(EQUIPMENT_OVERRIDE_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            self._sub_cache = records
            self._last_sub_mtime = sub_mtime
            return records

        # Fallback to local snapshot
        if os.path.exists(EQUIPMENT_OVERRIDE_CACHE_PATH):
            try:
                with open(EQUIPMENT_OVERRIDE_CACHE_PATH, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    if cached:
                        self._sub_cache = cached
                        return cached
            except Exception:
                pass

        return self._sub_cache

    def get_user_master(self, force_reload: bool = False) -> Dict[str, Dict[str, str]]:
        """
        Returns unified mapping: UPPER(HOSTNAME) -> { USER_NAME, EMP_NO, DEPT_NAME, HOSTNAME, IP }
        Strictly hostname-centric (장비명 기준) resolution:
        1. Reads HKT_DSLS_USER_LIST.csv (Main CSV) keyed strictly by HOSTNAME.
        2. Merges HKT_DSLS_USER_LIST_SUB.csv (Sub CSV / equipment_override.json) by HOSTNAME.
        3. If same HOSTNAME exists in both Main & Sub, Main CSV's department takes precedence if valid.
        4. Never overrides a host's info using another host's record by name (prevents homonym / cross-host corruption).
        """
        now = time.time()
        # Fast path: return in-memory cache without hitting remote network drive getmtime
        if not force_reload and self._cache and (now - self._loaded_at < 60.0):
            return self._cache

        main_mtime = 0.0
        if os.path.exists(USER_MASTER_PATH):
            try:
                main_mtime = os.path.getmtime(USER_MASTER_PATH)
            except OSError:
                main_mtime = 0.0

        sub_mtime = 0.0
        if os.path.exists(USER_MASTER_SUB_PATH):
            try:
                sub_mtime = os.path.getmtime(USER_MASTER_SUB_PATH)
            except OSError:
                sub_mtime = 0.0

        if not force_reload and self._cache and main_mtime == self._last_main_mtime and sub_mtime == self._last_sub_mtime:
            self._loaded_at = now
            return self._cache

        # Step 1: Parse Main CSV (Keyed by UPPER(HOSTNAME))
        main_mapping = self._parse_csv_file(USER_MASTER_PATH)

        # Step 2: Parse Sub CSV / equipment_override.json (Keyed by UPPER(HOSTNAME))
        sub_records = self._get_sub_master(force_reload=force_reload)

        # Step 3: Unified Hostname-Centric Merge
        unified_mapping: Dict[str, Dict[str, str]] = {}
        all_hosts = set(main_mapping.keys()).union(set(sub_records.keys()))

        for host in all_hosts:
            host_upper = host.strip().upper()
            if not host_upper:
                continue

            # AutoVT Server hosts
            if (
                host_upper in AUTOVT_HOSTS
                or host_upper in ["10.82.79.106", "10.82.79.107", "10.82.63.69"]
            ):
                unified_mapping[host_upper] = {
                    "USER_NAME": "AutoVT Server",
                    "EMP_NO": "AutoVT Server",
                    "DEPT_NAME": "디지털인프라혁신팀",
                    "HOSTNAME": host_upper,
                    "IP": (
                        "10.82.79.106" if host_upper == "OCW2206025"
                        else ("10.82.79.107" if host_upper == "OCW2206063"
                              else ("10.82.63.69" if host_upper == "OCW2112011" else host_upper))
                    ),
                    "PRIMARY_HOSTNAME": host_upper,
                    "PRIMARY_IP": "-"
                }
                continue

            has_main = host_upper in main_mapping
            has_sub = host_upper in sub_records

            if has_main and has_sub:
                # Same equipment in both Main and Sub:
                # Main is authoritative for department when assigned to a real user;
                # if Main row is an unassigned PC placeholder and Sub has an assigned user, use Sub's dept.
                m_item = main_mapping[host_upper]
                s_item = sub_records[host_upper]

                u_name = m_item.get("USER_NAME") if m_item.get("USER_NAME") not in ["-", "미등록", host_upper, ""] else s_item.get("USER_NAME", host_upper)
                e_no = m_item.get("EMP_NO") if m_item.get("EMP_NO") not in ["-", "미등록", "", None] else s_item.get("EMP_NO", "-")
                m_raw_dept = m_item.get("RAW_DEPT", "")
                s_raw_dept = s_item.get("RAW_DEPT") or s_item.get("DEPT_NAME", "")
                m_has_user = m_item.get("HAS_REAL_USER", True)
                s_has_user = s_item.get("HAS_REAL_USER", True)
                if m_raw_dept not in ["-", "미등록", "", None] and (m_has_user or not s_has_user):
                    d_name = m_raw_dept
                elif s_raw_dept not in ["-", "미등록", "", None]:
                    d_name = s_raw_dept
                elif m_raw_dept not in ["-", "미등록", "", None]:
                    d_name = m_raw_dept
                else:
                    d_name = m_item.get("DEPT_NAME") if m_item.get("DEPT_NAME") not in ["-", "미등록", "", None] else s_item.get("DEPT_NAME", "미등록")
                ip_val = s_item.get("IP") if s_item.get("IP") not in ["-", "미등록", ""] else m_item.get("IP", "-")

                # If still missing name / dept / emp_no, resolve from emp_directory
                if d_name in ["-", "미등록", ""] or u_name in ["-", "미등록", host_upper] or e_no in ["-", "미등록", ""]:
                    r_u, r_e, r_d = self._resolve_emp_id(u_name, e_no, host_upper, d_name, ip_val)
                    if u_name in ["-", "미등록", host_upper] and r_u not in ["-", "미등록", host_upper]:
                        u_name = r_u
                    if e_no in ["-", "미등록", ""] and r_e not in ["-", "미등록", ""]:
                        e_no = r_e
                    if d_name in ["-", "미등록", ""] and r_d not in ["-", "미등록", ""]:
                        d_name = r_d

                unified_mapping[host_upper] = {
                    "USER_NAME": u_name or host_upper,
                    "EMP_NO": e_no or "-",
                    "DEPT_NAME": d_name or "미등록",
                    "HOSTNAME": host_upper,
                    "IP": ip_val or "-",
                    "PRIMARY_HOSTNAME": host_upper,
                    "PRIMARY_IP": ip_val or "-"
                }

            elif has_sub:
                # Equipment registered in Sub (equipment_override.json / SUB CSV) only:
                # Trust Sub record directly for this host. Do NOT cross-override from other hosts.
                s_item = sub_records[host_upper]
                u_name = s_item.get("USER_NAME", host_upper)
                e_no = s_item.get("EMP_NO", "-")
                d_name = s_item.get("DEPT_NAME", "미등록")
                ip_val = s_item.get("IP", "-")

                if d_name in ["-", "미등록", ""] or u_name in ["-", "미등록", host_upper] or e_no in ["-", "미등록", ""]:
                    r_u, r_e, r_d = self._resolve_emp_id(u_name, e_no, host_upper, d_name, ip_val)
                    if u_name in ["-", "미등록", host_upper] and r_u not in ["-", "미등록", host_upper]:
                        u_name = r_u
                    if e_no in ["-", "미등록", ""] and r_e not in ["-", "미등록", ""]:
                        e_no = r_e
                    if d_name in ["-", "미등록", ""] and r_d not in ["-", "미등록", ""]:
                        d_name = r_d

                unified_mapping[host_upper] = {
                    "USER_NAME": u_name or host_upper,
                    "EMP_NO": e_no or "-",
                    "DEPT_NAME": d_name or "미등록",
                    "HOSTNAME": host_upper,
                    "IP": ip_val or "-",
                    "PRIMARY_HOSTNAME": host_upper,
                    "PRIMARY_IP": ip_val or "-"
                }

            else:
                # Equipment registered in Main CSV only:
                m_item = main_mapping[host_upper]
                u_name = m_item.get("USER_NAME", host_upper)
                e_no = m_item.get("EMP_NO", "-")
                d_name = m_item.get("DEPT_NAME", "미등록")
                ip_val = m_item.get("IP", "-")

                if d_name in ["-", "미등록", ""] or u_name in ["-", "미등록", host_upper] or e_no in ["-", "미등록", ""]:
                    r_u, r_e, r_d = self._resolve_emp_id(u_name, e_no, host_upper, d_name, ip_val)
                    if u_name in ["-", "미등록", host_upper] and r_u not in ["-", "미등록", host_upper]:
                        u_name = r_u
                    if e_no in ["-", "미등록", ""] and r_e not in ["-", "미등록", ""]:
                        e_no = r_e
                    if d_name in ["-", "미등록", ""] and r_d not in ["-", "미등록", ""]:
                        d_name = r_d

                unified_mapping[host_upper] = {
                    "USER_NAME": u_name or host_upper,
                    "EMP_NO": e_no or "-",
                    "DEPT_NAME": d_name or "미등록",
                    "HOSTNAME": host_upper,
                    "IP": ip_val or "-",
                    "PRIMARY_HOSTNAME": host_upper,
                    "PRIMARY_IP": ip_val or "-"
                }

        # Explicitly guarantee all AutoVT hostnames and IPs in mapping
        autovt_entries = {
            "OCW2112011": "10.82.63.69",
            "OCW2206025": "10.82.79.106",
            "OCW2206063": "10.82.79.107",
            "10.82.79.106": "10.82.79.106",
            "10.82.79.107": "10.82.79.107",
            "10.82.63.69": "10.82.63.69"
        }
        for k, ip_val in autovt_entries.items():
            unified_mapping[k] = {
                "USER_NAME": "AutoVT Server",
                "EMP_NO": "AutoVT Server",
                "DEPT_NAME": "디지털인프라혁신팀",
                "HOSTNAME": k,
                "IP": ip_val
            }

        # Step 3.5: Unify department and user name across multiple hosts owned by the same employee (EMP_NO).
        # Strict Priority Hierarchy:
        # 1) Main CSV (HKT_DSLS_USER_LIST.csv) — all rows with a non-empty 소속그룹, including rows without a hostname
        # 2) Sub CSV (HKT_DSLS_USER_LIST_SUB.csv / equipment_override.json) — if Main CSV has no 소속그룹 for EMP_NO
        # 3) Employee Directory (conf/emp_directory.json) — fallback ONLY when DEPT_NAME is missing ("-", "미등록", "")
        shared_emp_ids = {"-", "미등록", "", "AUTOVT SERVER", "7931", "4758", "57031677"}
        auth_emp_profile: Dict[str, Dict[str, str]] = {}

        # Priority 1A: Main CSV employee profiles (including hostless rows)
        for e_no_key, p_info in self._main_emp_profiles.items():
            e_upper = e_no_key.upper()
            d_name = str(p_info.get("DEPT_NAME", "미등록")).strip()
            u_name = str(p_info.get("USER_NAME", "")).strip()
            p_host = str(p_info.get("PRIMARY_HOSTNAME", "")).strip()
            if (
                e_upper not in shared_emp_ids
                and not e_upper.startswith("SUB")
                and d_name not in ["-", "미등록", ""]
            ):
                auth_emp_profile[e_upper] = {
                    "DEPT_NAME": d_name,
                    "USER_NAME": u_name,
                    "PRIMARY_HOSTNAME": p_host
                }

        # Priority 1B: Main CSV host records with explicit RAW_DEPT and an assigned real user
        for h_key in sorted(main_mapping.keys()):
            if h_key in AUTOVT_HOSTS:
                continue
            if not main_mapping[h_key].get("HAS_REAL_USER", True):
                continue
            m_raw_dept = str(main_mapping[h_key].get("RAW_DEPT", "")).strip()
            if m_raw_dept in ["-", "미등록", ""]:
                continue
            u_info = unified_mapping.get(h_key) or main_mapping[h_key]
            e_upper = str(u_info.get("EMP_NO", "-")).strip().upper()
            u_name = str(u_info.get("USER_NAME", "")).strip()
            if (
                e_upper not in shared_emp_ids
                and not e_upper.startswith("SUB")
            ):
                auth_emp_profile[e_upper] = {
                    "DEPT_NAME": m_raw_dept,
                    "USER_NAME": u_name,
                    "PRIMARY_HOSTNAME": h_key
                }

        # Priority 2: Sub CSV host records (only when Main CSV had no 소속그룹 for this EMP_NO)
        for h_key in sorted(sub_records.keys()):
            if h_key in AUTOVT_HOSTS:
                continue
            s_item = sub_records[h_key]
            s_raw_dept = str(s_item.get("RAW_DEPT") or s_item.get("DEPT_NAME", "")).strip()
            if s_raw_dept in ["-", "미등록", ""]:
                continue
            u_info = unified_mapping.get(h_key) or s_item
            e_upper = str(u_info.get("EMP_NO", "-")).strip().upper()
            u_name = str(u_info.get("USER_NAME", "")).strip()
            if (
                e_upper not in shared_emp_ids
                and not e_upper.startswith("SUB")
                and e_upper not in auth_emp_profile
            ):
                auth_emp_profile[e_upper] = {
                    "DEPT_NAME": s_raw_dept,
                    "USER_NAME": u_name,
                    "PRIMARY_HOSTNAME": h_key
                }

        for h_key in sorted(unified_mapping.keys(), reverse=True):
            if h_key in AUTOVT_HOSTS:
                continue
            h_info = unified_mapping[h_key]
            e_no = str(h_info.get("EMP_NO", "-")).strip()
            e_upper = e_no.upper()
            if e_upper in shared_emp_ids or e_upper.startswith("SUB"):
                continue
            if e_upper in auth_emp_profile:
                auth = auth_emp_profile[e_upper]
                h_info["DEPT_NAME"] = auth["DEPT_NAME"]
                if auth.get("USER_NAME") and auth["USER_NAME"] not in ["-", "미등록", ""]:
                    h_info["USER_NAME"] = auth["USER_NAME"]
                if auth.get("PRIMARY_HOSTNAME"):
                    h_info["PRIMARY_HOSTNAME"] = auth["PRIMARY_HOSTNAME"]
                else:
                    auth["PRIMARY_HOSTNAME"] = h_key
                    h_info["PRIMARY_HOSTNAME"] = h_key
            else:
                cur_dept = str(h_info.get("DEPT_NAME", "미등록")).strip()
                if cur_dept not in ["-", "미등록", ""]:
                    auth_emp_profile[e_upper] = {
                        "DEPT_NAME": cur_dept,
                        "USER_NAME": h_info.get("USER_NAME", ""),
                        "PRIMARY_HOSTNAME": h_key
                    }
                elif e_upper in self._emp_dir:
                    dir_team = self._emp_dir[e_upper].get("TEAM", "").strip()
                    if dir_team and dir_team not in ["-", "미등록", ""]:
                        auth_emp_profile[e_upper] = {
                            "DEPT_NAME": dir_team,
                            "USER_NAME": h_info.get("USER_NAME", ""),
                            "PRIMARY_HOSTNAME": h_key
                        }
                        h_info["DEPT_NAME"] = dir_team

        # Step 4: Enrich missing / unregistered IPs with recently observed intranet IPs from license logs
        try:
            observed_ips = self._extract_observed_ips()
            for h_key, h_info in unified_mapping.items():
                cur_ip = h_info.get("IP", "-")
                if (not cur_ip or cur_ip in ["-", "미등록"]) and h_key in observed_ips:
                    h_info["IP"] = observed_ips[h_key]
                    h_info["PRIMARY_IP"] = observed_ips[h_key]
        except Exception:
            pass

        # Keep _main_ip_map synchronized with the unified profile records
        self._main_ip_map = {
            unified_mapping[h_k]["IP"]: unified_mapping[h_k].copy()
            for h_k in main_mapping.keys()
            if h_k in unified_mapping and unified_mapping[h_k].get("IP") not in ["-", "미등록", "", None]
        }

        self._cache = unified_mapping
        self._loaded_at = now
        self._last_main_mtime = main_mtime
        self._last_sub_mtime = sub_mtime
        return unified_mapping

    def _extract_observed_ips(self) -> Dict[str, str]:
        """
        Extracts recently observed intranet IP addresses per hostname from 1-minute and 15-minute log files.
        Enriches missing/unregistered IP addresses in the master table for consistent site-wide IP resolution.
        """
        import datetime
        from services.config import LIC_LOG_1MIN_DIR, LIC_LOG_DIR

        observed: Dict[str, str] = {}
        year = datetime.datetime.now().year
        log_files = [
            os.path.join(LIC_LOG_1MIN_DIR, f"{year}_USER.dat"),
            os.path.join(LIC_LOG_DIR, f"{year}_USER.dat")
        ]

        for fp in log_files:
            if not os.path.exists(fp):
                continue
            try:
                with open(fp, "r", encoding="utf-8-sig", errors="ignore") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    read_bytes = min(size, 2000000)  # Read last ~2MB for recent active sessions
                    f.seek(size - read_bytes)
                    if read_bytes < size:
                        f.readline()  # Discard partial line
                    for line in f:
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 3 and "/" in parts[2]:
                            h, ip = parts[2].split("/", 1)
                            h = h.strip().upper()
                            ip = ip.strip()
                            if (
                                h
                                and ip
                                and h not in ["HOST", "HOSTNAME"]
                                and not h.startswith("TEST-HOST-")
                                and ip.upper() not in ["-", "미등록", "", "IP"]
                            ):
                                observed[h] = ip
            except Exception:
                continue

        # If live logs were read, persist to local snapshot for resilience
        if observed:
            try:
                os.makedirs(os.path.dirname(OBSERVED_IPS_CACHE_PATH), exist_ok=True)
                with open(OBSERVED_IPS_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(observed, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            return observed

        # Fallback to local persistent snapshot if live logs unreachable
        if os.path.exists(OBSERVED_IPS_CACHE_PATH):
            try:
                with open(OBSERVED_IPS_CACHE_PATH, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    if cached:
                        return cached
            except Exception:
                pass

        return {}

    def get_user_info(self, hostname: str, ip: str = "-") -> Dict[str, str]:
        """
        Authoritative user resolution hierarchy:
        1. AutoVT Server explicit mapping
        2. Unified Master by Hostname (authoritative, enriched with SUB verified users)
        3. Main CSV by IP Address (if IP belongs to a registered real person in Main)
        4. Unified Master by IP
        5. Default unregistered fallback
        """
        master = self.get_user_master()
        host_clean = hostname.strip().upper() if hostname else ""
        ip_clean = ip.strip() if ip else "-"

        # 1. AutoVT Server explicit check
        if host_clean in AUTOVT_HOSTS or ip_clean in ["10.82.79.106", "10.82.79.107", "10.82.63.69"]:
            return {
                "USER_NAME": "AutoVT Server",
                "EMP_NO": "AutoVT Server",
                "DEPT_NAME": "디지털인프라혁신팀",
                "HOSTNAME": host_clean or ip_clean,
                "IP": ip_clean if ip_clean != "-" else ("10.82.79.106" if host_clean == "OCW2206025" else ("10.82.79.107" if host_clean == "OCW2206063" else "10.82.63.69"))
            }

        # 2. Unified Master by Hostname (contains Main CSV priority + verified SUB enrichment)
        if host_clean and host_clean in master:
            entry = master[host_clean]
            u_name = entry.get("USER_NAME", "")
            if u_name and u_name not in ["미등록", host_clean, "-", ""]:
                return entry

        # 3. Main CSV by IP Address (if IP belongs to a valid real user in Main)
        if hasattr(self, "_main_ip_map") and ip_clean and ip_clean not in ["-", "미등록", ""] and ip_clean in self._main_ip_map:
            m_entry = self._main_ip_map[ip_clean]
            m_host = m_entry.get("HOSTNAME", "").upper()
            if m_host in master:
                return master[m_host]
            u_name = m_entry.get("USER_NAME", "")
            if u_name and u_name not in ["미등록", m_host, "-", ""]:
                return m_entry

        # 4. Unified master by Hostname fallback
        if host_clean and host_clean in master:
            return master[host_clean]

        # 5. Unified master by IP fallback
        if ip_clean and ip_clean not in ["-", "미등록", ""] and ip_clean in master:
            return master[ip_clean]

        # 6. Default unregistered fallback
        fallback_ip = ip_clean if ip_clean not in ["-", "미등록", ""] else "-"
        if fallback_ip == "-" and host_clean:
            try:
                obs = self._extract_observed_ips()
                if host_clean in obs:
                    fallback_ip = obs[host_clean]
            except Exception:
                pass

        return {
            "USER_NAME": host_clean or "미등록",
            "EMP_NO": "-",
            "DEPT_NAME": "미등록",
            "HOSTNAME": host_clean,
            "IP": fallback_ip
        }

    def get_canonical_user_key(self, hostname: str, m_info: Dict[str, str]) -> str:
        """
        Single Source of Truth for deduplicating active users across all dashboard services.
        - Shared equipment / AutoVT Server hosts are separated per hostname (HOST_{hostname}).
        - Real employees with a valid personal EMP_NO are unified by EMP_{emp_no}.
        - Users without EMP_NO but with a registered name are unified by USER_{name}_{dept}.
        - Unregistered hosts fall back to HOST_{hostname}.
        """
        h_clean = hostname.strip().upper() if hostname else ""
        u_name = str(m_info.get("USER_NAME", "미등록")).strip()
        e_no = str(m_info.get("EMP_NO", "-")).strip()
        d_name = str(m_info.get("DEPT_NAME", "미등록")).strip()

        is_shared_or_server = (
            h_clean in AUTOVT_HOSTS
            or u_name in ["AutoVT Server", "공용 PC", "공용PC", "DSP공용장비"]
            or e_no in ["AutoVT Server", "7931", "4758", "57031677"]
            or "AUTOVT" in h_clean
            or "공용" in u_name
            or "공용" in d_name
            or "SERVER" in u_name.upper()
        )
        if is_shared_or_server:
            return f"HOST_{h_clean}"

        is_valid_emp = bool(
            e_no
            and e_no not in ["-", "미등록", ""]
            and not e_no.startswith("Sub")
        )
        if is_valid_emp:
            return f"EMP_{e_no.upper()}"
        if u_name and u_name not in ["미등록", "-", h_clean, ""]:
            return f"USER_{u_name}_{d_name}"
        return f"HOST_{h_clean}"

    def get_departments(self) -> List[str]:
        master = self.get_user_master()
        depts = sorted(list(set(v.get("DEPT_NAME", "") for v in master.values() if v.get("DEPT_NAME") and v.get("DEPT_NAME") != "미등록")))
        return depts

# Singleton instance
user_master_service = UserMasterService()
