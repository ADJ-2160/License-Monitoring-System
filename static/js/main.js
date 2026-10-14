/**
 * CATIA MD2 License Monitoring System - Master Client Application
 * Modular, decoupled architecture with pure data-driven rendering and zero side effects.
 * 
 * Modules:
 *   1. Utils (Pure helpers for date, time slots, HTML escaping)
 *   2. DateRangeController (Independent date range synchronizer per section)
 *   3. RealtimeModule (Top KPI, Benchmark Sparkline, License Modules)
 *   4. PivotModule (Accordion 1: Summary Matrix, 15m Daily Slots, Dept Summary)
 *   5. AnomalyModule (Accordion 2: Continuous 8h+ Occupancy Monitor)
 *   6. StatsModule (Accordion 3: 08:00~17:00 15m Chart & Tables, Dept Donut, Insights)
 *   7. RequestsModule (Accordion 4: DSLS Denial Requests History)
 *   8. App (Master Coordinator & Polling Engine)
 */

// =========================================================================
// 1. Pure Utility Functions
// =========================================================================
const Utils = {
    parseDateIso(dateStr) {
        if (!dateStr) return new Date();
        const parts = dateStr.split('-');
        if (parts.length === 3) {
            return new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
        }
        return new Date(dateStr);
    },

    formatDateIso(d) {
        const yyyy = d.getFullYear();
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        return `${yyyy}-${mm}-${dd}`;
    },

    addDaysIso(dateStr, days) {
        const d = this.parseDateIso(dateStr);
        d.setDate(d.getDate() + days);
        return this.formatDateIso(d);
    },

    getTodayIsoString() {
        return this.formatDateIso(new Date());
    },

    escapeHtml(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    },

    debounce(func, wait = 150) {
        let timeout;
        return function(...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    },

    isDateRangeIncludingToday(startDate, endDate, availableDates = []) {
        if (!startDate || !endDate) return true;
        const curToday = this.getTodayIsoString();
        const sysToday = (availableDates && availableDates.length > 0) ? availableDates[0] : curToday;

        const includesCur = (startDate <= curToday && curToday <= endDate);
        const includesSys = (startDate <= sysToday && sysToday <= endDate);
        return includesCur || includesSys;
    }
};

// =========================================================================
// 2. Date Range Controller (Modularized per Accordion)
// =========================================================================
class DateRangeController {
    constructor(startInputId, endInputId, presetSelectId, onSearchCallback, options = {}) {
        this.startInput = document.getElementById(startInputId);
        this.endInput = document.getElementById(endInputId);
        this.presetSelect = document.getElementById(presetSelectId);
        this.onSearchCallback = onSearchCallback;
        this.options = Object.assign({
            maxDate: Utils.getTodayIsoString() // Default: never allow future dates (> today)
        }, options || {});
        this.debounceTimer = null;
        this.startFp = null;
        this.endFp = null;
        this.isUpdatingDates = false;
        this.init();
    }

    init() {
        if (!this.startInput || !this.endInput || !this.presetSelect) return;

        this.initFlatpickr();

        if (this.options.maxDate) {
            this.setMaxDate(this.options.maxDate);
        }

        // 'change' 및 'input' 이벤트 바인딩
        this.startInput.addEventListener('change', () => this.handleStartChange());
        this.startInput.addEventListener('input', () => this.handleStartChange());
        this.endInput.addEventListener('change', () => this.handleEndChange());
        this.endInput.addEventListener('input', () => this.handleEndChange());
        this.presetSelect.addEventListener('change', () => this.handlePresetChange());
    }

    getDateEnableFilter() {
        return (date) => {
            // 1. 주말 제외 (일:0, 토:6) - 달력에서 자동 Dimmed 및 클릭 차단
            const day = date.getDay();
            if (day === 0 || day === 6) return false;

            const iso = Utils.formatDateIso(date);

            // 2. maxDate 초과 제외 (미래일 및 당일 제외 조건)
            if (this.options.maxDate && iso > this.options.maxDate) return false;

            // 3. 실제 로그 데이터가 존재하는 가용 날짜 목록(Available Dates) 검증
            const avail = (App && App.availableDates && App.availableDates.length > 1)
                ? App.availableDates
                : (window.AVAILABLE_DATES && window.AVAILABLE_DATES.length > 1 ? window.AVAILABLE_DATES : null);

            if (avail && avail.length > 1) {
                return avail.includes(iso);
            }

            return true;
        };
    }

    isDateSelectable(dateIso) {
        if (!dateIso) return false;
        const d = Utils.parseDateIso(dateIso);
        if (!d || isNaN(d.getTime())) return false;
        const filter = this.getDateEnableFilter();
        return filter(d);
    }

    getNextValidSelectableDate(targetDateIso, maxLimitIso) {
        if (!targetDateIso) return targetDateIso;
        let cur = targetDateIso;
        const maxLimit = maxLimitIso || this.options.maxDate || Utils.getTodayIsoString();

        // 1. 이미 선택 가능한 유효한 날짜라면 그대로 반환
        if (this.isDateSelectable(cur)) {
            return cur;
        }

        // 2. 선택할 수 없는 날짜인 경우, 그 날짜보다 '이후의 날짜(+1일씩)' 중 가장 먼저 나오는 유효한 날짜 선택
        // (예: 토요일이나 일요일인 경우 -> 다음 주 월요일 선택)
        let safetyCount = 0;
        while (cur <= maxLimit && safetyCount < 30) {
            cur = Utils.addDaysIso(cur, 1);
            safetyCount++;
            if (this.isDateSelectable(cur)) {
                return cur;
            }
        }

        // 3. 만약 maxLimit에 도달했거나 탐색하지 못한 경우 토/일요일 기본 스냅 규칙 적용
        const d = Utils.parseDateIso(targetDateIso);
        if (d && !isNaN(d.getTime())) {
            const day = d.getDay();
            if (day === 6) { // 토요일 -> +2일 (다음 주 월요일)
                const nextMon = Utils.addDaysIso(targetDateIso, 2);
                return (maxLimit && nextMon > maxLimit) ? targetDateIso : nextMon;
            } else if (day === 0) { // 일요일 -> +1일 (다음 주 월요일)
                const nextMon = Utils.addDaysIso(targetDateIso, 1);
                return (maxLimit && nextMon > maxLimit) ? targetDateIso : nextMon;
            }
        }

        return cur <= maxLimit ? cur : maxLimit;
    }

    getSelectableYears(fp) {
        const yearsSet = new Set();
        const maxYear = this.options.maxDate ? this.options.maxDate.slice(0, 4) : null;
        if (App.availableDates && App.availableDates.length > 0) {
            App.availableDates.forEach(d => {
                if (typeof d === 'string' && d.length >= 4) {
                    const y = d.slice(0, 4);
                    if (!maxYear || y <= maxYear) {
                        yearsSet.add(y);
                    }
                }
            });
        }
        if (fp && fp.currentYear) {
            const curY = String(fp.currentYear);
            if (!maxYear || curY <= maxYear) {
                yearsSet.add(curY);
            }
        }
        if (yearsSet.size === 0) {
            yearsSet.add(String(new Date().getFullYear()));
        }
        return Array.from(yearsSet).sort((a, b) => Number(b) - Number(a));
    }

    handleYearDropdownChange(fp, targetYear) {
        if (!fp || !targetYear) return;
        const yStr = String(targetYear);
        const maxDate = this.options.maxDate || null;
        const yearDates = (App.availableDates || []).filter(d => (
            typeof d === 'string' && d.startsWith(`${yStr}-`) && (!maxDate || d <= maxDate)
        ));

        if (yearDates.length > 0) {
            const curMonthNum = fp.currentMonth + 1;
            const curMonthStr = String(curMonthNum).padStart(2, '0');
            const sameMonthDate = yearDates.find(d => d.startsWith(`${yStr}-${curMonthStr}-`));
            if (sameMonthDate) {
                fp.jumpToDate(sameMonthDate);
            } else {
                const sortedAsc = [...yearDates].sort();
                const firstMonth = parseInt(sortedAsc[0].slice(5, 7), 10);
                const targetDate = (curMonthNum < firstMonth) ? sortedAsc[0] : sortedAsc[sortedAsc.length - 1];
                fp.jumpToDate(targetDate);
            }
        } else {
            fp.changeYear(parseInt(yStr, 10));
        }
        this.syncYearDropdown(fp);
    }

    syncYearDropdown(fp) {
        if (!fp || !fp.calendarContainer) return;
        const curMonthContainer = fp.calendarContainer.querySelector('.flatpickr-current-month');
        if (!curMonthContainer) return;

        const numWrap = curMonthContainer.querySelector('.numInputWrapper');
        if (numWrap) {
            numWrap.style.display = 'none';
        }

        let yearSelect = curMonthContainer.querySelector('.flatpickr-yearDropdown-years');
        if (!yearSelect) {
            yearSelect = document.createElement('select');
            yearSelect.className = 'flatpickr-monthDropdown-months flatpickr-yearDropdown-years';
            yearSelect.setAttribute('aria-label', '연도');
            yearSelect.addEventListener('change', (e) => {
                e.stopPropagation();
                this.handleYearDropdownChange(fp, e.target.value);
            });
            curMonthContainer.insertBefore(yearSelect, curMonthContainer.firstChild);
        }

        const years = this.getSelectableYears(fp);
        const currentYearStr = String(fp.currentYear);
        const existingOpts = Array.from(yearSelect.options).map(o => o.value).join(',');
        if (existingOpts !== years.join(',')) {
            yearSelect.innerHTML = years.map(y => `<option class="flatpickr-monthDropdown-month" value="${y}">${y}년</option>`).join('');
        }
        if (years.includes(currentYearStr)) {
            yearSelect.value = currentYearStr;
        }
    }

    initFlatpickr() {
        if (typeof flatpickr === 'undefined') return;

        const locale = (flatpickr.l10ns && flatpickr.l10ns.ko) ? flatpickr.l10ns.ko : 'default';
        const filter = this.getDateEnableFilter();

        const commonConfig = {
            locale: locale,
            dateFormat: 'Y-m-d',
            disableMobile: true,
            allowInput: false,
            enable: [filter],
            onReady: (selectedDates, dateStr, fp) => {
                this.syncYearDropdown(fp);
            },
            onOpen: (selectedDates, dateStr, fp) => {
                this.syncYearDropdown(fp);
            },
            onYearChange: (selectedDates, dateStr, fp) => {
                this.syncYearDropdown(fp);
            },
            onMonthChange: (selectedDates, dateStr, fp) => {
                this.syncYearDropdown(fp);
            }
        };

        if (this.startInput) {
            this.startFp = flatpickr(this.startInput, Object.assign({}, commonConfig, {
                defaultDate: this.startInput.value || null,
                maxDate: this.options.maxDate || null,
                onChange: (selectedDates, dateStr) => {
                    if (dateStr) {
                        this.startInput.value = dateStr;
                        this.handleStartChange();
                    }
                }
            }));
        }

        if (this.endInput) {
            this.endFp = flatpickr(this.endInput, Object.assign({}, commonConfig, {
                defaultDate: this.endInput.value || null,
                maxDate: this.options.maxDate || null,
                onChange: (selectedDates, dateStr) => {
                    if (dateStr) {
                        this.endInput.value = dateStr;
                        this.handleEndChange();
                    }
                }
            }));
        }
    }

    refreshFlatpickr() {
        const filter = this.getDateEnableFilter();
        if (this.startFp) {
            this.startFp.set('enable', [filter]);
            if (this.options.maxDate) this.startFp.set('maxDate', this.options.maxDate);
            if (this.startInput && this.startInput.value) {
                const validStart = this.getNextValidSelectableDate(this.startInput.value, this.options.maxDate);
                this.startInput.value = validStart;
                this.startFp.setDate(validStart, false);
            }
            this.syncYearDropdown(this.startFp);
        }
        if (this.endFp) {
            this.endFp.set('enable', [filter]);
            if (this.options.maxDate) this.endFp.set('maxDate', this.options.maxDate);
            if (this.endInput && this.endInput.value) {
                const validEnd = this.getNextValidSelectableDate(this.endInput.value, this.options.maxDate);
                this.endInput.value = validEnd;
                this.endFp.setDate(validEnd, false);
            }
            this.syncYearDropdown(this.endFp);
        }
    }

    setMaxDate(maxDate) {
        this.options.maxDate = maxDate;
        if (this.startInput) {
            this.startInput.max = maxDate;
            this.startInput.setAttribute('max', maxDate);
            if (this.startInput.value && this.startInput.value > maxDate) {
                this.startInput.value = maxDate;
            }
        }
        if (this.endInput) {
            this.endInput.max = maxDate;
            this.endInput.setAttribute('max', maxDate);
            if (this.endInput.value && this.endInput.value > maxDate) {
                this.endInput.value = maxDate;
            }
        }
        if (this.startFp) {
            this.startFp.set('maxDate', maxDate);
            this.syncYearDropdown(this.startFp);
        }
        if (this.endFp) {
            this.endFp.set('maxDate', maxDate);
            this.syncYearDropdown(this.endFp);
        }
    }

    triggerSearchDebounced(delay = 150) {
        if (this.debounceTimer) {
            clearTimeout(this.debounceTimer);
        }
        this.debounceTimer = setTimeout(() => {
            if (this.onSearchCallback) {
                this.onSearchCallback();
            }
        }, delay);
    }

    handleStartChange() {
        if (this.isUpdatingDates) return;
        this.isUpdatingDates = true;
        try {
            let sVal = this.startInput.value;
            let eVal = this.endInput.value;
            if (!sVal) return;

            if (this.options.maxDate && sVal > this.options.maxDate) {
                sVal = this.options.maxDate;
                this.startInput.value = sVal;
                if (this.startFp) this.startFp.setDate(sVal, false);
            }

            const preset = this.presetSelect ? this.presetSelect.value : 'custom';

            if (preset !== 'custom') {
                const span = this.getPresetDays(preset);
                if (span !== null) {
                    let calculatedEnd = Utils.addDaysIso(sVal, span);
                    // 종료일이 선택 불가능한 날짜(주말 등)인 경우 이후의 가장 빠른 유효일로 보정
                    calculatedEnd = this.getNextValidSelectableDate(calculatedEnd, this.options.maxDate);

                    // 종료일이 maxDate를 초과하는 경우 maxDate로 맞추고 시작일도 역산 보정
                    if (this.options.maxDate && calculatedEnd > this.options.maxDate) {
                        calculatedEnd = this.options.maxDate;
                        calculatedEnd = this.getNextValidSelectableDate(calculatedEnd, this.options.maxDate);
                        sVal = Utils.addDaysIso(calculatedEnd, -span);
                        sVal = this.getNextValidSelectableDate(sVal, calculatedEnd);
                        this.startInput.value = sVal;
                        if (this.startFp) this.startFp.setDate(sVal, false);
                    }
                    this.endInput.value = calculatedEnd;
                    if (this.endFp) this.endFp.setDate(calculatedEnd, false);
                }
            } else {
                // 직접 지정(custom)인 경우: 두 날짜를 독립적으로 개별 컨트롤
                if (eVal && sVal > eVal) {
                    this.endInput.value = sVal;
                    if (this.endFp) this.endFp.setDate(sVal, false);
                }
            }

            this.triggerSearchDebounced();
        } finally {
            this.isUpdatingDates = false;
        }
    }

    handleEndChange() {
        if (this.isUpdatingDates) return;
        this.isUpdatingDates = true;
        try {
            let sVal = this.startInput.value;
            let eVal = this.endInput.value;
            if (!eVal) return;

            if (this.options.maxDate && eVal > this.options.maxDate) {
                eVal = this.options.maxDate;
                this.endInput.value = eVal;
                if (this.endFp) this.endFp.setDate(eVal, false);
            }

            const preset = this.presetSelect ? this.presetSelect.value : 'custom';

            if (preset !== 'custom') {
                const span = this.getPresetDays(preset);
                if (span !== null) {
                    const rawStart = Utils.addDaysIso(eVal, -span);
                    // 선택할 수 없는 날짜(토요일/일요일 등)가 들어오면 그것보다 이후의 날짜 중에 가장 최신(다음 주 월요일)으로 선택
                    const calculatedStart = this.getNextValidSelectableDate(rawStart, eVal);
                    this.startInput.value = calculatedStart;
                    if (this.startFp) this.startFp.setDate(calculatedStart, false);
                }
            } else {
                // 직접 지정(custom)인 경우: 두 날짜를 독립적으로 개별 컨트롤
                if (sVal && sVal > eVal) {
                    this.startInput.value = eVal;
                    if (this.startFp) this.startFp.setDate(eVal, false);
                }
            }

            this.triggerSearchDebounced();
        } finally {
            this.isUpdatingDates = false;
        }
    }

    handlePresetChange() {
        const preset = this.presetSelect.value;
        if (preset === 'custom') return;

        let latestDate;
        if (typeof this.options.getBaseLatestDate === 'function') {
            latestDate = this.options.getBaseLatestDate();
        } else if (this.options.maxDate) {
            latestDate = this.options.maxDate;
        } else {
            latestDate = App.availableDates[0] || Utils.getTodayIsoString();
        }

        if (this.options.maxDate && latestDate > this.options.maxDate) {
            latestDate = this.options.maxDate;
        }

        if (preset === '1') {
            this.startInput.value = latestDate;
            this.endInput.value = latestDate;
        } else if (preset === '3') {
            this.endInput.value = latestDate;
            this.startInput.value = this.getNextValidSelectableDate(Utils.addDaysIso(latestDate, -2), latestDate);
        } else if (preset === '7') {
            this.endInput.value = latestDate;
            this.startInput.value = this.getNextValidSelectableDate(Utils.addDaysIso(latestDate, -6), latestDate);
        } else if (preset === '14') {
            this.endInput.value = latestDate;
            this.startInput.value = this.getNextValidSelectableDate(Utils.addDaysIso(latestDate, -13), latestDate);
        } else if (preset === '30') {
            this.endInput.value = latestDate;
            this.startInput.value = this.getNextValidSelectableDate(Utils.addDaysIso(latestDate, -29), latestDate);
        }

        if (this.startFp) this.startFp.setDate(this.startInput.value, false);
        if (this.endFp) this.endFp.setDate(this.endInput.value, false);

        this.triggerSearchDebounced(50);
    }

    getPresetDays(preset) {
        if (!preset || preset === 'custom') return null;
        const num = parseInt(preset, 10);
        if (!isNaN(num) && num >= 1) {
            return num - 1;
        }
        return null;
    }

    getStartDate() {
        return this.startInput ? this.startInput.value : '';
    }

    getEndDate() {
        return this.endInput ? this.endInput.value : '';
    }

    setDates(startDate, endDate, preset = '1') {
        if (this.options.maxDate) {
            if (startDate && startDate > this.options.maxDate) {
                startDate = this.options.maxDate;
            }
            if (endDate && endDate > this.options.maxDate) {
                endDate = this.options.maxDate;
            }
        }
        if (startDate && endDate && startDate > endDate) {
            startDate = endDate;
        }
        if (this.startInput) {
            this.startInput.value = startDate;
            if (this.options.maxDate) {
                this.startInput.max = this.options.maxDate;
                this.startInput.setAttribute('max', this.options.maxDate);
            }
        }
        if (this.endInput) {
            this.endInput.value = endDate;
            if (this.options.maxDate) {
                this.endInput.max = this.options.maxDate;
                this.endInput.setAttribute('max', this.options.maxDate);
            }
        }
        if (this.startFp) {
            this.startFp.setDate(startDate, false);
        }
        if (this.endFp) {
            this.endFp.setDate(endDate, false);
        }
        if (this.presetSelect) this.presetSelect.value = preset;
    }
}

// =========================================================================
// 3. Realtime Dashboard Module (Top Overview & Snapshot)
// =========================================================================
const RealtimeModule = {
    sparklineChart: null,
    activeUsers: [],
    closedModuleAccordions: new Set(), // Default: all expanded (tracks explicitly collapsed modules)
    prevUsed: null,

    toggleModuleAccordion(code) {
        if (this.closedModuleAccordions.has(code)) {
            this.closedModuleAccordions.delete(code);
        } else {
            this.closedModuleAccordions.add(code);
        }
        const isOpen = !this.closedModuleAccordions.has(code);
        const bodyEl = document.getElementById(`module-body-${code}`);
        const chevronEl = document.getElementById(`chevron-${code}`);
        if (bodyEl) {
            bodyEl.style.display = isOpen ? 'flex' : 'none';
            const cardEl = bodyEl.closest('.module-accordion-card');
            if (cardEl) {
                cardEl.classList.toggle('collapsed', !isOpen);
            }
        }
        if (chevronEl) {
            chevronEl.classList.toggle('open', isOpen);
        }

        // 실시간 모듈 아코디언 개폐 시 우측 점유 추이 Sparkline 차트 리사이즈
        if (this.sparklineChart) {
            requestAnimationFrame(() => {
                try {
                    this.sparklineChart.resize();
                } catch (e) {}
            });
            setTimeout(() => {
                try {
                    this.sparklineChart.resize();
                } catch (e) {}
            }, 100);
        }
    },

    async load() {
        try {
            const res = await fetch(`/api/v1/license/realtime?_t=${Date.now()}`, { cache: 'no-store' });
            const data = await res.json();
            this.renderTopOverview(data);
            this.renderBenchmarkSparkline(data);
            this.activeUsers = data.active_users || [];
        } catch (err) {
            console.error('RealtimeModule load error:', err);
        }
    },

    renderTopOverview(data) {
        const total = data.total || 51;
        const used = data.used || 0;
        const avail = data.available !== undefined ? data.available : (total - used);
        const rate = data.rate_pct || 0;
        const critThresh = data.critical_threshold !== undefined ? data.critical_threshold : (total - 1);
        const warnThresh = data.warning_threshold !== undefined ? data.warning_threshold : (total - 6);

        const kpiUsedText = document.getElementById('kpiUsedText');
        const kpiAvailableText = document.getElementById('kpiAvailableText');
        const kpiRateText = document.getElementById('kpiRateText');
        const lastUpdatedTime = document.getElementById('lastUpdatedTime');
        const licModuleContainer = document.getElementById('licModuleContainer');

        if (kpiUsedText) kpiUsedText.textContent = `${used} / ${total}`;
        if (kpiAvailableText) kpiAvailableText.textContent = `${avail}개`;
        if (kpiRateText) kpiRateText.textContent = `${rate}%`;

        const headerLiveOccupancy = document.getElementById('headerLiveOccupancy');
        if (headerLiveOccupancy) {
            headerLiveOccupancy.textContent = `${used} / ${total} (${rate}%)`;
            headerLiveOccupancy.classList.remove('occ-peak-50', 'occ-warning-45', 'occ-normal');
            if (used >= critThresh) {
                headerLiveOccupancy.classList.add('occ-peak-50');
            } else if (used >= warnThresh) {
                headerLiveOccupancy.classList.add('occ-warning-45');
            } else {
                headerLiveOccupancy.classList.add('occ-normal');
            }
        }

        // 실시간 점유율 수치 변동 감지 시 시각적 하이라이트 플래시 피드백
        if (this.prevUsed !== null && this.prevUsed !== used) {
            const triggerFlash = (el) => {
                if (!el) return;
                el.classList.remove('metric-flash');
                void el.offsetWidth;
                el.classList.add('metric-flash');
            };
            triggerFlash(headerLiveOccupancy);
            triggerFlash(kpiUsedText);
        }
        this.prevUsed = used;

        const sidebarServerTime = document.getElementById('sidebarServerTime');
        if (sidebarServerTime && (data.updated_at || data.snapshot_time)) {
            sidebarServerTime.textContent = data.updated_at || `${data.target_date} ${data.snapshot_time}`;
        }

        if (lastUpdatedTime) {
            lastUpdatedTime.textContent = `${data.target_date || ''} ${data.snapshot_time || ''} (1분 실시간 갱신: ${data.updated_at || ''})`;
        }

        if (licModuleContainer) {
            const modules = (data.modules && data.modules.length > 0) ? data.modules : [
                {
                    module_code: 'MD2',
                    module_name: 'CATIA V5 Mechanical Design 2',
                    icon: 'fa-solid fa-cube',
                    color: '#38bdf8',
                    total: total,
                    used: used,
                    available: avail,
                    rate_pct: rate,
                    status_level: used >= total ? 'CRITICAL' : (used >= warnThresh ? 'WARNING' : 'NORMAL'),
                    status_label: used >= total ? '포화 (만석 0석)' : (avail <= 2 ? `혼잡 (잔여 ${avail}석)` : `즉시 접속 가능 (잔여 ${avail}석)`),
                    badge_class: used >= total ? 'badge-danger' : (avail <= 2 ? 'badge-warning' : 'badge-success'),
                    users: data.active_users || []
                }
            ];

            const html = `
                <div class="module-accordion-list">
                    ${modules.map(m => {
                        const isOpen = !this.closedModuleAccordions.has(m.module_code);
                        const fillClass = m.used >= m.total ? 'fill-red' : (m.rate_pct >= 80 ? 'fill-yellow' : 'fill-blue');
                        const statusBadgeClass = m.used >= m.total ? 'badge-danger' : (m.rate_pct >= 80 ? 'badge-warning' : 'badge-light');
                        const usersCount = (m.users && m.users.length) || 0;

                        return `
                            <div class="module-accordion-card ${isOpen ? '' : 'collapsed'}" data-module="${m.module_code}">
                                <div class="module-accordion-header" onclick="RealtimeModule.toggleModuleAccordion('${m.module_code}')" title="클릭하여 실시간 점유자 명단 확인">
                                    <div class="module-header-left">
                                        <span class="module-title">
                                            <i class="${m.icon || 'fa-solid fa-cube'}" style="color: ${m.color || '#38bdf8'}; font-size: 1.1rem;"></i>
                                            <strong>${m.module_code}</strong>
                                            <span class="module-fullname">(${m.module_name})</span>
                                        </span>
                                    </div>
                                    <div class="module-header-center">
                                        <div class="module-progress-wrapper">
                                            <div class="module-progress-info font-mono">
                                                <span><strong>${m.used}</strong> / ${m.total}개 (${m.rate_pct}%)</span>
                                                <span class="badge ${statusBadgeClass} font-bold">${m.rate_pct}%</span>
                                            </div>
                                            <div class="progress-track">
                                                <div class="progress-fill ${fillClass}" style="width: ${Math.min(100, m.rate_pct)}%;"></div>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="module-header-right">
                                        <span class="badge ${m.badge_class || 'badge-light'} font-bold">
                                            <i class="fa-solid fa-circle-dot mr-1"></i> ${m.status_label || (m.available > 0 ? `잔여 ${m.available}석` : '만석')}
                                        </span>
                                        <button class="btn-module-toggle" type="button">
                                            <i class="fa-solid fa-users text-muted mr-1"></i> 명단 (<strong class="text-primary">${usersCount}명</strong>)
                                            <i class="fa-solid fa-chevron-down module-chevron-icon ml-1 ${isOpen ? 'open' : ''}" id="chevron-${m.module_code}"></i>
                                        </button>
                                    </div>
                                </div>
                                <div class="module-accordion-body" id="module-body-${m.module_code}" style="display: ${isOpen ? 'flex' : 'none'};">
                                    <div class="module-users-box">
                                        <div class="module-users-header">
                                            <span class="users-title">
                                                <i class="fa-solid fa-list-check text-primary"></i> 
                                                <strong>${m.module_code}</strong> 실시간 점유자 명단 
                                                <span class="text-muted" style="font-size: 0.8rem; font-weight: normal;">(최신 1분 실측: ${data.snapshot_time || '--:--'})</span>
                                            </span>
                                            <span class="badge ${m.available > 0 ? 'badge-success' : 'badge-danger'}">
                                                ${m.available > 0 ? `🟢 잔여 ${m.available}개 (즉시 접속 가능)` : '🔴 잔여 없음 (모두 점유 중)'}
                                            </span>
                                        </div>
                                        ${usersCount > 0 ? `
                                            <div class="table-responsive module-users-table-wrapper">
                                                <table class="data-table module-users-table">
                                                    <thead>
                                                        <tr>
                                                            <th style="width: 45px;" class="text-center">No</th>
                                                            <th>사용자명</th>
                                                            <th>부서명</th>
                                                            <th>사번</th>
                                                            <th>장비명</th>
                                                            <th>IP</th>
                                                            <th class="text-center">점유확인시각</th>
                                                            <th class="text-center" title="현재 연속 점유가 시작된 시각 (라이선스 재연결 시 갱신)">점유시작시각</th>
                                                            <th class="text-center">당일누적가동</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        ${m.users.map((u, idx) => `
                                                            <tr>
                                                                <td class="text-center font-mono text-muted">${idx + 1}</td>
                                                                <td><strong>${Utils.escapeHtml(u.USER_NAME)}</strong></td>
                                                                <td><span class="badge badge-light">${Utils.escapeHtml(u.DEPT_NAME)}</span></td>
                                                                <td class="font-mono text-muted">${Utils.escapeHtml(u.EMP_NO)}</td>
                                                                <td class="font-mono">${Utils.escapeHtml(u.HOSTNAME)}</td>
                                                                <td class="font-mono text-muted">${Utils.escapeHtml(u.IP)}</td>
                                                                <td class="font-mono text-center text-primary font-bold">${Utils.escapeHtml(u.TIME)}</td>
                                                                <td class="font-mono text-center text-primary font-bold">${Utils.escapeHtml(u.SESSION_START || u.FIRST_SEEN || '-')}</td>
                                                                <td class="font-mono text-center font-bold" title="총 ${u.OCCUPIED_MINUTES != null ? u.OCCUPIED_MINUTES : Math.round((u.OCCUPIED_HOURS || 0) * 60)}분 (${u.OCCUPIED_HOURS || 0}시간)">${u.OCCUPIED_TIME_STR || `${u.OCCUPIED_HOURS || 0}h`}</td>
                                                            </tr>
                                                        `).join('')}
                                                    </tbody>
                                                </table>
                                            </div>
                                        ` : `
                                            <div class="empty-module-users">
                                                <i class="fa-solid fa-circle-check text-success mr-1"></i> 현재 점유 중인 사용자가 없습니다. (모든 라이선스 즉시 사용 가능)
                                            </div>
                                        `}
                                    </div>
                                </div>
                            </div>
                        `;
                    }).join('')}
                </div>
            `;
            licModuleContainer.innerHTML = html;
        }
    },

    renderBenchmarkSparkline(data) {
        const canvas = document.getElementById('miniSparklineChart');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');

        const histSeries = data.historical_time_series || [];
        const todaySeries = data.today_time_series || [];
        const totalLimit = (data && (data.total || data.total_limit)) || 51;
        const used = data.used || 0;
        const avail = data.available !== undefined ? data.available : Math.max(0, totalLimit - used);

        // 1. Calculate Today Peak
        let todayPeakVal = 0;
        let todayPeakTime = '--:--';
        todaySeries.forEach(s => {
            if (!s.is_future && s.usage !== null && s.usage !== undefined) {
                if (s.usage > todayPeakVal) {
                    todayPeakVal = s.usage;
                    todayPeakTime = s.time;
                }
            }
        });

        // 2. Calculate Historical Peak
        let histPeakVal = 0;
        let histPeakTime = '--:--';
        histSeries.forEach(s => {
            if (s.avg_usage !== null && s.avg_usage !== undefined) {
                if (s.avg_usage > histPeakVal) {
                    histPeakVal = s.avg_usage;
                    histPeakTime = s.time;
                }
            }
        });

        // 3. Populate Header & KPI Summary Cards
        const spkHeaderDateBadge = document.getElementById('spkHeaderDateBadge');
        const spkTodayPeak = document.getElementById('spkTodayPeak');
        const spkTodayPeakTime = document.getElementById('spkTodayPeakTime');
        const spkHistPeak = document.getElementById('spkHistPeak');
        const spkHistPeakDays = document.getElementById('spkHistPeakDays');
        const spkInsightText = document.getElementById('spkInsightText');

        if (spkHeaderDateBadge) {
            spkHeaderDateBadge.textContent = `${data.target_date || '오늘'} 기준`;
        }

        if (spkTodayPeak) {
            if (todayPeakVal > 0) {
                spkTodayPeak.textContent = `${todayPeakVal} / ${totalLimit}개`;
            } else {
                spkTodayPeak.textContent = `${used} / ${totalLimit}개`;
            }
        }
        if (spkTodayPeakTime) {
            if (todayPeakVal > 0) {
                const pct = Math.round((todayPeakVal / totalLimit) * 100);
                spkTodayPeakTime.textContent = `${todayPeakTime} 피크 (${pct}%)`;
            } else {
                spkTodayPeakTime.textContent = `실시간 점유 기준`;
            }
        }

        if (spkHistPeak) {
            spkHistPeak.textContent = `${histPeakVal} / ${totalLimit}개`;
        }
        if (spkHistPeakDays) {
            const histDays = data.historical_days_count || 0;
            spkHistPeakDays.textContent = `${histPeakTime} 평균 (${histDays}일 누적)`;
        }

        if (spkInsightText) {
            const warnThresh = data.warning_threshold !== undefined ? data.warning_threshold : (totalLimit - 6);
            const noticeThresh = Math.max(0, totalLimit - 11);
            if (used >= warnThresh || todayPeakVal >= warnThresh) {
                spkInsightText.innerHTML = `<strong class="text-warning">경고:</strong> 라이선스 점유량이 임계치(${warnThresh}개 이상)에 근접했습니다. 미사용 단말의 반납을 권장합니다.`;
            } else if (todayPeakVal >= noticeThresh) {
                spkInsightText.innerHTML = '<strong class="text-primary">안내:</strong> 오후 집중 시간대(14:00~16:30) 라이선스 수요가 높습니다. 유휴 단말 점유를 최소화하세요.';
            } else {
                spkInsightText.textContent = '통상 14:00~16:30에 라이선스 점유가 집중되며, 12:00~13:00 점심시간대에는 일시 반납이 증가합니다.';
            }
        }

        // 4. Prepare Chart Data
        const labels = histSeries.map(s => s.time);
        const histData = histSeries.map(s => s.avg_usage);
        const todayData = labels.map(t => {
            const match = todaySeries.find(s => s.time === t);
            return (match && !match.is_future) ? match.usage : null;
        });

        const validHistMax = Math.max(...histData.filter(v => v !== null && !isNaN(v)), 0);
        const validTodayMax = Math.max(...todayData.filter(v => v !== null && !isNaN(v)), 0);
        const peakValue = Math.max(validHistMax, validTodayMax);
        const yMax = Math.max(totalLimit, Math.ceil(peakValue)) + 10;

        if (this.sparklineChart) {
            this.sparklineChart.destroy();
        }

        this.sparklineChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: `과거 전체 평균 (${data.historical_days_count || 0}일간 누적)`,
                        data: histData,
                        borderColor: '#94a3b8',
                        backgroundColor: 'rgba(148, 163, 184, 0.08)',
                        borderWidth: 2,
                        borderDash: [4, 4],
                        pointRadius: 0,
                        tension: 0,
                        fill: true
                    },
                    {
                        label: `당일 점유 실적 (${data.target_date || '오늘'})`,
                        data: todayData,
                        borderColor: '#2563eb',
                        backgroundColor: 'rgba(37, 99, 235, 0.12)',
                        borderWidth: 2.5,
                        pointRadius: 2.5,
                        pointHoverRadius: 6,
                        tension: 0,
                        fill: false
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: {
                        position: 'top',
                        labels: { boxWidth: 12, font: { size: 11, family: 'Inter' } }
                    },
                    tooltip: {
                        callbacks: {
                            label(context) {
                                const val = context.parsed.y;
                                if (val === null || val === undefined) return `${context.dataset.label}: 측정 전`;
                                return `${context.dataset.label}: ${val}개 (${((val / totalLimit) * 100).toFixed(1)}%)`;
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        min: 0,
                        max: yMax,
                        ticks: { stepSize: 10, font: { size: 10 } },
                        grid: { color: 'rgba(226, 232, 240, 0.15)' }
                    },
                    x: {
                        ticks: {
                            font: { size: 10 },
                            maxRotation: 0,
                            minRotation: 0,
                            autoSkip: false,
                            callback(val, index) {
                                const label = this.getLabelForValue ? this.getLabelForValue(val) : labels[val];
                                if (!label) return '';
                                return (label.endsWith(':00') || index === 0 || index === labels.length - 1) ? label : '';
                            }
                        },
                        grid: { display: false }
                    }
                }
            }
        });
    },

};

