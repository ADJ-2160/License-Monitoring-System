# -*- coding: utf-8 -*-
"""
Configuration & System Constants for CATIA License Monitoring System
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_DIR = os.path.join(BASE_DIR, "conf")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# DSLS Network Storage & Log Paths
LIC_LOG_DIR = r"H:\DSLS_CHK\HKT_DSLS_LIC_LOG"
LIC_LOG_1MIN_DIR = r"H:\DSLS_CHK\HKT_DSLS_USER_1MIN"
USER_MASTER_PATH = r"H:\DSLS_CHK\HKT_DSLS_USER\HKT_DSLS_USER_LIST.csv"
USER_MASTER_SUB_PATH = r"H:\DSLS_CHK\HKT_DSLS_USER\HKT_DSLS_USER_LIST_SUB.csv"
PROCTRACE_LOG_DIR = r"H:\DSLS_CHK\HKT_PROCTRACE_LOG"
EQUIPMENT_OVERRIDE_CACHE_PATH = os.path.join(CONF_DIR, "equipment_override.json")
EMP_DIRECTORY_PATH = os.path.join(CONF_DIR, "emp_directory.json")
OBSERVED_IPS_CACHE_PATH = os.path.join(CONF_DIR, "observed_ips_cache.json")

# License Limits & Relative Thresholds (Single Source of Truth)
TOTAL_MD2_LIMIT = 51

# Relative Buffer Settings
# Critical / Saturation threshold: total - 1 (e.g. 51 -> 50, 60 -> 59)
CRITICAL_BUFFER = 1
# Warning / High Congestion threshold: total - 6 (e.g. 51 -> 45, 60 -> 54)
WARNING_BUFFER = 6

CRITICAL_THRESHOLD = TOTAL_MD2_LIMIT - CRITICAL_BUFFER
WARNING_THRESHOLD = TOTAL_MD2_LIMIT - WARNING_BUFFER

# Peak Warning Threshold (aligned with warning threshold)
PEAK_WARNING_THRESHOLD = WARNING_THRESHOLD

# License Module Configuration
MODULE_CONFIG = {
    "MD2": {
        "name": "CATIA V5 Mechanical Design 2",
        "limit": TOTAL_MD2_LIMIT,
        "warning_threshold": WARNING_THRESHOLD,
        "critical_threshold": CRITICAL_THRESHOLD,
        "icon": "fa-solid fa-cube",
        "color": "#38bdf8"
    }
}

# 08:00 ~ 17:00 15-Minute Working Hours Time Slots (37 total)
WORK_HOURS_SLOTS_15M = [
    "08:00", "08:15", "08:30", "08:45",
    "09:00", "09:15", "09:30", "09:45",
    "10:00", "10:15", "10:30", "10:45",
    "11:00", "11:15", "11:30", "11:45",
    "12:00", "12:15", "12:30", "12:45",
    "13:00", "13:15", "13:30", "13:45",
    "14:00", "14:15", "14:30", "14:45",
    "15:00", "15:15", "15:30", "15:45",
    "16:00", "16:15", "16:30", "16:45",
    "17:00"
]

# Lunch break slots between 12:00 and 13:00 (excluded from 50+ slot ratio calculations)
LUNCH_HOURS_SLOTS_15M = ["12:15", "12:30", "12:45"]

# Cache TTL Settings (in seconds)
CACHE_TTL_REALTIME = 10       # 10 seconds for real-time status
CACHE_TTL_PROCTRACE = 15      # 15 seconds for proc trace anomaly monitoring
CACHE_TTL_STATS = 600         # 10 minutes for multi-date stats & pivots
CACHE_TTL_USER_MASTER = 3600  # 1 hour for user master data
