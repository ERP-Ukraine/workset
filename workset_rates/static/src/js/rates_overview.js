/** «Огляд ставок» (рівень B, розділ 6б): client action workset_rates.overview — перший пункт меню «Ставки».
 *  Дані — ОДИН виклик workset.rate.rates_overview(домен фільтра, місяців, only_working): плитки, картки ліній, ряди графіків, тривоги,
 *  списки ліній і замовників для фільтра, підписи рядів мовою користувача. Фільтр (лінії, замовник, період, «тільки працюють») — у
 *  localStorage браузера окремо для кожного користувача. Графіки — Chart.js, який уже вантажить web (бандл web.chartjs_lib), кольори —
 *  штатна палітра Odoo й змінні Bootstrap теми (світла й темна). Клік по числу смуги, «пігулці», картці лінії, стовпцю розподілу, сектору
 *  «Хто знайшов» — штатний список за описом, який сервер прислав поруч із числом (open: дія, заголовок, домен, фільтри пошуку; openList
 *  лише виконує його) — число на елементі = число рядків списку; клік по тривозі — список або картка.
 *  Круг 06.11 (слово власника): картки ліній — один ряд від більшого числа до меншого, смуга зліва картки — колір лінії з сервера
 *  (line.color — той самий, що ряд лінії на графіку «По лініях»); усі числа — з роздільником тисяч (groupNumber).
 *  Круг вигляду 06.11 (слово власника «лише важливе, без порожнього й обрізаного»): нульові «пігулки» сховані; картка лінії коротка, решта —
 *  у підказці (lineTip), клік — фільтр сторінки за лінією; «Тривоги» трьома групами (alertGroups); графік місяців — три лінії зі значенням на
 *  останній точці, «Розподіл» — число над стовпцем; кольори графіків — з обчисленого стилю сторінки (Odoo 19 не задає CSS-змінні Bootstrap).
 *  Блок Е (на показ): стрічка «Сьогодні вирішити» над усім — чипи «Потрібна дія» із сервера (data.decide), клік — openChip. */
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { browser } from "@web/core/browser/browser";
import { cookie } from "@web/core/browser/cookie";
import { getColor, hexToRGBA } from "@web/core/colors/colors";
import { localization } from "@web/core/l10n/localization";
import { user } from "@web/core/user";
import { Layout } from "@web/search/layout";
import { groupNumber, withAlpha } from "@workset_rates/js/rates_format";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { CheckboxItem } from "@web/core/dropdown/checkbox_item";
import { Component, onWillStart, useEffect, useRef, useState } from "@odoo/owl";

const RATE = "workset.rate";
const MONTHS = [6, 12, 24];
const STORE = "workset_rates.overview.";
const ALL = "workset_rates.action_rates_all";
// «Працюють без ставки» — останні табелі пар без діючої ставки (штатний список табелів)
const ALERT_ACTIONS = { "workset.rate": ALL, "workset.rate.raise": "workset_rates.action_raises", "account.analytic.line": "hr_timesheet.timesheet_action_all" };

export function defaultFilter() {
    return { lines: [], customer: 0, months: 12, working: true };
}

/** Збережений фільтр → чистий: невідоме й зіпсоване — за замовчуванням. */
export function cleanFilter(saved) {
    const out = defaultFilter();
    if (!saved || typeof saved !== "object") {
        return out;
    }
    if (Array.isArray(saved.lines)) {
        out.lines = saved.lines.filter((id) => Number.isInteger(id) && id > 0);
    }
    if (Number.isInteger(saved.customer) && saved.customer > 0) {
        out.customer = saved.customer;
    }
    if (MONTHS.includes(saved.months)) {
        out.months = saved.months;
    }
    if (typeof saved.working === "boolean") {
        out.working = saved.working;
    }
    return out;
}