// =========================================================================
// 4. Pivot Module (Accordion 1)
// =========================================================================
const PivotModule = {
    dateController: null,
    rawData: null,
    currentSubview: 'summary', // 'summary' | 'slots'
    currentSlotDate: '',
    isSearching: false,
    lastFetchedAt: 0,

    init() {
        this.dateController = new DateRangeController(
            'pivotStartDateInput',
            'pivotEndDateInput',
            'pivotPresetSelect',
            () => this.search()
        );

        document.getElementById('btnSearchPivot')?.addEventListener('click', () => this.search());
        document.getElementById('searchInput')?.addEventListener('input', Utils.debounce(() => this.applyFilters(), 150));
        document.getElementById('filterDept')?.addEventListener('change', () => this.applyFilters());

        this.initSubviews();
        this.initExports();
    },

    initSubviews() {
        const pivotTabs = document.querySelectorAll('#accordionPivotItem .btn-tab');
        pivotTabs.forEach(tab => {
            tab.addEventListener('click', () => {
                pivotTabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this.currentSubview = tab.dataset.pivotSubview;

                const vSummary = document.getElementById('viewPivotSummaryContainer');
                const vSlots = document.getElementById('viewPivotSlotsContainer');

                if (vSummary) vSummary.style.display = (this.currentSubview === 'summary') ? 'block' : 'none';
                if (vSlots) vSlots.style.display = (this.currentSubview === 'slots') ? 'block' : 'none';

                if (this.currentSubview === 'slots') {
                    this.renderSlotsView();
                }
            });
        });
    },

    initExports() {
        const doExport = (format) => {
            const s = this.dateController.getStartDate();
            const e = this.dateController.getEndDate();
            const dept = document.getElementById('filterDept')?.value || '';
            const search = document.getElementById('searchInput')?.value || '';
            const subview = this.currentSubview || 'summary';
            const slotDate = this.currentSlotDate || s || '';

            const params = new URLSearchParams({
                format: format,
                type: 'pivot',
                start_date: s,
                end_date: e,
                view_mode: subview,
                slot_date: slotDate,
                dept: dept,
                search: search
            });
            window.location.href = `/api/v1/license/export?${params.toString()}`;
        };

        document.getElementById('btnExportPivotCsv')?.addEventListener('click', () => doExport('csv'));
        document.getElementById('btnExportPivotExcel')?.addEventListener('click', () => doExport('excel'));
    },

    async search() {
        if (this.isSearching) return;
        this.isSearching = true;

        const s = this.dateController.getStartDate();
        const e = this.dateController.getEndDate();
        const searchBtn = document.getElementById('btnSearchPivot');
        const noticeText = document.getElementById('pivotDateNoticeText');

        if (searchBtn) {
            searchBtn.disabled = true;
            searchBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 집계 중<span class="loading-dots"></span>';
        }
        if (noticeText) {
            noticeText.innerHTML = `선택하신 기간(${Utils.escapeHtml(s)} ~ ${Utils.escapeHtml(e)})의 사용자별 라이선스 점유 Matrix 데이터를 집계 중입니다<span class="loading-dots"></span>`;
        }

        try {
            const res = await fetch(`/api/v1/license/daily-user-pivot?start_date=${encodeURIComponent(s)}&end_date=${encodeURIComponent(e)}&_t=${Date.now()}`, { cache: 'no-store' });
            const data = await res.json();
            this.rawData = data;
            this.lastFetchedAt = Date.now();
            this.render();
        } catch (err) {
            console.error('Pivot search error:', err);
        } finally {
            this.isSearching = false;
            if (searchBtn) {
                searchBtn.disabled = false;
                searchBtn.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i> 조회';
            }
        }
    },

    render() {
        if (!this.rawData) return;
        this.renderNotice();
        this.renderSummaryTable();
        this.renderDeptSummaryTable();
        this.renderDateChips();
        if (this.currentSubview === 'slots') {
            this.renderSlotsView();
        }
    },

    renderNotice() {
        const notice = document.getElementById('pivotDateNoticeText');
        if (!notice || !this.rawData) return;
        const count = this.rawData.rows ? this.rawData.rows.length : 0;
        notice.textContent = `※ ${this.rawData.target_date} 기간 동안 고유 License 점유 사용자 총 ${count}명이 집계되었습니다.`;
    },

    applyFilters() {
        this.renderSummaryTable();
        if (this.currentSubview === 'slots') {
            this.renderSlotsView();
        }
    },

    renderSummaryTable() {
        const tbody = document.getElementById('pivotSummaryTbody');
        const thead = document.getElementById('pivotSummaryThead');
        const countBadge = document.getElementById('pivotSummaryCountBadge');
        if (!tbody || !this.rawData) return;

        const isMulti = this.rawData.is_multi_date;
        const selectedDates = this.rawData.selected_dates || [];
        const searchKeyword = (document.getElementById('searchInput')?.value || '').trim().toLowerCase();
        const deptFilter = document.getElementById('filterDept')?.value || 'ALL';

        let rows = this.rawData.rows || [];
        let filtered = rows.filter(r => {
            const matchesSearch = !searchKeyword ||
                (r.USER_NAME && r.USER_NAME.toLowerCase().includes(searchKeyword)) ||
                (r.DEPT_NAME && r.DEPT_NAME.toLowerCase().includes(searchKeyword)) ||
                (r.HOSTNAME && r.HOSTNAME.toLowerCase().includes(searchKeyword)) ||
                (r.EMP_NO && r.EMP_NO.toLowerCase().includes(searchKeyword)) ||
                (r.IP && r.IP.toLowerCase().includes(searchKeyword));
            const matchesDept = (deptFilter === 'ALL' || r.DEPT_NAME === deptFilter);
            return matchesSearch && matchesDept;
        });

        if (countBadge) {
            countBadge.textContent = `총 ${filtered.length}명`;
        }

        if (thead) {
            if (isMulti) {
                thead.innerHTML = `
                    <tr>
                        <th style="width: 40px;">No</th>
                        <th>부서명</th>
                        <th>사용자명</th>
                        <th>사번</th>
                        <th>장비명</th>
                        <th>IP</th>
                        <th class="text-center highlight-col">총 점유시간</th>
                        <th class="text-center">점유일수</th>
                        <th class="text-center">일평균 점유</th>
                        ${selectedDates.map(d => `<th class="text-center font-mono text-xs">${d.slice(5)}</th>`).join('')}
                    </tr>
                `;
            } else {
                thead.innerHTML = `
                    <tr>
                        <th style="width: 40px;">No</th>
                        <th>부서명</th>
                        <th>사용자명</th>
                        <th>사번</th>
                        <th>장비명</th>
                        <th>IP</th>
                        <th class="text-center highlight-col">당일 점유시간</th>
                        <th class="text-center">최초 접속</th>
                        <th class="text-center">최종 점유</th>
                        <th class="text-center">점유율(%)</th>
                    </tr>
                `;
            }
        }

        if (filtered.length === 0) {
            const colCount = isMulti ? (9 + selectedDates.length) : 10;
            tbody.innerHTML = `<tr><td colspan="${colCount}" class="text-center text-muted py-4">조회 조건에 일치하는 데이터가 없습니다.</td></tr>`;
            return;
        }

        tbody.innerHTML = filtered.map((r, idx) => {
            const totHours = r.TOTAL_HOURS || 0;
            const hourBadgeClass = totHours >= 30.0 ? 'badge-danger' : (totHours >= 15.0 ? 'badge-warning' : 'badge-light');

            if (isMulti) {
                const dateCols = selectedDates.map(d_iso => {
                    const d_ymd = d_iso.replace(/-/g, '');
                    const dHours = r.HOURS_BY_DATE ? (r.HOURS_BY_DATE[d_ymd] || 0) : 0;
                    if (dHours === 0) {
                        return '<td class="text-center text-muted font-mono">-</td>';
                    }
                    const cellClass = dHours >= 8.0 ? 'cell-danger' : (dHours >= 4.0 ? 'cell-warning' : 'cell-active');
                    return `<td class="text-center font-mono ${cellClass}">${dHours}h</td>`;
                }).join('');

                return `
                    <tr>
                        <td class="text-center font-mono text-muted">${idx + 1}</td>
                        <td><span class="dept-badge">${Utils.escapeHtml(r.DEPT_NAME)}</span></td>
                        <td class="font-bold text-dark">${Utils.escapeHtml(r.USER_NAME)}</td>
                        <td class="font-mono text-muted">${Utils.escapeHtml(r.EMP_NO)}</td>
                        <td class="font-mono text-muted">${Utils.escapeHtml(r.HOSTNAME)}</td>
                        <td class="font-mono text-muted">${Utils.escapeHtml(r.IP)}</td>
                        <td class="text-center"><span class="badge ${hourBadgeClass}">${totHours}h</span></td>
                        <td class="text-center font-mono font-bold">${r.ACTIVE_DAYS}일</td>
                        <td class="text-center font-mono text-primary font-bold">${r.AVG_HOURS_DAY}h</td>
                        ${dateCols}
                    </tr>
                `;
            } else {
                const slotCount = (this.rawData && this.rawData.time_slots && this.rawData.time_slots.length) ? this.rawData.time_slots.length : 37;
                const ratePct = ((totHours / (slotCount * 0.25)) * 100).toFixed(1);
                return `
                    <tr>
                        <td class="text-center font-mono text-muted">${idx + 1}</td>
                        <td><span class="dept-badge">${Utils.escapeHtml(r.DEPT_NAME)}</span></td>
                        <td class="font-bold text-dark">${Utils.escapeHtml(r.USER_NAME)}</td>
                        <td class="font-mono text-muted">${Utils.escapeHtml(r.EMP_NO)}</td>
                        <td class="font-mono text-muted">${Utils.escapeHtml(r.HOSTNAME)}</td>
                        <td class="font-mono text-muted">${Utils.escapeHtml(r.IP)}</td>
                        <td class="text-center"><span class="badge ${hourBadgeClass}">${totHours}h</span></td>
                        <td class="text-center font-mono text-primary font-bold">${r.FIRST_SEEN || '-'}</td>
                        <td class="text-center font-mono text-muted">${r.LAST_SEEN || '-'}</td>
                        <td class="text-center font-mono font-bold text-dark">${ratePct}%</td>
                    </tr>
                `;
            }
        }).join('');
    },

    renderDeptSummaryTable() {
        const tbody = document.getElementById('deptSummaryTbody');
        if (!tbody || !this.rawData) return;
        const list = this.rawData.dept_summary || [];

        tbody.innerHTML = list.map(d => `
            <tr>
                <td class="font-bold text-dark">${Utils.escapeHtml(d.dept_name)}</td>
                <td class="text-center font-mono font-bold">${d.user_count}명</td>
                <td class="text-center font-mono text-primary font-bold">${d.total_hours}h</td>
                <td class="text-center font-mono text-muted font-bold">${d.avg_hours}h</td>
            </tr>
        `).join('');
    },

    renderDateChips() {
        const wrapper = document.getElementById('pivotDateChipsWrapper');
        if (!wrapper || !this.rawData) return;

        const dates = this.rawData.selected_dates || [];
        if (dates.length <= 1) {
            wrapper.style.display = 'none';
            this.currentSlotDate = dates[0] || '';
            return;
        }

        wrapper.style.display = 'flex';
        wrapper.innerHTML = dates.map((d, idx) => `
            <button type="button" class="btn-chip ${idx === 0 ? 'active' : ''}" data-date="${d}">
                ${d.slice(5)}
            </button>
        `).join('');

        this.currentSlotDate = dates[0] || '';
        wrapper.querySelectorAll('.btn-chip').forEach(btn => {
            btn.addEventListener('click', () => {
                wrapper.querySelectorAll('.btn-chip').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.currentSlotDate = btn.dataset.date;
                this.renderSlotsView();
            });
        });
    },

    renderSlotsView() {
        const tbody = document.getElementById('pivotSlotsTbody');
        const thead = document.getElementById('pivotSlotsThead');
        const countBadge = document.getElementById('pivotSlotsCountBadge');
        if (!tbody || !this.rawData) return;

        const targetDate = this.currentSlotDate || this.rawData.selected_dates[0] || '';
        const targetYmd = targetDate.replace(/-/g, '');
        const dailyRows = (this.rawData.daily_details && this.rawData.daily_details[targetYmd]) ? this.rawData.daily_details[targetYmd] : [];
        const slots = this.rawData.time_slots || [];

        if (countBadge) {
            countBadge.textContent = `Selected Date: ${targetDate}`;
        }

        if (thead) {
            thead.innerHTML = `
                <tr>
                    <th style="width: 40px;">No</th>
                    <th>부서명</th>
                    <th>사용자명</th>
                    <th>장비명</th>
                    <th class="text-center">점유시간</th>
                    ${slots.map(s => `<th class="text-center font-mono text-xs slot-th">${s}</th>`).join('')}
                </tr>
            `;
        }

        const searchKeyword = (document.getElementById('searchInput')?.value || '').trim().toLowerCase();
        const deptFilter = document.getElementById('filterDept')?.value || 'ALL';

        let filtered = dailyRows.filter(r => {
            const matchesSearch = !searchKeyword ||
                (r.USER_NAME && r.USER_NAME.toLowerCase().includes(searchKeyword)) ||
                (r.DEPT_NAME && r.DEPT_NAME.toLowerCase().includes(searchKeyword)) ||
                (r.HOSTNAME && r.HOSTNAME.toLowerCase().includes(searchKeyword)) ||
                (r.EMP_NO && r.EMP_NO.toLowerCase().includes(searchKeyword)) ||
                (r.IP && r.IP.toLowerCase().includes(searchKeyword));
            const matchesDept = (deptFilter === 'ALL' || r.DEPT_NAME === deptFilter);
            return matchesSearch && matchesDept;
        });

        if (filtered.length === 0) {
            tbody.innerHTML = `<tr><td colspan="${5 + slots.length}" class="text-center text-muted py-4">${targetDate} 일자에 점유 데이터가 없습니다.</td></tr>`;
            return;
        }

        tbody.innerHTML = filtered.map((r, idx) => {
            const hoursVal = (r.HOURS_OCCUPIED !== undefined) ? r.HOURS_OCCUPIED : ((r.TOTAL_HOURS !== undefined) ? r.TOTAL_HOURS : 0);
            const occBadge = hoursVal >= 8.0 ? 'badge-danger' : (hoursVal >= 4.0 ? 'badge-warning' : 'badge-light');

            const slotCells = slots.map(s => {
                const isUsed = r.SLOTS && r.SLOTS[s] === 1;
                return isUsed ? '<td class="slot-cell occupied" title="' + s + ' 점유">■</td>' : '<td class="slot-cell empty">-</td>';
            }).join('');

            return `
                <tr>
                    <td class="text-center font-mono text-muted">${idx + 1}</td>
                    <td><span class="dept-badge">${Utils.escapeHtml(r.DEPT_NAME)}</span></td>
                    <td class="font-bold text-dark">${Utils.escapeHtml(r.USER_NAME)}</td>
                    <td class="font-mono text-muted">${Utils.escapeHtml(r.HOSTNAME)}</td>
                    <td class="text-center"><span class="badge ${occBadge}">${hoursVal}h</span></td>
                    ${slotCells}
                </tr>
            `;
        }).join('');
    }
};

// =========================================================================
// 5. Anomaly Module (Accordion 2: ProcTrace Client Idle Entry Live Monitor)
// =========================================================================
const AnomalyModule = {
    rawData: null,

    init() {
        document.getElementById('btnRefreshAnomaly')?.addEventListener('click', () => {
            this.search();
        });

        document.getElementById('anomalyDeptFilter')?.addEventListener('change', () => {
            this.applyLocalFilter();
        });

        document.getElementById('anomalySearchInput')?.addEventListener('input', Utils.debounce(() => {
            this.applyLocalFilter();
        }, 150));

        this.initExport();
    },

    initExport() {
        const doExport = (format) => {
            const dept = document.getElementById('anomalyDeptFilter')?.value || '전체';
            const search = document.getElementById('anomalySearchInput')?.value || '';

            const params = new URLSearchParams({
                format: format,
                type: 'anomaly',
                dept: dept,
                search: search
            });

            window.location.href = `/api/v1/license/export?${params.toString()}`;
        };

        document.getElementById('btnExportAnomalyCsv')?.addEventListener('click', () => doExport('csv'));
        document.getElementById('btnExportAnomalyExcel')?.addEventListener('click', () => doExport('excel'));
    },

    populateDeptOptions(departments) {
        const deptSelect = document.getElementById('anomalyDeptFilter');
        if (!deptSelect || !departments) return;
        const currentVal = deptSelect.value;
        deptSelect.innerHTML = '<option value="전체">전체 부서</option>' +
            departments.map(d => `<option value="${Utils.escapeHtml(d)}">${Utils.escapeHtml(d)}</option>`).join('');
        if (currentVal) deptSelect.value = currentVal;
    },

    async search() {
        const dept = document.getElementById('anomalyDeptFilter')?.value || '전체';
        const kw = document.getElementById('anomalySearchInput')?.value || '';
        const refreshBtn = document.getElementById('btnRefreshAnomaly');

        if (refreshBtn) {
            refreshBtn.disabled = true;
            refreshBtn.innerHTML = '<i class="fa-solid fa-arrows-rotate fa-spin"></i> 갱신 중<span class="loading-dots"></span>';
        }

        try {
            const res = await fetch(`/api/v1/license/anomalies?dept_name=${encodeURIComponent(dept)}&search=${encodeURIComponent(kw)}&_t=${Date.now()}`, { cache: 'no-store' });
            const data = await res.json();
            this.rawData = data;
            this.render(data);
        } catch (err) {
            console.error('Anomaly search error:', err);
        } finally {
            if (refreshBtn) {
                refreshBtn.disabled = false;
                refreshBtn.innerHTML = '<i class="fa-solid fa-arrows-rotate"></i> 실시간 새로고침';
            }
        }
    },

    applyLocalFilter() {
        if (!this.rawData || !this.rawData.items) return;
        const dept = document.getElementById('anomalyDeptFilter')?.value || '전체';
        const kw = (document.getElementById('anomalySearchInput')?.value || '').trim().toLowerCase();

        const filtered = this.rawData.items.filter(it => {
            if (dept !== '전체' && it.dept_name !== dept) return false;
            if (kw) {
                const fullStr = `${it.user_name} ${it.emp_id} ${it.dept_name} ${it.hostname} ${it.ip} ${it.pid} ${it.description} ${it.event_code || ''} ${it.status_label || ''}`.toLowerCase();
                if (!fullStr.includes(kw)) return false;
            }
            return true;
        });

        this.renderTableOnly(filtered);
    },

    render(data) {
        if (!data) return;
        const titleEl = document.getElementById('anomalyCountTitle');
        const items = data.items || [];
        const monitoredCount = data.monitored_hosts_count || 0;

        if (titleEl) {
            titleEl.textContent = `CATIA 프로세스 실시간 유휴 진입(Idle) 이상 점유 모니터링 (${items.length}대 감지 / 모니터링 대상: ${monitoredCount}대)`;
        }

        const overviewBadge = document.getElementById('overviewAnomalyBadge');
        if (overviewBadge) {
            overviewBadge.textContent = `${items.length}대 감지`;
            overviewBadge.className = items.length > 0 ? 'badge badge-warning ml-2' : 'badge badge-light ml-2';
        }

        this.renderTableOnly(items);
    },

    renderTableOnly(items) {
        const tbody = document.getElementById('anomalyBody');
        if (!tbody) return;

        if (!items || items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" class="text-center text-muted py-4">해당 조건에 탐지된 유휴 진입 이상 사용자가 없습니다. (모든 사용자 정상 사용 중)</td></tr>';
            return;
        }

        tbody.innerHTML = items.map(a => `
            <tr>
                <td class="font-mono text-dark font-bold">${Utils.escapeHtml(a.timestamp || '-')}</td>
                <td><span class="dept-badge">${Utils.escapeHtml(a.dept_name || '미등록')}</span></td>
                <td class="font-bold text-dark">${Utils.escapeHtml(a.user_name || '-')}</td>
                <td class="font-mono text-muted">${Utils.escapeHtml(a.emp_id || '-')}</td>
                <td class="font-mono font-bold">${Utils.escapeHtml(a.hostname || '-')}</td>
                <td class="font-mono text-muted">${Utils.escapeHtml(a.ip || '-')}</td>
                <td class="font-mono text-muted">${Utils.escapeHtml(a.pid || '-')}</td>
                <td class="font-mono text-xs"><span class="badge badge-secondary">CPU: ${Utils.escapeHtml(a.cpu || '-')}</span> <span class="badge badge-secondary">MEM: ${Utils.escapeHtml(a.mem || '-')}</span></td>
                <td><span class="badge badge-warning"><i class="fa-solid fa-moon"></i> ${Utils.escapeHtml(a.description || '[유휴 진입]')}</span></td>
                <td><span class="badge badge-danger"><i class="fa-solid fa-triangle-exclamation"></i> ${Utils.escapeHtml(a.action_recommend || 'License 회수 권고')}</span></td>
            </tr>
        `).join('');
    }
};