export class RatesOverview extends Component {
    static template = "workset_rates.RatesOverview";
    static components = { Layout, Dropdown, DropdownItem, CheckboxItem };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.months = MONTHS;
        this.storeKey = STORE + user.userId;
        this.filter = useState(this.restore());
        // open — розгорнуті групи «Тривог» (немає ключа — за замовчуванням: «Неповні дані» згорнуто, решта розгорнуто)
        this.state = useState({ data: null, byLine: false, open: {} });
        this.serial = 0;
        this.charts = [];
        this.historyRef = useRef("history");
        this.bucketsRef = useRef("buckets");
        this.foundRef = useRef("found");
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        useEffect(
            () => {
                this.renderCharts();
                return () => this.destroyCharts();
            },
            () => [this.state.data, this.state.byLine]
        );
    }

    // ——— фільтр ———
    restore() {
        try {
            return cleanFilter(JSON.parse(browser.localStorage.getItem(this.storeKey) || "null"));
        } catch {
            return defaultFilter();
        }
    }

    save() {
        // приватний режим / заповнене сховище — фільтр просто не запам'ятається, сторінка працює далі
        try {
            browser.localStorage.setItem(this.storeKey, JSON.stringify({ ...this.filter, lines: [...this.filter.lines] }));
        } catch {
            // нічого
        }
    }

    domain() {
        const d = [];
        if (this.filter.lines.length) {
            d.push(["line_id", "in", [...this.filter.lines]]);
        }
        if (this.filter.customer) {
            d.push(["customer_id", "=", this.filter.customer]);
        }
        return d;
    }

    async load() {
        const serial = ++this.serial;
        const data = await this.orm.call(RATE, "rates_overview", [this.domain(), this.filter.months], { only_working: this.filter.working });
        if (serial !== this.serial) {
            return;
        }
        // збережені лінії, яких уже немає (архів), — з фільтра геть і ще раз: інакше сторінка порожня з «Лінії: 1»
        // так само замовник без діючих ставок (його немає у списку вибору)
        const known = new Set(data.options.lines.map((l) => l.id));
        const keep = this.filter.lines.filter((id) => known.has(id));
        const customerGone = this.filter.customer && !data.options.customers.some((c) => c.id === this.filter.customer);
        if (keep.length !== this.filter.lines.length || customerGone) {
            this.filter.lines.splice(0, this.filter.lines.length, ...keep);
            if (customerGone) {
                this.filter.customer = 0;
            }
            this.save();
            return this.load();
        }
        this.state.data = data;
    }

    async changed() {
        this.save();
        await this.load();
    }

    toggleLine(id) {
        const i = this.filter.lines.indexOf(id);
        if (i >= 0) {
            this.filter.lines.splice(i, 1);
        } else {
            this.filter.lines.push(id);
        }
        this.changed();
    }

    allLines() {
        this.filter.lines.splice(0);
        this.changed();
    }

    onCustomer(ev) {
        this.filter.customer = parseInt(ev.target.value, 10) || 0;
        this.changed();
    }

    setMonths(m) {
        this.filter.months = m;
        this.changed();
    }

    toggleWorking() {
        this.filter.working = !this.filter.working;
        this.changed();
    }

    setByLine(value) {
        this.state.byLine = value;
    }

    // ——— показ ———
    get data() {
        return this.state.data;
    }

    get options() {
        return (this.state.data && this.state.data.options) || { lines: [], customers: [] };
    }

    get hasAlerts() {
        const d = this.state.data;
        return Boolean(d && (d.check || this.alertGroups.length));
    }

    /** Є хоч одна «пігулка» з числом > 0 (нульові сховані, ряду без них немає). */
    get hasPills() {
        const t = (this.state.data && this.state.data.tiles) || {};
        return ["contract_diff", "below_min", "to_check", "no_line", "raises"].some((k) => t[k]);
    }

    /** «Тривоги» трьома групами: «Потрібна дія» — живі тривоги з числом > 0 (з рядками) і розділи звірки про гроші та людей без ставки;
     *  «Договір ≠ база — подробиці»; «Неповні дані». Розділи й підписи груп — із сервера (check.groups), порожніх груп немає. */
    get alertGroups() {
        const d = this.state.data;
        if (!d) {
            return [];
        }
        const byKey = Object.fromEntries(((d.check && d.check.groups) || []).map((g) => [g.key, g]));
        const live = d.alerts.filter((a) => a.count);
        const action = byKey.action || { key: "action", title: d.labels.g_action, items: [] };
        const out = live.length || action.items.length ? [{ ...action, live }] : [];
        for (const key of ["contract", "data"]) {
            if (byKey[key]) {
                out.push({ ...byKey[key], live: [] });
            }
        }
        return out;
    }

    isOpen(group) {
        const open = this.state.open;
        return group.key in open ? open[group.key] : group.key !== "data";
    }

    toggleGroup(group) {
        this.state.open[group.key] = !this.isOpen(group);
    }

    /** Середні смуги пораховано по всіх діючих ставках (не лише по тих, хто працює) — коли «Тільки працюють» вимкнено. */
    allRates(key) {
        return !this.filter.working && ["avg", "below_min", "raises"].includes(key);
    }

    money(value) {
        return groupNumber(value, { decimalPoint: localization.decimalPoint });
    }

    /** Кількість (людей, ставок, тривог) — ціле з роздільником тисяч. */
    int(value) {
        return groupNumber(value, { digits: 0 });
    }

    /** Колір лінії (номер штатної палітри з сервера, line.color) — смуга картки; той самий номер — ряд лінії на графіку «По лініях». */
    lineColor(line) {
        return getColor(line.color || 0, cookie.get("color_scheme"), "odoo");
    }

    monthLabel(day) {
        return luxon.DateTime.fromISO(day).toFormat("LLL yy");
    }

    /** Сума без зайвих нулів: 3 → «3», 1,5 → «1,5». */
    plain(value) {
        return groupNumber(value, { trailingZeros: false, decimalPoint: localization.decimalPoint });
    }

    /** Посередники картки лінії одним рядком: «Ім'я −сума €»; суми різні — сума більшості ставок і замовники з іншою сумою
     *  («Ім'я −А € · Замовник −Б €»), а замовників сервер не знає — діапазон «Ім'я −Б…А €». Порожньо, якщо посередників немає. */
    agentText(line) {
        return (line.agents || [])
            .map((a) => {
                let sum;
                if (a.min === a.max) {
                    sum = `−${this.plain(a.max)} €`;
                } else if (a.except && a.except.length) {
                    sum = [`−${this.plain(a.main)} €`, ...a.except.map((e) => `${e.customer} −${this.plain(e.amount)} €`)].join(" · ");
                } else {
                    sum = `−${this.plain(a.min)}…${this.plain(a.max)} €`;
                }
                return a.name ? `${a.name} ${sum}` : sum;
            })
            .join(", ");
    }

    /** Фонд працівників картки лінії: «Фонд −А €»; суми різні (повна / неповна ставка) — «Фонд −Б…А €». */
    fundText(line) {
        const f = line.fund;
        if (!f) {
            return "";
        }
        const sum = f.min === f.max ? `−${this.plain(f.max)} €` : `−${this.plain(f.min)}…${this.plain(f.max)} €`;
        return `${this.data.labels.fund} ${sum}`;
    }

    /** Підказка картки лінії: усе, чого на короткій картці немає — якір, платять нам, після бонусів, посередник, фонд, нижче мінімуму, що
     *  робить клік. Рядки — через перенос. */
    lineTip(line) {
        const L = this.data.labels;
        const anchor = L["anchor_" + line.anchor] || "";
        let agent = "";
        if (line.agent === "yes") {
            agent = this.agentText(line);
        } else if (line.agent === "expected") {
            agent = L.agent_wait;
        }
        return [
            anchor ? `${line.name} · ${anchor}` : line.name,
            this.fill(L.tip_price, this.money(line.avg_price)),
            this.fill(L.tip_net, this.money(line.avg_net)),
            agent,
            this.fundText(line),
            line.below_min ? this.fill(L.tip_below, this.int(line.below_min)) : "",
            L.tip_line,
        ]
            .filter(Boolean)
            .join("\n");
    }

    /** Картка лінії вибрана фільтром сторінки (лише вона). */
    isLineOn(line) {
        return this.filter.lines.length === 1 && this.filter.lines[0] === line.id;
    }

    /** Клік по картці лінії — фільтр сторінки за цією лінією; ще раз — усі лінії. */
    filterLine(line) {
        if (this.isLineOn(line)) {
            return this.allLines();
        }
        this.filter.lines.splice(0, this.filter.lines.length, line.id);
        this.changed();
    }

    onLineKey(ev, line) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.filterLine(line);
        }
    }

    /** Підпис із сервера з підстановкою %s по черзі: підпис("Частка %s", "5–6") */
    fill(text, ...args) {
        let i = 0;
        return String(text).replace(/%s/g, () => String(args[i++] ?? ""));
    }

    // ——— переходи ———
    /** Єдиний вхід у список ставок: опис від сервера (rates_overview → open) — дія, заголовок вікна, домен (фільтр сторінки вже в ньому),
     *  фільтри пошуку й панель зліва. Тут нічого не додається: число на елементі пораховано по тих самих ставках. */
    async openList(spec) {
        if (!spec) {
            return;
        }
        const act = await this.action.loadAction(spec.action);
        this.action.doAction({ ...act, name: spec.title, domain: spec.domain, context: spec.context });
    }

    openTile(key) {
        return this.openList(this.data.open.tiles[key]);
    }

    /** Стовпець «Розподілу частки»: ставки з часткою стовпця. */
    openBucket(index) {
        const b = this.data.buckets[index];
        return this.openList(b && b.open);
    }

    /** Сектор «Хто знайшов»: ми / лінія / не вказано. */
    openFound(kind) {
        return this.openList(this.data.open.found[kind]);
    }

    /** Картка лінії → ставки, з яких пораховано картку (лінія вибрана в панелі зліва). */
    openLine(line) {
        return this.openList(line.open);
    }

    /** Заголовок тривоги — список; link === false (сервер: немає права читати ці записи, напр. табелі) — не посилання. */
    async openAlert(alert) {
        if (alert.link === false) {
            return;
        }
        const action = await this.action.loadAction(ALERT_ACTIONS[alert.model] || ALL);
        this.action.doAction({ ...action, domain: [["id", "in", alert.ids]], context: {} });
    }

    /** Чип «Сьогодні вирішити» — той самий перехід, що рядок «Тривог» (опис — із сервера, calc.decide_chips): розділ звірки — запис
     *  звірки (openCheck), жива тривога — її список (openAlert, і link === false так само не посилання). */
    openChip(chip) {
        const open = chip.open || {};
        return open.check ? this.openCheck() : this.openAlert(open);
    }

    /** Рядок тривоги: запис; у «Працюють без ставки» — нова ставка цієї пари (форма з працівником і замовником); click === false — без кліку. */
    openRow(alert, row) {
        if (row.click === false) {
            return;
        }
        return row.new ? this.openRecord("workset.rate", false, row.new) : this.openRecord(alert.model, row.id);
    }

    openRecord(model, id, context = {}) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: model, res_id: id, views: [[false, "form"]], target: "current", context });
    }

    openCheck() {
        this.openRecord("workset.rate.check", this.state.data.check.id);
    }

    // ——— графіки ———
    destroyCharts() {
        for (const chart of this.charts) {
            chart.destroy();
        }
        this.charts = [];
    }

    renderCharts() {
        this.destroyCharts();
        const d = this.state.data;
        if (!d || !window.Chart || !this.historyRef.el) {
            return;
        }
        const scheme = cookie.get("color_scheme");
        const color = (i) => getColor(i, scheme, "odoo");
        // кольори — з обчисленого стилю картки графіка (світла й темна тема): Odoo 19 CSS-змінних Bootstrap не задає (копія 20, 06.11);
        // сітка й нейтральний сектор — напівпрозорі від кольору тексту
        const css = getComputedStyle(this.historyRef.el.parentElement || this.historyRef.el);
        const text = css.color || undefined;
        const grid = withAlpha(text, 0.15);
        const axis = { ticks: { color: text }, grid: { color: grid } };
        // підписи осей і підказок — числа за мовою користувача (uk / ru: пробіл тисяч, кома)
        const base = {
            locale: (user.lang || "").replace("_", "-") || undefined,
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: { legend: { position: "bottom", labels: { color: text, boxWidth: 12 } } },
        };
        const font = (px) => `600 ${px}px ${css.fontFamily}`;
        const line = (i) => ({ borderColor: color(i), backgroundColor: hexToRGBA(color(i), 0.2), tension: 0.3, pointRadius: 2 });
        const labels = d.series.days.map((day) => this.monthLabel(day));
        const L = d.labels;
        let datasets;
        if (this.state.byLine) {
            // колір ряду — колір лінії з сервера (l.color), як смуга її картки
            datasets = d.series.by_line.map((l) => ({ label: l.name, data: l.share, ...line(l.color || 0) }));
        } else {
            // три лінії різних кольорів (круг вигляду 06.11): «Ставок діяло» (друга вісь) і «після бонусів» — не на графіку
            const total = d.series.total;
            datasets = [
                { label: L.wage, data: total.map((p) => p.avg_wage), ...line(0) },
                { label: L.price, data: total.map((p) => p.avg_price), ...line(1) },
                { label: L.share, data: total.map((p) => p.avg_share), ...line(2) },
            ];
        }
        // значення на останній точці кожної лінії (лише «Загалом»: по лініях підписи налізли б одне на одне)
        const lastValue = {
            id: "wsLastValue",
            afterDatasetsDraw: (chart) => {
                if (this.state.byLine) {
                    return;
                }
                const ctx = chart.ctx;
                ctx.save();
                ctx.font = font(12);
                ctx.textAlign = "left";
                ctx.textBaseline = "middle";
                chart.data.datasets.forEach((ds, i) => {
                    const points = chart.getDatasetMeta(i).data;
                    const value = ds.data[ds.data.length - 1];
                    if (!points.length || value === null || value === undefined || !chart.isDatasetVisible(i)) {
                        return;
                    }
                    const pt = points[points.length - 1];
                    ctx.fillStyle = ds.borderColor;
                    ctx.fillText(this.money(value), pt.x + 6, pt.y);
                });
                ctx.restore();
            },
        };
        this.charts.push(
            new Chart(this.historyRef.el, {
                type: "line",
                data: { labels, datasets },
                options: {
                    ...base,
                    layout: { padding: { right: this.state.byLine ? 0 : 44 } },
                    scales: {
                        // підписи місяців крупніше
                        x: { ...axis, ticks: { color: text, font: { size: 13 } } },
                        y: { ...axis, beginAtZero: true },
                    },
                },
                plugins: [lastValue],
            })
        );
        // клік по елементу графіка відкриває список, курсор-вказівник — лише над елементом
        const clickable = (open) => ({
            onClick: (_ev, elements) => {
                if (elements.length) {
                    open(elements[0].index);
                }
            },
            onHover: (ev, elements) => {
                const el = ev.native && ev.native.target;
                if (el) {
                    el.style.cursor = elements.length ? "pointer" : "default";
                }
            },
        });
        // «Розподіл частки»: число над стовпцем (порожні — без числа); крок стовпця — сервер (BUCKET_STEP)
        const barValue = {
            id: "wsBarValue",
            afterDatasetsDraw: (chart) => {
                const ctx = chart.ctx;
                const values = chart.data.datasets[0].data;
                ctx.save();
                ctx.font = font(12);
                ctx.textAlign = "center";
                ctx.textBaseline = "bottom";
                ctx.fillStyle = text || color(0);
                chart.getDatasetMeta(0).data.forEach((bar, i) => {
                    if (values[i]) {
                        ctx.fillText(this.int(values[i]), bar.x, bar.y - 3);
                    }
                });
                ctx.restore();
            },
        };
        this.charts.push(
            new Chart(this.bucketsRef.el, {
                type: "bar",
                data: {
                    labels: d.buckets.map((b) => b.label),
                    datasets: [{ label: L.people, data: d.buckets.map((b) => b.count), backgroundColor: color(0), borderRadius: 3 }],
                },
                options: {
                    ...base,
                    ...clickable((i) => this.openBucket(i)),
                    layout: { padding: { top: 18 } },
                    plugins: { legend: { display: false } },
                    scales: { x: axis, y: { ...axis, beginAtZero: true, ticks: { color: text, precision: 0 } } },
                },
                plugins: [barValue],
            })
        );
        // «Хто знайшов»: «Не вказано» — нейтральний сірий (змінна теми), щоб не збігатися з «Знайшли ми»; поле порожнє в усіх — підпис у центрі,
        // вписаний у дірку кільця (measureText: найширший рядок і висота блоку — не далі за 0,9 внутрішнього радіуса від центру)
        const kinds = ["us", "line", "none"];
        const total = d.found.us + d.found.line + d.found.none;
        const empty = total > 0 && d.found.us + d.found.line === 0;
        const neutral = withAlpha(text, 0.35) || color(5);
        const centerText = {
            id: "wsFoundEmpty",
            afterDraw: (chart) => {
                if (!empty) {
                    return;
                }
                const a = chart.chartArea;
                const arc = chart.getDatasetMeta(0).data[0];
                const cx = arc ? arc.x : (a.left + a.right) / 2;
                const cy = arc ? arc.y : (a.top + a.bottom) / 2;
                const inner = arc && arc.innerRadius ? arc.innerRadius : Math.min(a.width, a.height) * 0.3;
                const ctx = chart.ctx;
                const rows = this.fill(L.found_empty, this.int(total), this.int(total)).split("\n");
                let size = inner / 3;
                ctx.save();
                ctx.font = font(size);
                const wide = Math.max(...rows.map((row) => ctx.measureText(row).width));
                const tall = rows.length * size * 1.3;
                size = Math.max(9, size * Math.min(1, (inner * 0.9) / Math.hypot(wide / 2, tall / 2)));
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                ctx.fillStyle = text || neutral;
                ctx.font = font(size);
                rows.forEach((row, i) => ctx.fillText(row, cx, cy + (i - (rows.length - 1) / 2) * size * 1.3));
                ctx.restore();
            },
        };
        this.charts.push(
            new Chart(this.foundRef.el, {
                type: "doughnut",
                data: {
                    labels: [L.found_us, L.found_line, L.found_none],
                    datasets: [{ data: [d.found.us, d.found.line, d.found.none], backgroundColor: [color(0), color(1), neutral], borderWidth: 0 }],
                },
                options: { ...base, ...clickable((i) => this.openFound(kinds[i])), cutout: "60%" },
                plugins: [centerText],
            })
        );
    }
}

registry.category("actions").add("workset_rates.overview", RatesOverview);