// =========================================================================
// 6. Stats Module (Accordion 3)
// =========================================================================
const StatsModule = {
    dateController: null,
    rawData: null,
    currentViewMode: 'matrix', // 'matrix' | '15m' | 'daily' | 'hourly'
    currentChartMode: 'line', // 'line' | 'bar'
    currentDeptMode: 'top6', // 'top6' | 'all'
    mainChart: null,
    deptChart: null,
    isSearching: false,
    lastFetchedAt: 0,

    init() {
        this.dateController = new DateRangeController(
            'statsStartDateInput',
            'statsEndDateInput',
            'statsPresetSelect',
            () => this.search()
        );

        document.getElementById('btnSearchStats')?.addEventListener('click', () => this.search());
        this.initViewToggles();
        this.initChartToggles();
        this.initDeptToggles();
        this.initExports();
    },

    initDeptToggles() {
        const top6Btn = document.getElementById('btnDeptModeTop6');
        const allBtn = document.getElementById('btnDeptModeAll');
        if (top6Btn && allBtn) {
            top6Btn.addEventListener('click', () => {
                top6Btn.classList.add('active');
                allBtn.classList.remove('active');
                this.currentDeptMode = 'top6';
                if (this.rawData) this.renderDeptChart();
            });
            allBtn.addEventListener('click', () => {
                allBtn.classList.add('active');
                top6Btn.classList.remove('active');
                this.currentDeptMode = 'all';
                if (this.rawData) this.renderDeptChart();
            });
        }
    },

    initViewToggles() {
        const statsTabs = document.querySelectorAll('#accordionStatsItem .btn-tab');
        statsTabs.forEach(tab => {
            tab.addEventListener('click', () => {
                statsTabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this.currentViewMode = tab.dataset.statView;

                const v15m = document.getElementById('view15mStatsTable');
                const vDaily = document.getElementById('viewDailyStatsTable');
                const vHourly = document.getElementById('viewHourlyStatsTable');
                const vMatrix = document.getElementById('viewMatrixStatsTable');

                if (v15m) v15m.style.display = (this.currentViewMode === '15m') ? 'block' : 'none';
                if (vDaily) vDaily.style.display = (this.currentViewMode === 'daily') ? 'block' : 'none';
                if (vHourly) vHourly.style.display = (this.currentViewMode === 'hourly') ? 'block' : 'none';
                if (vMatrix) vMatrix.style.display = (this.currentViewMode === 'matrix') ? 'block' : 'none';
            });
        });
    },

    initChartToggles() {
        const chartBtns = document.querySelectorAll('#accordionStatsItem .btn-tab-sm');
        chartBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                chartBtns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.currentChartMode = btn.dataset.chartMode;

                const chartTitle = document.getElementById('mainChartTitle');
                if (this.currentChartMode === 'line') {
                    if (chartTitle) chartTitle.innerHTML = '<i class="fa-solid fa-chart-line"></i> 시간대별 동시 점유 수량 추이';
                } else {
                    if (chartTitle) chartTitle.innerHTML = '<i class="fa-solid fa-chart-column"></i> 일자별 최대 Peak vs 일일 평균 사용량';
                }

                if (this.rawData) {
                    this.renderMainChart();
                }
            });
        });
    },

    initExports() {
        const doExport = (format) => {
            const s = this.dateController.getStartDate();
            const e = this.dateController.getEndDate();
            const viewMode = this.currentViewMode || 'matrix';

            const params = new URLSearchParams({
                format: format,
                type: 'stats',
                start_date: s,
                end_date: e,
                view_mode: viewMode
            });
            window.location.href = `/api/v1/license/export?${params.toString()}`;
        };

        document.getElementById('btnExportStatsCsv')?.addEventListener('click', () => doExport('csv'));
        document.getElementById('btnExportStatsExcel')?.addEventListener('click', () => doExport('excel'));
    },

    async search() {
        if (this.isSearching) return;
        this.isSearching = true;

        const s = this.dateController.getStartDate();
        const e = this.dateController.getEndDate();
        const searchBtn = document.getElementById('btnSearchStats');

        if (searchBtn) {
            searchBtn.disabled = true;
            searchBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 분석 중<span class="loading-dots"></span>';
        }

        try {
            const res = await fetch(`/api/v1/license/stats?start_date=${encodeURIComponent(s)}&end_date=${encodeURIComponent(e)}&_t=${Date.now()}`, { cache: 'no-store' });
            const data = await res.json();
            this.rawData = data;
            this.lastFetchedAt = Date.now();
            this.render();
        } catch (err) {
            console.error('Stats search error:', err);
        } finally {
            this.isSearching = false;
            if (searchBtn) {
                searchBtn.disabled = false;
                searchBtn.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i> 기간 분석';
            }
        }
    },

    render() {
        if (!this.rawData) return;
        this.renderPeakCards();
        this.renderMainChart();
        this.renderDeptChart();
        this.render15mTable();
        this.renderDailyTable();
        this.renderHourlyTable();
        this.renderMatrixTable();
        this.renderInsights();
    },

    renderPeakCards() {
        const p = this.rawData.peak_summary || {};
        const dailySummary = this.rawData.daily_summary || [];
        const deptDist = this.rawData.dept_distribution || [];

        const kpiPeriodOver50PctText = document.getElementById('kpiPeriodOver50PctText');
        const kpiPeriodOver50PctSub = document.getElementById('kpiPeriodOver50PctSub');
        const kpiDaysOver60PctText = document.getElementById('kpiDaysOver60PctText');
        const kpiDaysOver60PctSub = document.getElementById('kpiDaysOver60PctSub');
        const kpiWorkAvgText = document.getElementById('kpiWorkAvgText');
        const kpiWorkRateSub = document.getElementById('kpiWorkRateSub');
        const kpiUniqueUsersText = document.getElementById('kpiUniqueUsersText');
        const kpiUniqueUsersSub = document.getElementById('kpiUniqueUsersSub');
        const statsDateBadge = document.getElementById('statsDateBadge');

        let periodOver50Count = p.period_over_50_count;
        let periodTotalSlots = p.period_total_slots;
        let periodOver50Pct = p.period_over_50_pct;

        if (periodOver50Count === undefined || periodTotalSlots === undefined || periodOver50Pct === undefined) {
            periodOver50Count = dailySummary.reduce((acc, d) => acc + (d.slots_over_50_count || 0), 0);
            periodTotalSlots = dailySummary.reduce((acc, d) => acc + (d.total_slots || 0), 0);
            periodOver50Pct = periodTotalSlots > 0 ? Number(((periodOver50Count / periodTotalSlots) * 100).toFixed(1)) : 0.0;
        }

        let daysOver60Count = p.days_over_60_pct_count;
        let totalDaysCount = p.total_days_count;
        let daysOver60Rate = p.days_over_60_pct_rate;

        if (daysOver60Count === undefined || totalDaysCount === undefined || daysOver60Rate === undefined) {
            totalDaysCount = dailySummary.length;
            daysOver60Count = dailySummary.filter(d => (d.slots_over_50_pct || 0) >= 60.0).length;
            daysOver60Rate = totalDaysCount > 0 ? Number(((daysOver60Count / totalDaysCount) * 100).toFixed(1)) : 0.0;
        }

        let uniqueUsersCount = p.unique_users_count !== undefined ? p.unique_users_count : this.rawData.unique_users_count;
        if (uniqueUsersCount === undefined) {
            uniqueUsersCount = deptDist.reduce((acc, d) => acc + (d.user_count || 0), 0);
        }
        const activeDeptsCount = p.active_depts_count !== undefined ? p.active_depts_count : deptDist.length;

        if (kpiPeriodOver50PctText) kpiPeriodOver50PctText.textContent = `${periodOver50Pct}%`;
        if (kpiPeriodOver50PctSub) kpiPeriodOver50PctSub.textContent = `50개 이상 슬롯: ${periodOver50Count} / ${periodTotalSlots}개 (점심 제외)`;
        if (kpiDaysOver60PctText) kpiDaysOver60PctText.textContent = `${daysOver60Count}일 / ${totalDaysCount}일`;
        if (kpiDaysOver60PctSub) kpiDaysOver60PctSub.textContent = `일별 60% 이상 비중: ${daysOver60Rate}%`;
        if (kpiWorkAvgText) kpiWorkAvgText.textContent = `${p.work_hours_avg || 0}개`;
        if (kpiWorkRateSub) kpiWorkRateSub.textContent = `가동률: ${p.work_hours_rate || 0}%`;
        if (kpiUniqueUsersText) kpiUniqueUsersText.textContent = `${uniqueUsersCount}명`;
        if (kpiUniqueUsersSub) kpiUniqueUsersSub.textContent = `1회 이상 사용 (중복 제거 · 총 ${activeDeptsCount}개 부서)`;
        if (statsDateBadge) statsDateBadge.textContent = this.rawData.target_date || '';
    },

    renderMainChart() {
        const canvas = document.getElementById('mainTimeSeriesChart');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');

        if (this.mainChart) {
            this.mainChart.destroy();
        }

        const totalLimit = this.rawData?.total_limit || 51;

        if (this.currentChartMode === 'bar') {
            const daily = this.rawData.daily_summary || [];
            const labels = daily.map(d => d.date.slice(5));
            const peakData = daily.map(d => d.peak_usage);
            const avgData = daily.map(d => d.avg_usage);
            const maxVal = Math.max(...peakData.filter(v => v !== null && !isNaN(v)), ...avgData.filter(v => v !== null && !isNaN(v)), 0);
            const yMax = Math.max(totalLimit, Math.ceil(maxVal)) + 20;

            this.mainChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: '일일 최대 피크 (Peak)',
                            data: peakData,
                            backgroundColor: 'rgba(239, 68, 68, 0.85)',
                            borderRadius: 4
                        },
                        {
                            label: '일일 평균 점유량 (Average)',
                            data: avgData,
                            backgroundColor: 'rgba(37, 99, 235, 0.75)',
                            borderRadius: 4
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: { min: 0, max: yMax, ticks: { stepSize: 10 } }
                    }
                }
            });
        } else {
            const series15m = this.rawData.time_series_15m || [];
            const labels = series15m.map(s => s.time);
            const avgData = series15m.map(s => s.avg_usage);
            const peakData = series15m.map(s => s.peak_usage);
            const maxVal = Math.max(...peakData.filter(v => v !== null && !isNaN(v)), ...avgData.filter(v => v !== null && !isNaN(v)), 0);
            const yMax = Math.max(totalLimit, Math.ceil(maxVal)) + 20;

            this.mainChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: '최대 피크 점유량 (Peak)',
                            data: peakData,
                            borderColor: '#ef4444',
                            backgroundColor: 'rgba(239, 68, 68, 0.08)',
                            borderWidth: 2,
                            pointRadius: 2,
                            fill: true,
                            tension: 0
                        },
                        {
                            label: '평균 점유량 (Average)',
                            data: avgData,
                            borderColor: '#2563eb',
                            backgroundColor: 'rgba(37, 99, 235, 0.12)',
                            borderWidth: 2.5,
                            pointRadius: 2,
                            fill: true,
                            tension: 0
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    scales: {
                        y: { min: 0, max: yMax, ticks: { stepSize: 10 } },
                        x: {
                            ticks: {
                                maxRotation: 0,
                                minRotation: 0,
                                autoSkip: false,
                                callback(val, index) {
                                    const label = this.getLabelForValue ? this.getLabelForValue(val) : labels[val];
                                    if (!label) return '';
                                    return (label.endsWith(':00') || index === 0 || index === labels.length - 1) ? label : '';
                                }
                            }
                        }
                    }
                }
            });
        }
    },

    renderDeptChart() {
        const canvas = document.getElementById('deptPieChart');
        const legendWrapper = document.getElementById('deptLegendWrapper');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');

        if (this.deptChart) {
            this.deptChart.destroy();
            this.deptChart = null;
        }

        const deptDist = this.rawData?.dept_distribution || [];
        if (deptDist.length === 0) {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            if (legendWrapper) legendWrapper.innerHTML = '<div class="text-muted text-xs p-3">부서 데이터 없음</div>';
            return;
        }

        let labels = [];
        let dataVals = [];
        let bgColors = [];
        let legendItems = [];

        const totalUsage = deptDist.reduce((acc, cur) => acc + cur.count, 0);

        if (this.currentDeptMode === 'all') {
            labels = deptDist.map(d => `${d.dept_name} (${d.user_count || 0}명)`);
            dataVals = deptDist.map(d => d.count);
            // High-contrast distinct colors
            bgColors = deptDist.map((_, i) => {
                const hue = Math.round((i * 137.508) % 360);
                return `hsl(${hue}, 70%, 48%)`;
            });

            legendItems = deptDist.map((d, i) => {
                const hrs = d.total_hours !== undefined ? d.total_hours : (d.count * 0.25);
                const pct = totalUsage > 0 ? ((d.count / totalUsage) * 100).toFixed(1) : 0;
                const uCount = d.user_count || 0;
                return {
                    index: i,
                    name: `${d.dept_name} (${uCount}명)`,
                    count: d.count,
                    userCount: uCount,
                    users: d.users || [],
                    hours: hrs,
                    pct: pct,
                    color: bgColors[i]
                };
            });
        } else {
            const topDepts = deptDist.slice(0, 6);
            const others = deptDist.slice(6);
            const otherCount = others.reduce((acc, cur) => acc + cur.count, 0);
            const otherUserCount = others.reduce((acc, cur) => acc + (cur.user_count || 0), 0);

            labels = topDepts.map(d => `${d.dept_name} (${d.user_count || 0}명)`);
            dataVals = topDepts.map(d => d.count);

            const defaultPalette = [
                '#2563eb', '#0284c7', '#0d9488', '#16a34a', '#d97706', '#dc2626', '#64748b'
            ];

            if (otherCount > 0) {
                labels.push(`기타 부서 (${otherUserCount}명)`);
                dataVals.push(otherCount);
            }

            bgColors = defaultPalette.slice(0, labels.length);

            legendItems = topDepts.map((d, i) => {
                const hrs = d.total_hours !== undefined ? d.total_hours : (d.count * 0.25);
                const pct = totalUsage > 0 ? ((d.count / totalUsage) * 100).toFixed(1) : 0;
                const uCount = d.user_count || 0;
                return {
                    index: i,
                    name: `${d.dept_name} (${uCount}명)`,
                    count: d.count,
                    userCount: uCount,
                    users: d.users || [],
                    hours: hrs,
                    pct: pct,
                    color: bgColors[i]
                };
            });

            if (otherCount > 0) {
                const otherHrs = otherCount * 0.25;
                const otherPct = totalUsage > 0 ? ((otherCount / totalUsage) * 100).toFixed(1) : 0;
                const otherUsers = others.flatMap(d => (d.users || []).map(u => ({
                    ...u,
                    dept_name: d.dept_name
                }))).sort((a, b) => (b.total_hours || 0) - (a.total_hours || 0));
                legendItems.push({
                    index: labels.length - 1,
                    name: `기타 부서 (${otherUserCount}명)`,
                    count: otherCount,
                    userCount: otherUserCount,
                    users: otherUsers,
                    hours: otherHrs,
                    pct: otherPct,
                    color: bgColors[labels.length - 1]
                });
            }
        }

        // Render Clean Scrollable Legend (Guaranteed Inline Color Block + Hours + Theme-Adaptive Text)
        if (legendWrapper) {
            legendWrapper.innerHTML = `
                <ul class="dept-legend-list" style="list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:6px;">
                    ${legendItems.map(item => {
                        const userRowsHtml = (item.users && item.users.length > 0)
                            ? item.users.map((u, uIdx) => {
                                const subMeta = [
                                    u.dept_name,
                                    (u.emp_no && u.emp_no !== '-' ? u.emp_no : null),
                                    u.hostname
                                ].filter(Boolean).join(' · ');
                                const uHrs = Number(u.total_hours || 0);
                                return `
                                    <div style="display:flex; align-items:center; justify-content:space-between; gap:6px; padding:2px 0; border-bottom:1px dashed rgba(128,128,128,0.15); text-align:left;">
                                        <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${Utils.escapeHtml(u.user_name)} (${Utils.escapeHtml(subMeta)})">
                                            <strong style="font-weight:600;">${uIdx + 1}. ${Utils.escapeHtml(u.user_name)}</strong>
                                            <span style="opacity:0.75; font-size:10px; margin-left:3px;">(${Utils.escapeHtml(subMeta)})</span>
                                        </span>
                                        <span style="white-space:nowrap; font-family:monospace; font-size:10.5px; opacity:0.85; flex-shrink:0;">${uHrs}h</span>
                                    </div>
                                `;
                            }).join('')
                            : '<div style="opacity:0.6; font-size:10.5px; padding:2px 0; text-align:left;">조회된 사용자 내역 없음</div>';

                        return `
                            <li class="dept-legend-item" data-dept-idx="${item.index}" title="${Utils.escapeHtml(item.name)}: 누적 ${item.hours}h (${item.pct}%)" style="display:flex; align-items:center; cursor:pointer; font-size:12px; padding:2px 4px; border-radius:3px; user-select:none;">
                                <span class="dept-legend-color" style="display:inline-block !important; width:13px !important; height:13px !important; min-width:13px !important; min-height:13px !important; background-color:${item.color} !important; border-radius:2px !important; flex-shrink:0 !important; margin-right:8px !important;"></span>
                                <span class="dept-legend-text" style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${Utils.escapeHtml(item.name)}</span>
                                <span class="dept-legend-hours" style="margin-left:auto; padding-left:8px; font-size:11px; opacity:0.85; white-space:nowrap;">${item.hours}h <span style="font-size:10px;">(${item.pct}%)</span></span>
                            </li>
                            <li class="dept-user-sublist" data-sublist-idx="${item.index}" style="display:none; list-style:none; margin:0 0 4px 21px; padding:4px 8px; border-left:2px solid ${item.color}; background:rgba(128,128,128,0.06); border-radius:0 4px 4px 0; font-size:11px; text-align:left;">
                                ${userRowsHtml}
                            </li>
                        `;
                    }).join('')}
                </ul>
            `;

            // Hover & Click interactions between legend item and chart
            const items = legendWrapper.querySelectorAll('.dept-legend-item');
            items.forEach(el => {
                const idx = parseInt(el.dataset.deptIdx, 10);
                const sublistEl = legendWrapper.querySelector(`.dept-user-sublist[data-sublist-idx="${idx}"]`);

                el.addEventListener('click', () => {
                    if (sublistEl) {
                        sublistEl.style.display = sublistEl.style.display === 'none' ? 'block' : 'none';
                    }
                });

                el.addEventListener('mouseenter', () => {
                    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
                    el.style.backgroundColor = isLight ? 'rgba(0, 0, 0, 0.08)' : 'rgba(255, 255, 255, 0.15)';
                    el.style.fontWeight = '600';
                    if (this.deptChart) {
                        this.deptChart.setActiveElements([{ datasetIndex: 0, index: idx }]);
                        this.deptChart.tooltip.setActiveElements([{ datasetIndex: 0, index: idx }], { x: 0, y: 0 });
                        this.deptChart.update();
                    }
                });
                el.addEventListener('mouseleave', () => {
                    el.style.backgroundColor = 'transparent';
                    el.style.fontWeight = 'normal';
                    if (this.deptChart) {
                        this.deptChart.setActiveElements([]);
                        this.deptChart.tooltip.setActiveElements([], { x: 0, y: 0 });
                        this.deptChart.update();
                    }
                });
            });
        }

        // Render Doughnut Chart
        this.deptChart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: dataVals,
                    backgroundColor: bgColors,
                    borderWidth: 1,
                    borderColor: '#ffffff',
                    hoverOffset: 5
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '60%',
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        callbacks: {
                            label(context) {
                                const val = context.parsed;
                                const hrs = val * 0.25;
                                const pct = totalUsage > 0 ? ((val / totalUsage) * 100).toFixed(1) : 0;
                                return ` ${context.label}: 누적 ${hrs}h (${pct}%)`;
                            }
                        }
                    }
                }
            }
        });
    },

    render15mTable() {
        const tbody = document.getElementById('timeSeries15mTbody');
        if (!tbody || !this.rawData) return;
        const list = this.rawData.time_series_15m || [];

        tbody.innerHTML = list.map(s => {
            if (s.is_future) {
                return `
                    <tr class="text-muted">
                        <td class="font-mono font-bold">${s.time}</td>
                        <td>-</td>
                        <td>-</td>
                        <td>-</td>
                        <td>
                            <div class="progress-gauge" title="미측정">
                                <div class="progress-gauge-fill" style="width: 0%;"></div>
                            </div>
                        </td>
                        <td>-</td>
                        <td><span class="badge badge-light">미측정</span></td>
                    </tr>
                `;
            }

            const peakVal = s.peak_usage || 0;
            const avgVal = s.avg_usage || 0;
            const rateVal = s.rate_pct || 0;
            const isPeak = s.is_peak;

            let fillColor = '#10b981'; // green
            if (rateVal >= 90 || isPeak) {
                fillColor = '#ef4444'; // red
            } else if (rateVal >= 75) {
                fillColor = '#f59e0b'; // orange
            } else if (rateVal >= 50) {
                fillColor = '#3b82f6'; // blue
            }

            return `
                <tr class="${isPeak ? 'row-peak-warning' : ''}">
                    <td class="font-mono font-bold text-dark">${s.time}</td>
                    <td class="font-mono font-bold text-primary">${avgVal}개</td>
                    <td class="font-mono font-bold text-danger">${peakVal}개</td>
                    <td class="font-mono text-muted">${s.min_usage || 0}개</td>
                    <td>
                        <div class="progress-gauge" title="점유율: ${rateVal}% (${avgVal}/51개)">
                            <div class="progress-gauge-fill" style="width: ${Math.max(rateVal > 0 ? 3 : 0, Math.min(100, rateVal))}%; background-color: ${fillColor};"></div>
                        </div>
                    </td>
                    <td class="font-mono font-bold">${rateVal}%</td>
                    <td>
                        ${isPeak ? '<span class="badge badge-danger">피크 경고</span>' : '<span class="badge badge-normal">정상</span>'}
                    </td>
                </tr>
            `;
        }).join('');
    },

    renderDailyTable() {
        const tbody = document.getElementById('dailySummaryTbody');
        const countBadge = document.getElementById('dailyStatsCountBadge');
        if (!tbody || !this.rawData) return;
        const rawList = this.rawData.daily_summary || [];
        const list = rawList.filter(d => (d.peak_usage || 0) > 0 || (d.avg_usage || 0) > 0);

        if (countBadge) {
            countBadge.textContent = `${list.length}일간 분석`;
        }

        if (list.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-muted p-3">조회된 기간의 사용량 데이터가 없습니다.</td></tr>';
            return;
        }

        tbody.innerHTML = list.map(d => `
            <tr>
                <td class="font-mono font-bold text-dark">${d.date}</td>
                <td class="font-mono font-bold text-primary">${d.avg_usage}개</td>
                <td class="font-mono font-bold text-danger">${d.peak_usage}개</td>
                <td class="font-mono text-muted">${d.peak_time}</td>
                <td class="font-mono">${d.morning_peak}개</td>
                <td class="font-mono">${d.afternoon_peak}개</td>
                <td class="font-mono font-bold">${d.avg_rate_pct}%</td>
            </tr>
        `).join('');
    },

    renderHourlyTable() {
        const tbody = document.getElementById('hourlySummaryTbody');
        if (!tbody || !this.rawData) return;
        const list = this.rawData.hourly_summary || [];

        tbody.innerHTML = list.map(h => {
            if (h.is_future) {
                return `
                    <tr class="text-muted">
                        <td class="font-mono font-bold">${h.hour}</td>
                        <td>-</td>
                        <td>-</td>
                        <td>-</td>
                        <td>-</td>
                        <td>-</td>
                    </tr>
                `;
            }
            return `
                <tr>
                    <td class="font-mono font-bold text-dark">${h.hour}</td>
                    <td class="font-mono font-bold text-primary">${h.avg_usage}개</td>
                    <td class="font-mono font-bold text-danger">${h.peak_usage}개</td>
                    <td class="font-mono text-muted">${h.min_usage}개</td>
                    <td class="font-mono font-bold">${h.rate_pct}%</td>
                    <td><span class="badge badge-light">${h.avg_usage}개 평균</span></td>
                </tr>
            `;
        }).join('');
    },

    renderMatrixTable() {
        const thead = document.getElementById('dailyMatrixThead');
        const tbody = document.getElementById('dailyMatrixTbody');
        const countBadge = document.getElementById('matrixStatsCountBadge');
        if (!thead || !tbody || !this.rawData) return;

        const series15m = this.rawData.time_series_15m || [];
        const rawDailySummary = this.rawData.daily_summary || [];
        const dailySummary = rawDailySummary.filter(d => (d.peak_usage || 0) > 0 || (d.avg_usage || 0) > 0);

        const total = this.rawData.total_limit || 51;
        const critThresh = this.rawData.critical_threshold !== undefined ? this.rawData.critical_threshold : (total - 1);
        const warnThresh = this.rawData.warning_threshold !== undefined ? this.rawData.warning_threshold : (total - 6);

        const matrixLegendPeak = document.getElementById('matrixLegendPeak');
        if (matrixLegendPeak) {
            matrixLegendPeak.textContent = `${critThresh}개 이상 (피크/포화)`;
        }
        const matrixLegendWarning = document.getElementById('matrixLegendWarning');
        if (matrixLegendWarning) {
            matrixLegendWarning.textContent = `${warnThresh}~${critThresh - 1}개 주의`;
        }

        if (countBadge) {
            countBadge.textContent = `${dailySummary.length}일간 분석`;
        }

        if (dailySummary.length === 0 || series15m.length === 0) {
            thead.innerHTML = '<tr><th class="sticky-col-date">일자</th><th>데이터 없음</th></tr>';
            tbody.innerHTML = '<tr><td colspan="2" class="text-muted p-3">조회된 기간의 사용량 데이터가 없습니다.</td></tr>';
            return;
        }

        const timeHeaders = series15m.map(s => `<th>${s.time}</th>`).join('');
        thead.innerHTML = `
            <tr>
                <th class="sticky-col-date">일자</th>
                ${timeHeaders}
                <th class="text-danger" style="background:#162032; border-left: 2px solid rgba(255,255,255,0.15);">일일 Peak</th>
                <th class="text-primary" style="background:#162032;">일일 평균</th>
                <th style="background:#162032;">오전 Peak</th>
                <th style="background:#162032;">오후 Peak</th>
                <th class="text-danger" style="background:#162032; border-left: 1px solid rgba(255,255,255,0.15);" title="08:00~17:00 업무시간 슬롯 중 ${critThresh}개 이상 점유 슬롯 수 및 유효 슬롯 대비 비율(%) (점심시간 12:15~12:45 제외)">${critThresh}개 이상 비율</th>
            </tr>
        `;

        tbody.innerHTML = dailySummary.map(d => {
            const dateYmd = d.date_ymd || (d.date ? d.date.replace(/-/g, '') : '');
            
            const slotCells = series15m.map(s => {
                const byDate = s.by_date || {};
                const val = byDate[dateYmd];

                if (val === null || val === undefined) {
                    return '<td><span class="text-muted" style="opacity:0.3;">-</span></td>';
                }

                if (val >= critThresh) {
                    return `<td><span class="matrix-cell-peak-50" title="${d.date} ${s.time}: ${val}개 (${critThresh}개 이상 포화)">${val}</span></td>`;
                } else if (val >= warnThresh) {
                    return `<td><span class="matrix-cell-warning-45" title="${d.date} ${s.time}: ${val}개 (피크 주의)">${val}</span></td>`;
                } else if (val > 0) {
                    return `<td><span class="matrix-cell-normal font-mono">${val}</span></td>`;
                } else {
                    return '<td><span class="text-muted" style="opacity:0.35;">0</span></td>';
                }
            }).join('');

            const peakClass = (d.peak_usage >= critThresh) ? 'matrix-cell-peak-50' : ((d.peak_usage >= warnThresh) ? 'matrix-cell-warning-45' : 'font-mono font-bold text-danger');

            // 50개 이상 슬롯 수 및 전체 슬롯 대비 비율 계산 (08:00~17:00 기준, 점심시간 12:15, 12:30, 12:45 제외, 백엔드 값 우선, 미제공 시 프론트엔드 fallback)
            let count50 = d.slots_over_threshold_count !== undefined ? d.slots_over_threshold_count : d.slots_over_50_count;
            let totalSlots = d.total_slots;
            let pct50 = d.slots_over_threshold_pct !== undefined ? d.slots_over_threshold_pct : d.slots_over_50_pct;

            if (count50 === undefined || totalSlots === undefined || pct50 === undefined) {
                let measuredSlots = 0;
                let c50 = 0;
                series15m.forEach(s => {
                    if (s.time < '08:00' || s.time > '17:00') return;
                    // 점심시간(12:15, 12:30, 12:45) 제외 (분모, 분자 모두 제외)
                    if (s.time > '12:00' && s.time < '13:00') return;
                    const val = (s.by_date || {})[dateYmd];
                    if (val !== null && val !== undefined) {
                        measuredSlots++;
                        if (val >= critThresh) c50++;
                    }
                });
                count50 = c50;
                totalSlots = measuredSlots;
                pct50 = measuredSlots > 0 ? Number(((c50 / measuredSlots) * 100).toFixed(1)) : 0;
            }

            const pct50Class = (pct50 >= 60)
                ? 'matrix-cell-peak-50'
                : ((pct50 >= 40) ? 'matrix-cell-warning-45' : 'matrix-cell-normal font-mono');
            const peak50Badge = (totalSlots > 0 && count50 > 0)
                ? `<span class="${pct50Class}" style="padding: 2px 6px; font-weight: bold;" title="${d.date}: ${critThresh}개 이상 슬롯 ${count50}개 / 유효 ${totalSlots}개 (점심시간 12:15~12:45 제외)">${pct50}%</span> <span class="text-muted font-mono" style="font-size: 0.72rem;">(${count50}/${totalSlots})</span>`
                : (totalSlots > 0
                    ? `<span class="text-muted font-mono" style="opacity: 0.45;" title="${d.date}: ${critThresh}개 이상 슬롯 없음 (0/${totalSlots}) (점심시간 12:15~12:45 제외)">0.0% <span style="font-size:0.72rem;">(0/${totalSlots})</span></span>`
                    : `<span class="text-muted font-mono" style="opacity: 0.35;" title="${d.date}: 유효 근무시간 슬롯 없음">-</span>`);

            return `
                <tr>
                    <td class="sticky-col-date font-mono font-bold text-dark">${d.date}</td>
                    ${slotCells}
                    <td style="border-left: 2px solid rgba(255,255,255,0.15);"><span class="${peakClass}">${d.peak_usage}개</span></td>
                    <td class="font-mono font-bold text-primary">${d.avg_usage}개</td>
                    <td class="font-mono text-muted">${d.morning_peak}개</td>
                    <td class="font-mono text-muted">${d.afternoon_peak}개</td>
                    <td style="border-left: 1px solid rgba(255,255,255,0.15); white-space: nowrap;">${peak50Badge}</td>
                </tr>
            `;
        }).join('');
    },

    renderInsights() {
        const listEl = document.getElementById('peakSummaryList');
        if (!listEl || !this.rawData) return;

        const p = this.rawData.peak_summary || {};
        const pMax = p.period_max || {};
        const topDept = p.top_dept || {};

        if (!pMax.usage || pMax.usage === 0) {
            listEl.innerHTML = `
                <li>선택하신 기간에는 License 사용 이력이 존재하지 않습니다 (주말/공휴일 또는 미가동).</li>
                <li>정규 근무시간(08:00~17:00) 점유량 및 동시 사용자 수가 모두 0건(0.0%)입니다.</li>
            `;
            return;
        }

        const topHours = topDept.total_hours !== undefined ? topDept.total_hours : ((topDept.count || 0) * 0.25);
        listEl.innerHTML = `
            <li>조회 기간 내 50개 이상 사용 비율은 <strong>${p.period_over_50_pct || 0}%</strong>를 차지하였습니다.</li>
            <li>50개 이상 사용 비율이 <strong>60% 이상</strong>인 고부하 일수는 조회 기간 총 <strong>${p.total_days_count || 0}일</strong> 중 <strong>${p.days_over_60_pct_count || 0}일</strong> (${p.days_over_60_pct_rate || 0}%)입니다.</li>
            <li>정규 근무시간(08:00~17:00) 전체 평균 점유량은 <strong>${p.work_hours_avg || 0}개</strong> (가동률 ${p.work_hours_rate || 0}%)입니다.</li>
            <li>최다 License 점유 부서는 <strong>${topDept.dept_name || '-'}</strong> (누적 점유시간 <strong>${topHours}h</strong>, 비중 ${topDept.share_pct || 0}%)입니다.</li>
        `;
    }
};

// =========================================================================
// 7. Requests Module (Accordion 4: License 초과 요청 및 대기 현황)
// =========================================================================
const RequestsModule = {
    dateController: null,
    rawData: null,
    chart: null,
    deptChart: null,
    currentDeptMode: 'top6',

    init() {
        const maxPastDate = App.getMaxAllowedPastDate();
        this.dateController = new DateRangeController(
            'requestStartDateInput',
            'requestEndDateInput',
            'requestPresetSelect',
            () => this.search(),
            {
                maxDate: maxPastDate,
                getBaseLatestDate: () => App.getMaxAllowedPastDate()
            }
        );

        document.getElementById('btnSearchRequests')?.addEventListener('click', () => this.search());
        this.initDeptToggles();
        this.initExports();
    },

    initDeptToggles() {
        const top6Btn = document.getElementById('btnReqDeptModeTop6');
        const allBtn = document.getElementById('btnReqDeptModeAll');
        if (top6Btn && allBtn) {
            top6Btn.addEventListener('click', () => {
                top6Btn.classList.add('active');
                allBtn.classList.remove('active');
                this.currentDeptMode = 'top6';
                if (this.rawData) this.renderDeptChart();
            });
            allBtn.addEventListener('click', () => {
                allBtn.classList.add('active');
                top6Btn.classList.remove('active');
                this.currentDeptMode = 'all';
                if (this.rawData) this.renderDeptChart();
            });
        }
    },

    initExports() {
        const doExport = (format) => {
            const s = this.dateController.getStartDate();
            const e = this.dateController.getEndDate();
            const params = new URLSearchParams({
                format: format,
                type: 'requests',
                start_date: s,
                end_date: e
            });
            window.location.href = `/api/v1/license/export?${params.toString()}`;
        };

        document.getElementById('btnExportRequestsCsv')?.addEventListener('click', () => doExport('csv'));
        document.getElementById('btnExportRequestsExcel')?.addEventListener('click', () => doExport('excel'));
    },

    async search() {
        const s = this.dateController.getStartDate();
        const e = this.dateController.getEndDate();
        const searchBtn = document.getElementById('btnSearchRequests');
        const countBadge = document.getElementById('requestsCountBadge');
        const tbody = document.getElementById('requestsBody');

        // 로딩 중 UI 피드백 설정 (점 애니메이션 및 펄스 배지 적용)
        if (searchBtn) {
            searchBtn.disabled = true;
            searchBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 분석 중<span class="loading-dots"></span>';
        }
        if (countBadge) {
            countBadge.className = 'badge badge-loading';
            countBadge.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 이력 조회 중<span class="loading-dots"></span>';
        }
        if (tbody && (!this.rawData || !this.rawData.items || this.rawData.items.length === 0)) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="10" class="text-center py-5">
                        <div class="loading-spinner-box">
                            <i class="fa-solid fa-circle-notch fa-spin fa-2x"></i>
                            <div class="loading-title">지정하신 기간(${Utils.escapeHtml(s)} ~ ${Utils.escapeHtml(e)})의 DSLS 초과 요청 로그를 정밀 분석 중입니다<span class="loading-dots"></span></div>
                            <div class="loading-sub">다중 일자 로그 파일 파싱 및 동시 대기자 수 클러스터링 계산이 진행 중입니다.</div>
                        </div>
                    </td>
                </tr>
            `;
        }

        try {
            const res = await fetch(`/api/v1/license/requests?start_date=${encodeURIComponent(s)}&end_date=${encodeURIComponent(e)}&_t=${Date.now()}`, { cache: 'no-store' });
            const data = await res.json();
            this.rawData = data;
            this.render();
        } catch (err) {
            console.error('RequestsModule search error:', err);
            if (countBadge) {
                countBadge.className = 'badge badge-danger';
                countBadge.textContent = '조회 실패';
            }
        } finally {
            if (searchBtn) {
                searchBtn.disabled = false;
                searchBtn.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i> 이력 조회';
            }
        }
    },

    render() {
        if (!this.rawData) return;
        this.renderKpis();
        this.renderChart();
        this.renderDeptChart();
        this.renderInsights();
        this.renderTable();
    },

    renderKpis() {
        const countBadge = document.getElementById('requestsCountBadge');
        const kpiTotal = document.getElementById('kpiReqTotalCount');
        const kpiTotalSub = document.getElementById('kpiReqTotalSub');
        const kpiUsers = document.getElementById('kpiReqUserCount');
        const kpiUsersSub = document.getElementById('kpiReqUserSub');
        const kpiAvg = document.getElementById('kpiReqAvgWaiters');
        const kpiAvgSub = document.getElementById('kpiReqAvgWaitersSub');
        const kpiPeak = document.getElementById('kpiReqPeakWaiters');
        const kpiPeakSub = document.getElementById('kpiReqPeakWaitersSub');

        const totalSessions = this.rawData.total_sessions !== undefined ? this.rawData.total_sessions : (this.rawData.total_requests || 0);

        if (countBadge) {
            if (totalSessions > 0) {
                countBadge.className = 'badge badge-purple';
                countBadge.textContent = `총 ${totalSessions}건`;
            } else {
                countBadge.className = 'badge badge-green';
                countBadge.textContent = `총 0건 (정상 운영)`;
            }
        }

        const denialSlotRatePct = this.rawData.denial_slot_rate_pct !== undefined ? this.rawData.denial_slot_rate_pct : 0.0;
        const denialSlotsCount = this.rawData.denial_slots_count !== undefined ? this.rawData.denial_slots_count : 0;
        const totalWorkSlots = this.rawData.total_work_slots !== undefined ? this.rawData.total_work_slots : 0;
        const dailyAvgSessions = this.rawData.daily_avg_sessions !== undefined ? this.rawData.daily_avg_sessions : 0.0;

        if (kpiTotal) kpiTotal.textContent = `${denialSlotRatePct}%`;
        if (kpiTotalSub) kpiTotalSub.textContent = `병목 슬롯: ${denialSlotsCount}/${totalWorkSlots}개 (일평균 ${dailyAvgSessions}건 / 총 ${totalSessions}건)`;

        const affectedUserRatePct = this.rawData.affected_user_rate_pct !== undefined ? this.rawData.affected_user_rate_pct : 0.0;
        const uniqueUsersCount = this.rawData.unique_users_count || 0;
        const totalActiveUsers = this.rawData.total_active_users || 0;
        const topDept = this.rawData.top_dept || '-';
        const topDeptPct = this.rawData.top_dept_pct || 0;

        if (kpiUsers) kpiUsers.textContent = `${affectedUserRatePct}% (${uniqueUsersCount}명)`;
        if (kpiUsersSub) {
            kpiUsersSub.textContent = `실사용자 ${totalActiveUsers}명 중 (최다: ${topDept} ${topDeptPct}%)`;
        }

        const avgWait = this.rawData.avg_waiters !== undefined ? this.rawData.avg_waiters : 0.0;
        if (kpiAvg) kpiAvg.textContent = avgWait > 0 ? `+${avgWait.toFixed(1)}명` : `0.0명`;
        if (kpiAvgSub) kpiAvgSub.textContent = this.rawData.is_multi_date ? '초과 발생 슬롯 평균 동시 대기' : '초과 발생 슬롯 평균';

        const peakWait = this.rawData.peak_waiters || 0;
        if (kpiPeak) kpiPeak.textContent = peakWait > 0 ? `+${peakWait}명` : `0명`;
        if (kpiPeakSub) kpiPeakSub.textContent = this.rawData.is_multi_date ? '전체 슬롯 중 실제 최대 동시 대기' : '단일 슬롯 최대 동시 대기';
    },

    renderChart() {
        const canvas = document.getElementById('requestsChart');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const series = this.rawData.timeseries_15m || [];
        const labels = series.map(d => d.time);
        const denialData = series.map(d => d.denial_count);
        const waiterData = series.map(d => d.unique_users);

        if (this.chart) {
            this.chart.destroy();
        }

        const maxVal = Math.max(
            ...denialData.filter(v => v !== null && !isNaN(v)),
            ...waiterData.filter(v => v !== null && !isNaN(v)),
            4
        );
        const yMax = Math.ceil(maxVal * 1.25);

        this.chart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Denial 발생 건수 (건)',
                        data: denialData,
                        backgroundColor: 'rgba(239, 68, 68, 0.65)',
                        borderColor: '#ef4444',
                        borderWidth: 1,
                        borderRadius: 3,
                        order: 2
                    },
                    {
                        label: this.rawData.is_multi_date ? '피크 동시 대기 (명)' : '동시 대기 인원 (명)',
                        data: waiterData,
                        type: 'line',
                        borderColor: '#a855f7',
                        backgroundColor: 'rgba(168, 85, 247, 0.15)',
                        borderWidth: 2,
                        pointRadius: 2.5,
                        pointBackgroundColor: '#a855f7',
                        tension: 0,
                        fill: false,
                        order: 1
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            afterBody: (context) => {
                                const idx = context[0].dataIndex;
                                const item = series[idx];
                                if (!item) return '';
                                if (this.rawData.is_multi_date) {
                                    return `15분 슬롯: ${item.time}\n기간 합계 Denial: ${item.denial_count}건\n동시 대기 피크: ${item.unique_users}명 (발생 시 평균: ${item.avg_waiters || item.unique_users}명)\n재시도 포함 총시도: ${item.raw_requests}회`;
                                }
                                return `15분 슬롯: ${item.time}\nDenial 건수: ${item.denial_count}건\n재시도 포함 총시도: ${item.raw_requests}회\n동시 대기자: ${item.unique_users}명`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255, 255, 255, 0.05)' },
                        ticks: {
                            color: '#94a3b8',
                            font: { size: 10 },
                            maxRotation: 0,
                            minRotation: 0,
                            autoSkip: false,
                            callback(val, index) {
                                const label = this.getLabelForValue ? this.getLabelForValue(val) : labels[val];
                                if (!label) return '';
                                return (label.endsWith(':00') || index === 0 || index === labels.length - 1) ? label : '';
                            }
                        }
                    },
                    y: {
                        min: 0,
                        max: yMax,
                        grid: { color: 'rgba(255, 255, 255, 0.08)' },
                        ticks: {
                            color: '#94a3b8',
                            font: { size: 11 },
                            stepSize: 1,
                            precision: 0
                        }
                    }
                }
            }
        });
    },

    renderDeptChart() {
        const canvas = document.getElementById('reqDeptPieChart');
        const legendWrapper = document.getElementById('reqDeptLegendWrapper');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');

        if (this.deptChart) {
            this.deptChart.destroy();
            this.deptChart = null;
        }

        let deptDist = this.rawData?.dept_distribution || [];
        if (deptDist.length === 0 && Array.isArray(this.rawData?.items) && this.rawData.items.length > 0) {
            const deptMap = {};
            this.rawData.items.forEach(it => {
                const dName = it.dept_name || '미등록';
                const uKey = it.user_key || it.user_name || it.hostname || '-';
                const retryCnt = it.retry_count || 1;
                if (!deptMap[dName]) {
                    deptMap[dName] = { dept_name: dName, count: 0, raw_requests: 0, userMap: {} };
                }
                deptMap[dName].count += 1;
                deptMap[dName].raw_requests += retryCnt;
                if (!deptMap[dName].userMap[uKey]) {
                    deptMap[dName].userMap[uKey] = {
                        user_name: it.user_name || '-',
                        emp_no: it.emp_no || '-',
                        hostname: it.hostname || '-',
                        count: 0,
                        raw_requests: 0
                    };
                }
                deptMap[dName].userMap[uKey].count += 1;
                deptMap[dName].userMap[uKey].raw_requests += retryCnt;
            });
            const totalItems = this.rawData.items.length;
            deptDist = Object.values(deptMap)
                .sort((a, b) => b.count - a.count)
                .map(d => {
                    const uList = Object.values(d.userMap).sort((a, b) => (b.count - a.count) || (b.raw_requests - a.raw_requests));
                    return {
                        dept_name: d.dept_name,
                        share_pct: totalItems > 0 ? Number(((d.count / totalItems) * 100).toFixed(1)) : 0.0,
                        count: d.count,
                        raw_requests: d.raw_requests,
                        user_count: uList.length,
                        users: uList
                    };
                });
        }

        if (deptDist.length === 0) {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            if (legendWrapper) legendWrapper.innerHTML = '<div class="text-muted text-xs p-3">초과 요청(Denial) 발생 부서 없음</div>';
            return;
        }

        let labels = [];
        let dataVals = [];
        let bgColors = [];
        let legendItems = [];

        const totalDenials = deptDist.reduce((acc, cur) => acc + cur.count, 0);

        if (this.currentDeptMode === 'all') {
            labels = deptDist.map(d => `${d.dept_name} (${d.user_count || 0}명)`);
            dataVals = deptDist.map(d => d.count);
            bgColors = deptDist.map((_, i) => {
                const hue = Math.round((i * 137.508) % 360);
                return `hsl(${hue}, 70%, 48%)`;
            });

            legendItems = deptDist.map((d, i) => {
                const pct = totalDenials > 0 ? ((d.count / totalDenials) * 100).toFixed(1) : '0.0';
                const uCount = d.user_count || 0;
                return {
                    index: i,
                    name: `${d.dept_name} (${uCount}명)`,
                    count: d.count,
                    rawRequests: d.raw_requests || d.count,
                    userCount: uCount,
                    users: d.users || [],
                    pct: pct,
                    color: bgColors[i]
                };
            });
        } else {
            const topDepts = deptDist.slice(0, 6);
            const others = deptDist.slice(6);
            const otherCount = others.reduce((acc, cur) => acc + cur.count, 0);
            const otherRawRequests = others.reduce((acc, cur) => acc + (cur.raw_requests || cur.count), 0);
            const otherUserCount = others.reduce((acc, cur) => acc + (cur.user_count || 0), 0);

            labels = topDepts.map(d => `${d.dept_name} (${d.user_count || 0}명)`);
            dataVals = topDepts.map(d => d.count);

            const defaultPalette = [
                '#2563eb', '#0284c7', '#0d9488', '#16a34a', '#d97706', '#dc2626', '#64748b'
            ];

            if (otherCount > 0) {
                labels.push(`기타 부서 (${otherUserCount}명)`);
                dataVals.push(otherCount);
            }

            bgColors = defaultPalette.slice(0, labels.length);

            legendItems = topDepts.map((d, i) => {
                const pct = totalDenials > 0 ? ((d.count / totalDenials) * 100).toFixed(1) : '0.0';
                const uCount = d.user_count || 0;
                return {
                    index: i,
                    name: `${d.dept_name} (${uCount}명)`,
                    count: d.count,
                    rawRequests: d.raw_requests || d.count,
                    userCount: uCount,
                    users: d.users || [],
                    pct: pct,
                    color: bgColors[i]
                };
            });

            if (otherCount > 0) {
                const otherPct = totalDenials > 0 ? ((otherCount / totalDenials) * 100).toFixed(1) : '0.0';
                const otherUsers = others.flatMap(d => (d.users || []).map(u => ({
                    ...u,
                    dept_name: d.dept_name
                }))).sort((a, b) => (b.count || 0) - (a.count || 0));
                legendItems.push({
                    index: labels.length - 1,
                    name: `기타 부서 (${otherUserCount}명)`,
                    count: otherCount,
                    rawRequests: otherRawRequests,
                    userCount: otherUserCount,
                    users: otherUsers,
                    pct: otherPct,
                    color: bgColors[labels.length - 1]
                });
            }
        }

        if (legendWrapper) {
            legendWrapper.innerHTML = `
                <ul class="dept-legend-list" style="list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:6px;">
                    ${legendItems.map(item => {
                        const userRowsHtml = (item.users && item.users.length > 0)
                            ? item.users.map((u, uIdx) => {
                                const subMeta = [
                                    u.dept_name,
                                    (u.emp_no && u.emp_no !== '-' ? u.emp_no : null),
                                    u.hostname
                                ].filter(Boolean).join(' · ');
                                const uCnt = Number(u.count || 0);
                                const uRaw = Number(u.raw_requests || uCnt);
                                const uMetricText = uRaw > uCnt ? `${uCnt}건 (${uRaw}회)` : `${uCnt}건`;
                                return `
                                    <div style="display:flex; align-items:center; justify-content:space-between; gap:6px; padding:2px 0; border-bottom:1px dashed rgba(128,128,128,0.15); text-align:left;">
                                        <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${Utils.escapeHtml(u.user_name)} (${Utils.escapeHtml(subMeta)})">
                                            <strong style="font-weight:600;">${uIdx + 1}. ${Utils.escapeHtml(u.user_name)}</strong>
                                            <span style="opacity:0.75; font-size:10px; margin-left:3px;">(${Utils.escapeHtml(subMeta)})</span>
                                        </span>
                                        <span style="white-space:nowrap; font-family:monospace; font-size:10.5px; opacity:0.85; flex-shrink:0;">${uMetricText}</span>
                                    </div>
                                `;
                            }).join('')
                            : '<div style="opacity:0.6; font-size:10.5px; padding:2px 0; text-align:left;">조회된 사용자 내역 없음</div>';

                        return `
                            <li class="dept-legend-item" data-dept-idx="${item.index}" title="${Utils.escapeHtml(item.name)}: 초과 요청 ${item.count}건 / 총 시도 ${item.rawRequests}회 (${item.pct}%)" style="display:flex; align-items:center; cursor:pointer; font-size:12px; padding:2px 4px; border-radius:3px; user-select:none;">
                                <span class="dept-legend-color" style="display:inline-block !important; width:13px !important; height:13px !important; min-width:13px !important; min-height:13px !important; background-color:${item.color} !important; border-radius:2px !important; flex-shrink:0 !important; margin-right:8px !important;"></span>
                                <span class="dept-legend-text" style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${Utils.escapeHtml(item.name)}</span>
                                <span class="dept-legend-hours" style="margin-left:auto; padding-left:8px; font-size:11px; opacity:0.85; white-space:nowrap;">${item.count}건 <span style="font-size:10px;">(${item.pct}%)</span></span>
                            </li>
                            <li class="dept-user-sublist" data-sublist-idx="${item.index}" style="display:none; list-style:none; margin:0 0 4px 21px; padding:4px 8px; border-left:2px solid ${item.color}; background:rgba(128,128,128,0.06); border-radius:0 4px 4px 0; font-size:11px; text-align:left;">
                                ${userRowsHtml}
                            </li>
                        `;
                    }).join('')}
                </ul>
            `;

            const items = legendWrapper.querySelectorAll('.dept-legend-item');
            items.forEach(el => {
                const idx = parseInt(el.dataset.deptIdx, 10);
                const sublistEl = legendWrapper.querySelector(`.dept-user-sublist[data-sublist-idx="${idx}"]`);

                el.addEventListener('click', () => {
                    if (sublistEl) {
                        sublistEl.style.display = sublistEl.style.display === 'none' ? 'block' : 'none';
                    }
                });

                el.addEventListener('mouseenter', () => {
                    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
                    el.style.backgroundColor = isLight ? 'rgba(0, 0, 0, 0.08)' : 'rgba(255, 255, 255, 0.15)';
                    el.style.fontWeight = '600';
                    if (this.deptChart) {
                        this.deptChart.setActiveElements([{ datasetIndex: 0, index: idx }]);
                        this.deptChart.tooltip.setActiveElements([{ datasetIndex: 0, index: idx }], { x: 0, y: 0 });
                        this.deptChart.update();
                    }
                });
                el.addEventListener('mouseleave', () => {
                    el.style.backgroundColor = 'transparent';
                    el.style.fontWeight = 'normal';
                    if (this.deptChart) {
                        this.deptChart.setActiveElements([]);
                        this.deptChart.tooltip.setActiveElements([], { x: 0, y: 0 });
                        this.deptChart.update();
                    }
                });
            });
        }

        this.deptChart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: dataVals,
                    backgroundColor: bgColors,
                    borderWidth: 1,
                    borderColor: '#ffffff',
                    hoverOffset: 5
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '60%',
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        callbacks: {
                            label(context) {
                                const val = context.parsed;
                                const pct = totalDenials > 0 ? ((val / totalDenials) * 100).toFixed(1) : 0;
                                return ` ${context.label}: 초과 요청 ${val}건 (${pct}%)`;
                            }
                        }
                    }
                }
            }
        });
    },

    renderInsights() {
        const listEl = document.getElementById('reqPeakSummaryList');
        if (!listEl || !this.rawData) return;

        const totalSessions = this.rawData.total_sessions !== undefined ? this.rawData.total_sessions : (this.rawData.total_requests || 0);
        if (!totalSessions || totalSessions === 0) {
            listEl.innerHTML = `
                <li>선택하신 기간에는 License 부족으로 인한 초과 요청(Denial) 이력이 없습니다 (정상 운영).</li>
                <li>정규 근무시간(08:00~17:00) 내 거절 발생 슬롯 및 대기 인원이 모두 0건(0.0%)입니다.</li>
            `;
            return;
        }

        const denialSlotRatePct = this.rawData.denial_slot_rate_pct !== undefined ? this.rawData.denial_slot_rate_pct : 0.0;
        const denialSlotsCount = this.rawData.denial_slots_count !== undefined ? this.rawData.denial_slots_count : 0;
        const totalWorkSlots = this.rawData.total_work_slots !== undefined ? this.rawData.total_work_slots : 0;
        const dailyAvgSessions = this.rawData.daily_avg_sessions !== undefined ? this.rawData.daily_avg_sessions : 0.0;
        const uniqueUsersCount = this.rawData.unique_users_count || 0;
        const totalActiveUsers = this.rawData.total_active_users || 0;
        const affectedUserRatePct = this.rawData.affected_user_rate_pct !== undefined ? this.rawData.affected_user_rate_pct : 0.0;
        const avgWait = this.rawData.avg_waiters !== undefined ? this.rawData.avg_waiters : 0.0;
        const peakWait = this.rawData.peak_waiters || 0;
        const topDept = this.rawData.top_dept || '-';
        const topDeptCount = this.rawData.top_dept_count || 0;
        const topDeptPct = this.rawData.top_dept_pct || 0;

        listEl.innerHTML = `
            <li>조회 기간 내 초과 요청(Denial) 발생 슬롯 비율은 <strong>${denialSlotRatePct}%</strong> (${denialSlotsCount}/${totalWorkSlots}개 슬롯, 일평균 <strong>${dailyAvgSessions}건</strong>)입니다.</li>
            <li>실제 CATIA 가동 인원 <strong>${totalActiveUsers}명</strong> 중 거절 경험 인원은 <strong>${uniqueUsersCount}명</strong> (영향 비율 <strong>${affectedUserRatePct}%</strong>)입니다.</li>
            <li>초과 발생 시 평균 동시 대기자는 <strong>+${avgWait.toFixed(1)}명</strong>, 단일 슬롯 최대 피크 대기자는 <strong>+${peakWait}명</strong>입니다.</li>
            <li>최다 초과 요청(Denial) 발생 부서는 <strong>${Utils.escapeHtml(topDept)}</strong> (유효 부족 <strong>${topDeptCount}건</strong>, 비중 <strong>${topDeptPct}%</strong>)입니다.</li>
        `;
    },

    renderTable() {
        const tbody = document.getElementById('requestsBody');
        if (!tbody) return;

        const items = this.rawData.items || [];
        if (items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" class="text-center text-muted py-4">조회 기간 동안 License Pool 초과/거절(Denial) 이력이 없습니다. (License 정상 운영)</td></tr>';
            return;
        }

        tbody.innerHTML = items.map(it => {
            const retryColHtml = (it.retry_count && it.retry_count > 1)
                ? `<span class="badge badge-warning font-bold" title="5분 내 연속 ${it.retry_count}회 시도"><i class="fa-solid fa-arrows-rotate"></i> ${it.retry_count}회</span>`
                : `<span class="badge badge-secondary font-mono">1회</span>`;

            return `
            <tr>
                <td class="font-mono font-bold text-dark">${Utils.escapeHtml(it.date)}</td>
                <td class="font-mono font-bold text-danger">${Utils.escapeHtml(it.request_time)}</td>
                <td style="text-align: center;">${retryColHtml}</td>
                <td><span class="dept-badge">${Utils.escapeHtml(it.dept_name)}</span></td>
                <td class="font-bold text-dark">${Utils.escapeHtml(it.user_name)}</td>
                <td class="font-mono text-muted">${Utils.escapeHtml(it.emp_no)}</td>
                <td class="font-mono text-muted">${Utils.escapeHtml(it.hostname)}</td>
                <td class="font-mono text-muted">${Utils.escapeHtml(it.ip)}</td>
                <td><span class="badge badge-primary font-mono">MD2</span></td>
                <td><span class="badge badge-danger">${Utils.escapeHtml(it.status)}</span></td>
            </tr>
            `;
        }).join('');
    }
};

// =========================================================================
// 8. Simulation Module (Accordion 5: License 권장 증설 Dashboard)
// =========================================================================
const SimulationModule = {
    dateController: null,
    rawData: null,
    chart: null,
    currentSeats: 1,
    baseLimit: 51,
    debouncedSearch: null,

    init() {
        const maxPastDate = App.getMaxAllowedPastDate();
        this.dateController = new DateRangeController(
            'simStartDateInput',
            'simEndDateInput',
            'simPresetSelect',
            () => this.search(),
            {
                maxDate: maxPastDate,
                getBaseLatestDate: () => App.getMaxAllowedPastDate()
            }
        );

        this.debouncedSearch = Utils.debounce(() => this.search(), 200);

        // Base Limit Input
        const baseLimitInput = document.getElementById('simBaseLimitInput');
        if (baseLimitInput) {
            baseLimitInput.addEventListener('change', (e) => {
                this.setBaseLimit(parseInt(e.target.value, 10), true);
            });
            baseLimitInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    baseLimitInput.blur();
                }
            });
        }

        // Stepper, Direct Number Input & Slider controls
        const slider = document.getElementById('simSeatRange');
        const numInput = document.getElementById('simSeatNumberInput');
        const btnDec = document.getElementById('btnSimSeatDec');
        const btnInc = document.getElementById('btnSimSeatInc');
        const btnOpt = document.getElementById('btnApplyOptimalSim');

        if (slider) {
            slider.addEventListener('input', (e) => {
                this.setSeats(parseInt(e.target.value, 10), false);
                this.debouncedSearch();
            });
        }
        if (numInput) {
            numInput.addEventListener('change', (e) => {
                this.setSeats(parseInt(e.target.value, 10), true);
            });
            numInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    numInput.blur();
                }
            });
        }
        if (btnDec) {
            btnDec.addEventListener('click', () => {
                this.setSeats(this.currentSeats - 1, true);
            });
        }
        if (btnInc) {
            btnInc.addEventListener('click', () => {
                this.setSeats(this.currentSeats + 1, true);
            });
        }
        if (btnOpt) {
            btnOpt.addEventListener('click', () => {
                if (this.rawData && this.rawData.optimal_seats_recommended !== undefined) {
                    this.setSeats(this.rawData.optimal_seats_recommended, true);
                }
            });
        }

        document.getElementById('btnSearchSim')?.addEventListener('click', () => this.search());
        this.initExports();
    },

    setBaseLimit(val, triggerSearch = true) {
        val = isNaN(val) ? 51 : Math.max(1, Math.min(200, val));
        this.baseLimit = val;
        const baseLimitInput = document.getElementById('simBaseLimitInput');
        if (baseLimitInput) baseLimitInput.value = val;
        if (triggerSearch) {
            this.search();
        }
    },

    setSeats(val, triggerSearch = true) {
        val = isNaN(val) ? 1 : Math.max(0, Math.min(50, val));
        this.currentSeats = val;
        const slider = document.getElementById('simSeatRange');
        const numInput = document.getElementById('simSeatNumberInput');
        if (slider) {
            if (val > parseInt(slider.max, 10)) {
                slider.max = Math.max(50, val);
            }
            slider.value = Math.min(parseInt(slider.max, 10), val);
        }
        if (numInput) numInput.value = val;
        if (triggerSearch) {
            this.search();
        }
    },

    initExports() {
        const doExport = (format) => {
            const s = this.dateController.getStartDate();
            const e = this.dateController.getEndDate();
            const params = new URLSearchParams({
                format: format,
                type: 'simulation',
                start_date: s,
                end_date: e,
                base_limit: this.baseLimit.toString(),
                add_seats: this.currentSeats.toString(),
                search: this.currentSeats.toString()
            });
            window.location.href = `/api/v1/license/export?${params.toString()}`;
        };

        document.getElementById('btnExportSimCsv')?.addEventListener('click', () => doExport('csv'));
        document.getElementById('btnExportSimExcel')?.addEventListener('click', () => doExport('excel'));
    },

    async search() {
        const s = this.dateController.getStartDate();
        const e = this.dateController.getEndDate();
        const searchBtn = document.getElementById('btnSearchSim');

        if (searchBtn) {
            searchBtn.disabled = true;
            searchBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 분석 중<span class="loading-dots"></span>';
        }

        try {
            const res = await fetch(`/api/v1/license/simulation?start_date=${encodeURIComponent(s)}&end_date=${encodeURIComponent(e)}&add_seats=${this.currentSeats}&base_limit=${this.baseLimit}&_t=${Date.now()}`, { cache: 'no-store' });
            const data = await res.json();
            this.rawData = data;
            this.render();
        } catch (err) {
            console.error('SimulationModule search error:', err);
        } finally {
            if (searchBtn) {
                searchBtn.disabled = false;
                searchBtn.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i> 예측 분석 실행';
            }
        }
    },

    render() {
        if (!this.rawData) return;
        this.renderKpis();
        this.renderNotice();
        this.renderChart();
        this.renderScenarioTable();
    },

    renderKpis() {
        const kpiDemand = document.getElementById('kpiSimPeakDemand');
        const kpiDemandSub = document.getElementById('kpiSimPeakDemandSub');
        const kpiRate = document.getElementById('kpiSimResolutionRate');
        const kpiRateSub = document.getElementById('kpiSimResolutionRateSub');
        const kpiOpt = document.getElementById('kpiSimOptimalSeat');
        const kpiOptSub = document.getElementById('kpiSimOptimalSeatSub');

        const curr = this.rawData.current_scenario || {};
        const maxDemand = this.rawData.max_potential_demand || this.baseLimit;
        const baseLimit = this.rawData.base_limit || this.baseLimit;
        const overLimit = Math.max(0, maxDemand - baseLimit);

        if (kpiDemand) kpiDemand.textContent = `${maxDemand}개`;
        if (kpiDemandSub) kpiDemandSub.textContent = `기준 보유량 ${baseLimit}개 대비 +${overLimit}개 초과`;

        if (kpiRate) kpiRate.textContent = `${curr.resolution_rate !== undefined ? curr.resolution_rate : 100}%`;
        if (kpiRateSub) kpiRateSub.textContent = `잔여 Denial ${curr.remaining_denials || 0}건 / 유효 부족 ${this.rawData.total_denials_observed || 0}건`;

        const optSeats = this.rawData.optimal_seats_recommended || 0;
        if (kpiOpt) kpiOpt.textContent = `+${optSeats} Copy`;
        if (kpiOptSub) kpiOptSub.textContent = `99% SLA Target (${this.rawData.p99_demand || baseLimit}개 수요 충족)`;
    },

    renderNotice() {
        const noticeEl = document.getElementById('simCalloutNoticeText');
        if (noticeEl && this.rawData.notice) {
            noticeEl.textContent = this.rawData.notice;
        }
    },

    renderChart() {
        const canvas = document.getElementById('simChart');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const series = this.rawData.chart_series_15m || [];

        const labels = series.map(d => d.time);
        const peakDemandData = series.map(d => d.peak_demand);
        const simData = series.map(d => d.peak_simulated);
        const simLimit = this.rawData.simulated_limit || (this.baseLimit + this.currentSeats);
        const limitLineData = new Array(labels.length).fill(simLimit);

        if (this.chart) {
            this.chart.destroy();
        }

        const maxSimVal = Math.max(
            ...peakDemandData.filter(v => v !== null && !isNaN(v)),
            ...simData.filter(v => v !== null && !isNaN(v)),
            0
        );
        const yMax = Math.max(simLimit, Math.ceil(maxSimVal)) + 20;

        this.chart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: '최대 Peak 잠재 수요',
                        data: peakDemandData,
                        borderColor: '#ef4444',
                        backgroundColor: 'rgba(239, 68, 68, 0.15)',
                        borderWidth: 2.5,
                        borderDash: [4, 4],
                        pointRadius: 2.5,
                        pointBackgroundColor: '#ef4444',
                        tension: 0,
                        fill: false
                    },
                    {
                        label: 'Simulation 점유 (가상 증설 반영)',
                        data: simData,
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.15)',
                        borderWidth: 2,
                        pointRadius: 1.5,
                        tension: 0,
                        fill: false
                    },
                    {
                        label: `가상 한도선 (${simLimit}개)`,
                        data: limitLineData,
                        borderColor: '#a855f7',
                        borderWidth: 2,
                        borderDash: [6, 3],
                        pointRadius: 0,
                        fill: false
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false
                },
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        callbacks: {
                            afterBody: (context) => {
                                const idx = context[0].dataIndex;
                                const item = series[idx];
                                if (!item) return '';
                                const baseLimit = this.rawData.base_limit || this.baseLimit;
                                const peakExcess = item.peak_excess !== undefined ? item.peak_excess : Math.max(0, (item.peak_demand || 0) - baseLimit);
                                return `총 Denial: ${item.total_denials}건\n피크 초과수요: +${peakExcess}개\n최대 Peak 잠재수요: ${item.peak_demand}개\nSimulation 점유: ${item.peak_simulated}개`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255, 255, 255, 0.05)' },
                        ticks: {
                            color: '#94a3b8',
                            font: { size: 10 },
                            maxRotation: 0,
                            minRotation: 0,
                            autoSkip: false,
                            callback(val, index) {
                                const label = this.getLabelForValue ? this.getLabelForValue(val) : labels[val];
                                if (!label) return '';
                                return (label.endsWith(':00') || index === 0 || index === labels.length - 1) ? label : '';
                            }
                        }
                    },
                    y: {
                        min: 0,
                        max: yMax,
                        grid: { color: 'rgba(255, 255, 255, 0.08)' },
                        ticks: {
                            color: '#94a3b8',
                            font: { size: 11 },
                            stepSize: 10
                        }
                    }
                }
            }
        });
    },

    renderScenarioTable() {
        const tbody = document.getElementById('simScenarioBody');
        if (!tbody || !this.rawData) return;

        const scenarios = this.rawData.scenarios || [];
        const optSeats = this.rawData.optimal_seats_recommended;

        const maxSeatInScenarios = scenarios.length > 0 ? scenarios[scenarios.length - 1].add_seats : 10;
        const subRange = document.getElementById('simScenarioRangeSubtitle');
        if (subRange) {
            subRange.textContent = `(+0 ~ +${maxSeatInScenarios} Copy)`;
        }

        tbody.innerHTML = scenarios.map(sc => {
            const isSelected = sc.add_seats === this.currentSeats;
            const isOpt = sc.add_seats === optSeats;
            let trClass = '';
            if (isSelected) trClass += ' tr-selected';
            if (isOpt) trClass += ' tr-optimal';

            const slaBadge = sc.sla_met
                ? '<span class="badge badge-success"><i class="fa-solid fa-check"></i> 충족</span>'
                : '<span class="badge badge-secondary"><i class="fa-solid fa-xmark"></i> 미충족</span>';

            return `
                <tr class="${trClass}">
                    <td class="font-bold text-dark">+${sc.add_seats} Copy 증설</td>
                    <td class="font-mono font-bold">${sc.total_limit}개</td>
                    <td class="font-mono ${sc.remaining_denials > 0 ? 'text-danger font-bold' : 'text-muted'}">${sc.remaining_denials}건</td>
                    <td class="font-mono font-bold text-success">${sc.resolved_denials}건</td>
                    <td class="font-mono font-bold ${sc.resolution_rate >= 100 ? 'text-success' : (sc.resolution_rate >= 80 ? 'text-warning' : 'text-danger')}">${sc.resolution_rate}%</td>
                    <td>${slaBadge}</td>
                    <td><span class="badge ${sc.recommendation_class}">${Utils.escapeHtml(sc.recommendation_tag)}</span></td>
                    <td>
                        <button class="btn btn-sm ${isSelected ? 'btn-primary' : 'btn-secondary'}" onclick="SimulationModule.setSeats(${sc.add_seats})">
                            ${isSelected ? '<i class="fa-solid fa-check"></i> 적용중' : '선택'}
                        </button>
                    </td>
                </tr>
            `;
        }).join('');
    }
};
window.SimulationModule = SimulationModule;

// =========================================================================
// 8. Navigation Controller (Sidebar Navigation & Multi-View Routing)
// =========================================================================
const NavigationController = {
    currentViewId: 'viewOverview',
    viewMetadata: {
        'viewOverview': {
            name: '실시간 License 현황',
            desc: 'CATIA MD2 실시간 점유 현황, 모듈 상태 및 유휴 이상 점유 모니터링',
            hash: 'overview',
            onActivate: () => {
                RealtimeModule.load();
                AnomalyModule.search();
                App.triggerResize();
            }
        },
        'viewAnomaly': {
            name: '실시간 License 현황',
            desc: 'CATIA MD2 실시간 점유 현황, 모듈 상태 및 유휴 이상 점유 모니터링',
            hash: 'anomaly',
            onActivate: () => {
                NavigationController.switchView('viewOverview', false);
                const section = document.getElementById('sectionAnomalyOverview');
                if (section) section.scrollIntoView({ behavior: 'smooth' });
            }
        },
        'viewStats': {
            name: '1. 일자 및 시간대별 License 사용량 통계 분석',
            desc: '15분/일자/시간 단위 점유율, 일자별 Matrix, 부서별 점유 비중 분석',
            hash: 'stats',
            onActivate: () => {
                const s = StatsModule.dateController ? StatsModule.dateController.getStartDate() : null;
                const e = StatsModule.dateController ? StatsModule.dateController.getEndDate() : null;
                const includesToday = Utils.isDateRangeIncludingToday(s, e, App.availableDates);
                const isFresh = (Date.now() - (StatsModule.lastFetchedAt || 0)) < 15000;

                if (!StatsModule.rawData || (includesToday && !isFresh)) {
                    StatsModule.search();
                }
                App.triggerResize();
            }
        },
        'viewRequests': {
            name: '2. License 초과 요청 및 대기(Denial) 현황 분석',
            desc: '라이선스 풀 부족으로 인한 대기 및 거부 로그 상세 분석',
            hash: 'requests',
            onActivate: () => {
                if (!RequestsModule.rawData) {
                    RequestsModule.search();
                }
                App.triggerResize();
            }
        },
        'viewSimulation': {
            name: '3. License 권장 증설 예측 Dashboard',
            desc: 'SLA 기반 적정 라이선스 수량 산정 및 추가 도입 시뮬레이션',
            hash: 'simulation',
            onActivate: () => {
                if (!SimulationModule.rawData) {
                    SimulationModule.search();
                }
                App.triggerResize();
            }
        },
        'viewPivot': {
            name: '4. 사용자별 점유 집계 Pivot & 15분 Slot Matrix',
            desc: '사용자/부서별 일자 누적 점유시간 집계 및 15분 슬롯 매트릭스',
            hash: 'pivot',
            onActivate: () => {
                const s = PivotModule.dateController ? PivotModule.dateController.getStartDate() : null;
                const e = PivotModule.dateController ? PivotModule.dateController.getEndDate() : null;
                const includesToday = Utils.isDateRangeIncludingToday(s, e, App.availableDates);
                const isFresh = (Date.now() - (PivotModule.lastFetchedAt || 0)) < 15000;

                if (!PivotModule.rawData || (includesToday && !isFresh)) {
                    PivotModule.search();
                }
                App.triggerResize();
            }
        }
    },

    init() {
        // Nav Links Event Binding
        const navLinks = document.querySelectorAll('.app-sidebar .nav-link[data-view]');
        navLinks.forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const targetView = link.dataset.view;
                if (targetView) {
                    this.switchView(targetView, true);
                    const sidebar = document.getElementById('appSidebar');
                    if (window.innerWidth <= 992 && sidebar) {
                        sidebar.classList.remove('mobile-open');
                    }
                }
            });
        });

        // Unified Sidebar Toggle (Collapse on desktop, Open/Close drawer on mobile)
        const toggleBtn = document.getElementById('btnToggleSidebar');
        const sidebar = document.getElementById('appSidebar');
        const collapseIcon = document.getElementById('sidebarCollapseIcon');
        if (toggleBtn && sidebar) {
            const savedCollapsed = localStorage.getItem('cop_sidebar_collapsed') === 'true';
            if (savedCollapsed && window.innerWidth > 992) {
                sidebar.classList.add('collapsed');
                if (collapseIcon) {
                    collapseIcon.classList.remove('fa-bars-staggered');
                    collapseIcon.classList.add('fa-bars');
                }
            }

            toggleBtn.addEventListener('click', () => {
                if (window.innerWidth <= 992) {
                    sidebar.classList.toggle('mobile-open');
                } else {
                    sidebar.classList.toggle('collapsed');
                    const isCollapsed = sidebar.classList.contains('collapsed');
                    localStorage.setItem('cop_sidebar_collapsed', isCollapsed);
                    if (collapseIcon) {
                        if (isCollapsed) {
                            collapseIcon.classList.remove('fa-bars-staggered');
                            collapseIcon.classList.add('fa-bars');
                        } else {
                            collapseIcon.classList.remove('fa-bars');
                            collapseIcon.classList.add('fa-bars-staggered');
                        }
                    }
                    App.triggerResize();
                }
            });
        }

        // Handle URL Hash on load & hashchange
        window.addEventListener('hashchange', () => {
            this.handleHashChange();
        });

        // Initial Route based on window.location.hash
        const initialHash = (window.location.hash || '').replace('#', '').toLowerCase();
        let targetView = 'viewOverview';
        if (initialHash) {
            for (const [vid, meta] of Object.entries(this.viewMetadata)) {
                if (meta.hash === initialHash) {
                    targetView = vid;
                    break;
                }
            }
        }
        this.switchView(targetView, false);
    },

    handleHashChange() {
        const hash = (window.location.hash || '').replace('#', '').toLowerCase();
        for (const [vid, meta] of Object.entries(this.viewMetadata)) {
            if (meta.hash === hash) {
                this.switchView(vid, false);
                return;
            }
        }
    },

    switchView(viewId, updateHash = true) {
        if (!this.viewMetadata[viewId]) {
            viewId = 'viewOverview';
        }
        this.currentViewId = viewId;
        const meta = this.viewMetadata[viewId];

        // 1. Toggle content views
        const views = document.querySelectorAll('.content-view');
        views.forEach(v => {
            if (v.id === viewId) {
                v.classList.add('active');
            } else {
                v.classList.remove('active');
            }
        });

        // 2. Toggle active nav link
        const navLinks = document.querySelectorAll('.app-sidebar .nav-link');
        navLinks.forEach(link => {
            if (link.dataset.view === viewId) {
                link.classList.add('active');
            } else {
                link.classList.remove('active');
            }
        });

        // 3. Update Header Info
        const nameEl = document.getElementById('currentViewName');
        const descEl = document.getElementById('currentViewDesc');
        if (nameEl && meta.name) nameEl.textContent = meta.name;
        if (descEl && meta.desc) descEl.textContent = meta.desc;

        // 4. Update hash if requested
        if (updateHash && meta.hash) {
            history.pushState(null, '', `#${meta.hash}`);
        }

        // 5. Close mobile sidebar if open
        const sidebar = document.getElementById('appSidebar');
        if (sidebar?.classList.contains('mobile-open')) {
            sidebar.classList.remove('mobile-open');
        }

        // 6. Reset scroll to top of view
        window.scrollTo({ top: 0, behavior: 'instant' });
        const viewsWrapper = document.querySelector('.content-views-wrapper');
        if (viewsWrapper) viewsWrapper.scrollTop = 0;

        // 7. Trigger view activation & chart resize
        if (typeof meta.onActivate === 'function') {
            meta.onActivate();
        } else {
            App.triggerResize();
        }
    }
};
window.NavigationController = NavigationController;

// =========================================================================
// 9. Application Master Coordinator (App)
// =========================================================================
const App = {
    availableDates: [],

    getMaxAllowedPastDate() {
        const curCalendarToday = Utils.getTodayIsoString();
        const calendarYesterday = Utils.addDaysIso(curCalendarToday, -1);

        // 시스템 기준 당일: 가용 로그 데이터가 존재하면 가장 최신 일자, 없으면 캘린더 당일
        const systemToday = (this.availableDates && this.availableDates.length > 0)
            ? this.availableDates[0]
            : curCalendarToday;

        // 시스템 당일보다 엄격하게 과거(<)인 일자 목록 필터링
        const pastDates = (this.availableDates || []).filter(d => d < systemToday);
        const systemYesterday = pastDates[0] || Utils.addDaysIso(systemToday, -1);

        // 당일 기준 이전(전일)은 시스템 전일과 캘린더 전일 중 더 과거(안전한) 날짜 선택
        return (systemYesterday < calendarYesterday) ? systemYesterday : calendarYesterday;
    },

    async init() {
        ThemeController.init();
        this.initAccordions();
        await this.loadInitialMetadata();

        // Initialize all isolated modules
        PivotModule.init();
        AnomalyModule.init();
        StatsModule.init();
        RequestsModule.init();
        SimulationModule.init();

        // Set default 1-day range (최근 1일) for Pivot & Stats
        const defaultEnd = this.availableDates[0] || Utils.getTodayIsoString();
        const defaultStart = defaultEnd;

        PivotModule.dateController.setDates(defaultStart, defaultEnd, '1');
        StatsModule.dateController.setDates(defaultStart, defaultEnd, '1');

        // RequestsModule & SimulationModule: 당일 기준 이전까지만 허용 (전일 D-1)
        const maxPastDate = this.getMaxAllowedPastDate();
        
        RequestsModule.dateController.setMaxDate(maxPastDate);
        RequestsModule.dateController.setDates(maxPastDate, maxPastDate, '1');

        SimulationModule.dateController.setMaxDate(maxPastDate);
        SimulationModule.dateController.setDates(maxPastDate, maxPastDate, '1');

        // Active View Activation via Navigation Routing (단 1회 활성 뷰 즉시 로드)
        NavigationController.init();

        // Background Prefetch for Inactive Tabs (초기 뷰 외 비활성 탭 사전 로딩 - 중복 호출 방지)
        const activeView = NavigationController.currentViewId;
        const prefetchTasks = [];

        if (activeView !== 'viewOverview') {
            prefetchTasks.push(RealtimeModule.load());
            prefetchTasks.push(AnomalyModule.search());
        }
        if (activeView !== 'viewPivot' && !PivotModule.rawData) {
            prefetchTasks.push(PivotModule.search());
        }
        if (activeView !== 'viewStats' && !StatsModule.rawData) {
            prefetchTasks.push(StatsModule.search());
        }
        if (activeView !== 'viewRequests' && !RequestsModule.rawData) {
            prefetchTasks.push(RequestsModule.search());
        }
        if (activeView !== 'viewSimulation' && !SimulationModule.rawData) {
            prefetchTasks.push(SimulationModule.search());
        }

        if (prefetchTasks.length > 0) {
            await Promise.all(prefetchTasks);
        }

        this.triggerResize();

        // Global manual refresh button
        const globalRefreshBtn = document.getElementById('btnRefresh');
        globalRefreshBtn?.addEventListener('click', async () => {
            const icon = globalRefreshBtn.querySelector('i');
            if (icon) icon.classList.add('fa-spin');
            globalRefreshBtn.disabled = true;
            try {
                await this.loadInitialMetadata();
                await Promise.all([
                    RealtimeModule.load(),
                    PivotModule.search(),
                    AnomalyModule.search(),
                    StatsModule.search(),
                    RequestsModule.search(),
                    SimulationModule.search()
                ]);
            } finally {
                if (icon) icon.classList.remove('fa-spin');
                globalRefreshBtn.disabled = false;
                globalRefreshBtn.classList.add('btn-refresh-success');
                setTimeout(() => {
                    globalRefreshBtn.classList.remove('btn-refresh-success');
                }, 600);
            }
        });

        this.lastCheckedDayIso = Utils.getTodayIsoString();
        // 30-second Auto-refresh for real-time dashboard & day/year rollover sync
        setInterval(() => {
            const curDayIso = Utils.getTodayIsoString();
            if (curDayIso !== this.lastCheckedDayIso) {
                this.lastCheckedDayIso = curDayIso;
                this.loadInitialMetadata();
            }
            RealtimeModule.load();
            AnomalyModule.search();
        }, 30000);
    },

    initAccordions() {
        const moduleMap = {
            'accordionStatsItem': () => StatsModule.search(),
            'accordionRequestsItem': () => RequestsModule.search(),
            'accordionSimulationItem': () => SimulationModule.search(),
            'accordionPivotItem': () => PivotModule.search()
        };

        const triggers = document.querySelectorAll('.accordion-trigger');
        triggers.forEach(trigger => {
            trigger.addEventListener('click', () => {
                const item = trigger.closest('.accordion-item');
                if (!item) return;
                const wasActive = item.classList.contains('active');
                if (wasActive) {
                    item.classList.remove('active');
                } else {
                    item.classList.add('active');
                    this.triggerResize();

                    // 아코디언이 열릴 때 해당 섹션의 최신 집계 결과를 비동기 자동 갱신
                    const updateFn = moduleMap[item.id];
                    if (updateFn) {
                        updateFn().then(() => {
                            this.triggerResize();
                        }).catch(err => {
                            console.error(`Error updating aggregate data for ${item.id}:`, err);
                        });
                    }
                }
            });
        });
    },

    triggerResize() {
        const resizeCharts = () => {
            try {
                if (RealtimeModule?.sparklineChart) RealtimeModule.sparklineChart.resize();
                if (StatsModule?.mainChart) StatsModule.mainChart.resize();
                if (StatsModule?.deptChart) StatsModule.deptChart.resize();
                if (RequestsModule?.deptChart) RequestsModule.deptChart.resize();
                if (RequestsModule?.chart) {
                    RequestsModule.chart.resize();
                    RequestsModule.chart.update('none');
                } else if (RequestsModule?.rawData) {
                    RequestsModule.renderChart();
                }
                if (SimulationModule?.chart) {
                    SimulationModule.chart.resize();
                    SimulationModule.chart.update('none');
                } else if (SimulationModule?.rawData) {
                    SimulationModule.renderChart();
                }
            } catch (e) {
                console.error('Error resizing charts on accordion toggle:', e);
            }
        };
        requestAnimationFrame(resizeCharts);
        setTimeout(resizeCharts, 60);
        setTimeout(resizeCharts, 200);
    },

    async loadInitialMetadata() {
        try {
            const [resDates, resDepts] = await Promise.all([
                fetch('/api/v1/license/available-dates'),
                fetch('/api/v1/license/departments')
            ]);
            const datesData = await resDates.json();
            const deptsData = await resDepts.json();

            this.availableDates = datesData.dates || [];

            const deptSelect = document.getElementById('filterDept');
            if (deptSelect && deptsData.departments) {
                const existing = deptSelect.value;
                deptSelect.innerHTML = '<option value="ALL">전체 부서</option>' +
                    deptsData.departments.map(d => `<option value="${Utils.escapeHtml(d)}">${Utils.escapeHtml(d)}</option>`).join('');
                if (existing) deptSelect.value = existing;
            }

            if (deptsData.departments) {
                AnomalyModule.populateDeptOptions(deptsData.departments);
            }

            // 가용 일자 로드 후 각 모듈의 최대 일자 및 달력 필터 동기화
            const todayIso = Utils.getTodayIsoString();
            const maxPastDate = this.getMaxAllowedPastDate();
            PivotModule?.dateController?.setMaxDate(todayIso);
            StatsModule?.dateController?.setMaxDate(todayIso);
            RequestsModule?.dateController?.setMaxDate(maxPastDate);
            SimulationModule?.dateController?.setMaxDate(maxPastDate);
            PivotModule?.dateController?.refreshFlatpickr();
            StatsModule?.dateController?.refreshFlatpickr();
            RequestsModule?.dateController?.refreshFlatpickr();
            SimulationModule?.dateController?.refreshFlatpickr();
        } catch (err) {
            console.error('Initial metadata load error:', err);
        }
    }
};

// =========================================================================
// 10. Theme Controller (Dark Mode Default & Dynamic Chart Sync)
// =========================================================================
const ThemeController = {
    currentTheme: 'dark',

    init() {
        const savedTheme = localStorage.getItem('cop_theme') || 'dark';
        this.setTheme(savedTheme);

        const btnToggle = document.getElementById('btnThemeToggle');
        if (btnToggle) {
            btnToggle.addEventListener('click', () => {
                const nextTheme = this.currentTheme === 'dark' ? 'light' : 'dark';
                this.setTheme(nextTheme);
            });
        }
    },

    setTheme(theme) {
        this.currentTheme = theme;
        document.documentElement.setAttribute('data-theme', theme);
        document.documentElement.style.colorScheme = theme;
        localStorage.setItem('cop_theme', theme);

        const icon = document.getElementById('themeIcon');
        const btnToggle = document.getElementById('btnThemeToggle');
        if (icon) {
            if (theme === 'light') {
                icon.className = 'fa-solid fa-sun';
                if (btnToggle) btnToggle.title = '다크 모드로 전환';
            } else {
                icon.className = 'fa-solid fa-moon';
                if (btnToggle) btnToggle.title = '라이트 모드로 전환';
            }
        }

        this.updateChartsTheme();
        PivotModule?.dateController?.refreshFlatpickr();
        StatsModule?.dateController?.refreshFlatpickr();
        RequestsModule?.dateController?.refreshFlatpickr();
        SimulationModule?.dateController?.refreshFlatpickr();
    },

    updateChartsTheme() {
        const isLight = this.currentTheme === 'light';
        const textColor = isLight ? '#475569' : '#94a3b8';
        const gridColor = isLight ? 'rgba(0, 0, 0, 0.06)' : 'rgba(255, 255, 255, 0.05)';

        if (window.Chart) {
            Chart.defaults.color = textColor;
            Chart.defaults.borderColor = gridColor;
        }

        // Realtime sparkline chart sync
        if (RealtimeModule && RealtimeModule.sparklineChart && RealtimeModule.sparklineChart.options) {
            try {
                if (RealtimeModule.sparklineChart.options.scales?.x) {
                    RealtimeModule.sparklineChart.options.scales.x.ticks.color = textColor;
                    RealtimeModule.sparklineChart.options.scales.x.grid.color = gridColor;
                }
                if (RealtimeModule.sparklineChart.options.scales?.y) {
                    RealtimeModule.sparklineChart.options.scales.y.ticks.color = textColor;
                    RealtimeModule.sparklineChart.options.scales.y.grid.color = gridColor;
                }
                RealtimeModule.sparklineChart.update();
            } catch (e) {}
        }

        // Stats module chart sync
        if (StatsModule && StatsModule.mainChart && StatsModule.mainChart.options) {
            try {
                if (StatsModule.mainChart.options.scales?.x) {
                    StatsModule.mainChart.options.scales.x.ticks.color = textColor;
                    StatsModule.mainChart.options.scales.x.grid.color = gridColor;
                }
                if (StatsModule.mainChart.options.scales?.y) {
                    StatsModule.mainChart.options.scales.y.ticks.color = textColor;
                    StatsModule.mainChart.options.scales.y.grid.color = gridColor;
                }
                StatsModule.mainChart.update();
            } catch (e) {}
        }

        // Stats module dept pie chart sync
        if (StatsModule && typeof StatsModule.renderDeptChart === 'function' && StatsModule.rawData) {
            try {
                StatsModule.renderDeptChart();
            } catch (e) {}
        }

        // Requests module denial chart sync
        if (RequestsModule && RequestsModule.chart && RequestsModule.chart.options) {
            try {
                if (RequestsModule.chart.options.scales?.x) {
                    RequestsModule.chart.options.scales.x.ticks.color = textColor;
                    RequestsModule.chart.options.scales.x.grid.color = gridColor;
                }
                if (RequestsModule.chart.options.scales?.y) {
                    RequestsModule.chart.options.scales.y.ticks.color = textColor;
                    RequestsModule.chart.options.scales.y.grid.color = gridColor;
                }
                RequestsModule.chart.update();
            } catch (e) {}
        }

        // Simulation module chart sync
        if (SimulationModule && SimulationModule.chart && SimulationModule.chart.options) {
            try {
                if (SimulationModule.chart.options.scales?.x) {
                    SimulationModule.chart.options.scales.x.ticks.color = textColor;
                    SimulationModule.chart.options.scales.x.grid.color = gridColor;
                }
                if (SimulationModule.chart.options.scales?.y) {
                    SimulationModule.chart.options.scales.y.ticks.color = textColor;
                    SimulationModule.chart.options.scales.y.grid.color = gridColor;
                }
                SimulationModule.chart.update();
            } catch (e) {}
        }
    }
};

// Start application
document.addEventListener('DOMContentLoaded', () => {
    App.init();
});
