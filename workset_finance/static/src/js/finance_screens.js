/** «Фінанси» (круг 2): екран «Власник» — макет A (п'ять плиток із «з початку року», чипи, водоспад «Як складається прибуток», графік
 *  року, «Власники», «Перекази на особисті рахунки», «Окремо від прибутку») і під ним таблиця «Драбина» макета B; client action
 *  workset_finance.owner. ОДИН виклик сервера на workset.finance.month (finance_owner): числа, текст таблиці «тис. €», геометрію водоспаду,
 *  ряди графіка й описи списків для кліку рахує сервер (models/calc.py, models/workset_finance_screens.py), тут — лише показ і переходи
 *  (образець — екрани «Прибутковості», М3). Графік — Chart.js із бандла web (web.chartjs_lib); кольори графіка — явні (PALETTE: CSS-змінних
 *  Bootstrap Odoo 19 не задає), бренд — тон класу стовпця «чистий прибуток» (scss: $primary); світла чи темна тема — за яскравістю фону.
 *  Кожне число відкриває свої записи (опис 'open' — у тій самій відповіді); клік по місяцю графіка — вибір місяця; чип «Якість даних» —
 *  вікно «три кошики» з тієї самої відповіді. Місяць переживає перехід до списку й назад (стан дії: useSetupAction → props.state). */
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { formatFloat } from "@web/views/fields/formatters";
import { Dialog } from "@web/core/dialog/dialog";
import { useSetupAction } from "@web/search/action_hook";
import { Layout } from "@web/search/layout";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { Component, onWillStart, useEffect, useRef, useState } from "@odoo/owl";

const MONTH = "workset.finance.month";

/** Сума в € без копійок: 69349.6 → «69 350», −438 → «−438» (мінус — типографський). */
export function euros(value) {
    return formatFloat(Math.round(value || 0), { digits: [16, 0] }).replace(/^-/, "−");
}

/** Відсоток з одним знаком без зайвого нуля: 16.6 → «16,6», 1 → «1». */
export function plain(value) {
    return formatFloat(value || 0, { digits: [16, 1], trailingZeros: false }).replace(/^-/, "−");
}

/** Кольори графіка (як у М3): ряди — нейтральний сірий, що читається на обох темах; бренд — запасний тон стовпця «чистий прибуток»
 *  ($primary світлої теми; справжній береться з класу — brandOf); мінус — червоний Bootstrap. Текст і сітка — під фон теми. */
export const PALETTE = {
    light: { text: "#4a5058", grid: "#e2e4e8", zero: "#8a8f98", line: "#9aa0ab", brand: "#6b3e66", danger: "#dc3545" },
    dark: { text: "#c3c7ce", grid: "#3a3f48", zero: "#8a8f98", line: "#9aa0ab", brand: "#6b3e66", danger: "#dc3545" },
};

/** «rgb(1, 2, 3)» / «rgba(…)» → [r, g, b]; прозоре чи не колір — null. */
export function rgbOf(css) {
    const m = String(css || "").match(/rgba?\(([^)]+)\)/);
    if (!m) {
        return null;
    }
    const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    if (p.length < 3 || p.slice(0, 3).some((x) => Number.isNaN(x)) || (p.length > 3 && p[3] === 0)) {
        return null;
    }
    return p.slice(0, 3);
}

/** «#rrggbb» палітри → [r, g, b]; не колір — null. */
export function hexRgb(hex) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex || ""));
    return m ? [1, 2, 3].map((i) => parseInt(m[i], 16)) : null;
}

/** Темний фон: відносна яскравість < 0,5. */
export function isDark(rgb) {
    return Boolean(rgb) && (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255 < 0.5;
}

/** [r, g, b] і прозорість → «rgba(r, g, b, a)». */
export function rgba(rgb, alpha) {
    return `rgba(${rgb.join(", ")}, ${alpha})`;
}

/** Фон під елементом: перший непрозорий від el угору (немає — білий). */
function backdrop(el) {
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
        const c = rgbOf(getComputedStyle(n).backgroundColor);
        if (c) {
            return c;
        }
    }
    return [255, 255, 255];
}

/** Тон бренду — фон класу стовпця «чистий прибуток» водоспаду (scss: $primary свого бандла): графік того самого кольору; клас не дав
 *  кольору — запасний з палітри. → [r, g, b]. */
function brandOf(root, fallback) {
    if (!root) {
        return fallback;
    }
    const probe = document.createElement("span");
    probe.className = "o_workset_finance_fall_net d-none";
    root.appendChild(probe);
    const rgb = rgbOf(getComputedStyle(probe).backgroundColor);
    probe.remove();
    return rgb || fallback;
}

/** Вікно «Якість даних: три кошики» (вміст — chip.baskets тієї самої відповіді, нового виклику немає): міжфірмові, без місця, банк без
 *  документа за видами; підписи — за кодом мовою користувача (сервер). Клік по рядку — закрити вікно й відкрити записи кошика. */
export class QualityDialog extends Component {
    static template = "workset_finance.QualityDialog";
    static components = { Dialog };
    static props = ["*"];

    setup() {
        this.euros = euros;
        this.plain = plain;
    }

    get chip() {
        return this.props.chip;
    }

    /** Заголовок із сервера — через t-props (атрибут title шаблону збирач перекладу взяв би за підпис). */
    get dialogProps() {
        return { title: this.chip.title, size: "md" };
    }

    open(spec) {
        if (spec) {
            this.props.close();
            this.props.screen.openList(spec);
        }
    }

    keyOpen(ev, spec) {
        if (spec && (ev.key === "Enter" || ev.key === " ")) {
            ev.preventDefault();
            this.open(spec);
        }
    }
}

export class FinanceOwner extends Component {
    static template = "workset_finance.Owner";
    static components = { Layout, Dropdown, DropdownItem };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        const params = (this.props.action && this.props.action.params) || {};
        // місяць — з повернення назад (стан дії), з переходу (params.month), інакше — за замовчуванням сервера
        this.month = (this.props.state && this.props.state.month) || params.month || false;
        this.state = useState({ data: null, failed: false });
        this.serial = 0;
        this.charts = [];
        this.euros = euros;
        this.plain = plain;
        this.chartRef = useRef("chart");
        useSetupAction({ getLocalState: () => ({ month: this.month }) });
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        useEffect(
            () => {
                this.renderChart();
                return () => this.destroyCharts();
            },
            () => [this.state.data]
        );
    }

    get data() {
        return this.state.data;
    }

    /** Виклик сервера; відповідь, що запізнилась (уже вибрали інший місяць), відкидається. Помилка при першому показі — як у Odoo (вікно
     *  помилки); при зміні місяця — повідомлення, на екрані лишаються попередні дані з позначкою (state.failed). */
    async load() {
        const serial = ++this.serial;
        let data;
        try {
            data = await this.orm.call(MONTH, "finance_owner", [this.month]);
        } catch (error) {
            if (!this.state.data) {
                throw error;
            }
            if (serial === this.serial) {
                this.month = this.state.data.month.value;
                this.state.failed = true;
                this.notification.add(_t("Не вдалося оновити — на екрані попередні дані"), { type: "danger" });
            }
            return;
        }
        if (serial !== this.serial) {
            return;
        }
        this.month = data.month.value;
        this.state.failed = false;
        this.state.data = data;
    }

    setMonth(value) {
        if (value && (!this.data || value !== this.data.month.value)) {
            this.month = value;
            this.load();
        }
    }

    /** Список за описом сервера: модель, заголовок, домен, контекст (без «Створити»); є res_id — форма одного запису; опис {dialog} — вікно
     *  «три кошики» якості даних з тієї самої відповіді. */
    openList(spec) {
        if (!spec) {
            return;
        }
        if (spec.dialog === "quality") {
            const chip = this.data && this.data.chips.find((c) => c.key === "quality");
            if (chip) {
                this.dialog.add(QualityDialog, { chip, screen: this });
            }
            return;
        }
        const form = Boolean(spec.res_id);
        this.action.doAction({
            type: "ir.actions.act_window",
            name: spec.title,
            res_model: spec.model,
            views: form
                ? [[false, "form"]]
                : [
                      [false, "list"],
                      [false, "form"],
                  ],
            ...(form ? { res_id: spec.res_id } : {}),
            domain: spec.domain,
            context: spec.context || {},
            target: "current",
        });
    }

    /** «Прибутковість ліній →» за тим самим місяцем (екран «Огляд» М3; посилання є, лише коли модуль установлено й є доступ — сервер). */
    openProfit() {
        const p = this.data.aside && this.data.aside.profit;
        if (p) {
            this.action.doAction({ type: "ir.actions.client", tag: p.tag, name: p.name, params: { month: p.month } });
        }
    }

    /** Вигляд «клікабельно» (рука, підкреслення при наведенні) — лише в елемента з описом списку; без опису — як текст. */
    clickCls(spec) {
        return spec ? "o_workset_finance_click" : "";
    }

    /** Підказка «Відкрити записи» (мовою користувача, з сервера) — лише в елемента, що їх відкриває; вікно — своя підказка. */
    hint(spec) {
        return spec ? spec.hint || this.data.labels.open : false;
    }

    /** Клавіатура: елемент із описом — кнопка для читача екрана, у порядку Tab; Enter або пробіл — як клік. */
    role(spec) {
        return spec ? "button" : false;
    }

    tab(spec) {
        return spec ? "0" : false;
    }

    keyOpen(ev, spec) {
        if (spec && (ev.key === "Enter" || ev.key === " ")) {
            ev.preventDefault();
            ev.stopPropagation();
            this.openList(spec);
        }
    }

    /** Плитка: чистий прибуток — кольором бренду (нижче нуля — червоним); решта — кольором тексту (мінус у «Україні» й частках — норма). */
    tileCls(tile) {
        if (tile.key !== "s4_net") {
            return "";
        }
        return tile.value < 0 ? "text-danger" : "text-primary";
    }

    /** Клітинка таблиці: обраний місяць підсвічено; є список — клік. Позначка «розр.» / «попр.» — видима мітка в шаблоні (круг 2б). */
    cellCls(cell, index) {
        return [index === this.data.ladder.selected ? "o_workset_finance_sel" : "", this.clickCls(cell.open)].filter(Boolean).join(" ");
    }

    /** Підказка клітинки й «Разом»: звідки число («розрахунок штабу», «поправка: <підстава>») і «Відкрити записи». */
    cellTitle(cell) {
        return [cell.hint, this.hint(cell.open)].filter(Boolean).join(" · ") || false;
    }

    /** Підказка чипа: свій текст сервера (якість даних — «не розібрано X € із Y €, поріг 1 %»), інакше — як у всього клікабельного. */
    chipTitle(chip) {
        return chip.hint || this.hint(chip.open);
    }

    /** Нижній ряд — стільки карток, скільки є що показати (порожньої не буває): клас сітки за їх числом, усі однієї ширини й висоти. */
    get bottomCount() {
        const d = this.data;
        return [d.owners, d.transfers, d.aside].filter(Boolean).length;
    }

    // ——— водоспад (геометрія — calc.fall_bars) ———
    /** Тонкий крок (< 2 % найбільшого підсумку) — не менше 4 px, щоб було видно. */
    barStyle(b) {
        return `bottom: ${b.bottom}%; height: ${Math.max(b.height, 0.6)}%;` + (b.thin ? " min-height: 4px;" : "");
    }

    /** Підпис суми в одному місці в усіх стовпців: плюс — над верхом, мінус — під низом. */
    valueStyle(b) {
        return b.amount < 0 ? `top: ${100 - b.bottom}%;` : `bottom: ${b.bottom + b.height}%;`;
    }

    /** Пунктир від підсумку після кроку до наступного стовпця. */
    linkStyle(b) {
        return `bottom: ${b.end}%;`;
    }

    /** Сума кроку: доданок угору після першого стовпця — зі знаком «+». */
    stepAmount(b) {
        return b.dir === "up" ? `+${euros(b.amount)}` : euros(b.amount);
    }

    /** Перший стовпець і «до часток» — бренд світліше, чистий прибуток — бренд, нижче нуля — червоне; доданки й частки — сірі. */
    barClass(b) {
        if (b.dir === "total") {
            if (b.amount < 0) {
                return "bg-danger";
            }
            return b.key === "s4_net" ? "o_workset_finance_fall_net" : "o_workset_finance_fall_total";
        }
        return b.dir === "down" ? "o_workset_finance_fall_down" : "o_workset_finance_fall_up";
    }

    // ——— графік «Чистий прибуток <рік>, тис. €» ———
    destroyCharts() {
        for (const chart of this.charts) {
            chart.destroy();
        }
        this.charts = [];
    }

    /** Клік і наведення: місяць — стовпчик осі під курсором (scales.x.getValueForPixel), лише в полі графіка й лише місяць із числом. */
    chartPick(onPick, has) {
        const at = (chart, ev) => {
            const a = chart.chartArea;
            if (!a || ev.x < a.left || ev.x > a.right || ev.y < a.top || ev.y > a.bottom) {
                return null;
            }
            const i = Math.round(chart.scales.x.getValueForPixel(ev.x));
            return Number.isInteger(i) && i >= 0 && i < chart.data.labels.length && has(i) ? i : null;
        };
        return {
            onClick: (ev, _els, chart) => {
                const i = at(chart, ev);
                if (i !== null) {
                    onPick(i);
                }
            },
            onHover: (ev, _els, chart) => {
                chart.canvas.style.cursor = at(chart, ev) === null ? "" : "pointer";
            },
        };
    }

    /** Стовпці — чистий прибуток місяця (тис. €): обраний місяць — бренд, решта — бренд світліше, мінус — червоний; число над стовпцем (під
     *  мінусом); тонка сіра лінія з точками — «до часток» з числами над точками (менше й сіріше за числа стовпців; близько до числа стовпця —
     *  піднімається над ним, без накладання — круг 2б, як у макеті A); обраний місяць — смуга під рядами й підпис осі кольором бренду. Клік —
     *  вибір місяця. */
    renderChart() {
        this.destroyCharts();
        const d = this.state.data;
        const el = this.chartRef.el;
        if (!d || !d.series || !window.Chart || !el) {
            return;
        }
        const s = d.series;
        const bg = backdrop(el);
        const pal = PALETTE[isDark(bg) ? "dark" : "light"];
        const brand = brandOf(el.closest(".o_workset_finance"), hexRgb(pal.brand));
        const c = { ...pal, brand: rgba(brand, 1), soft: rgba(brand, 0.55), band: rgba(brand, 0.12), font: getComputedStyle(el).fontFamily };
        const sel = s.selected;
        const values = {
            id: "wsFinanceValues",
            afterDatasetsDraw: (chart) => {
                const meta = chart.getDatasetMeta(0);
                const line = chart.getDatasetMeta(1);
                const ctx = chart.ctx;
                ctx.save();
                ctx.textAlign = "center";
                const tops = [];
                meta.data.forEach((bar, i) => {
                    const v = s.net[i];
                    tops[i] = null;
                    if (v !== null && v !== undefined && s.net_text[i] !== "—") {
                        ctx.font = `600 12px ${c.font}`;
                        ctx.fillStyle = v < 0 ? c.danger : c.text;
                        ctx.textBaseline = v < 0 ? "top" : "bottom";
                        ctx.fillText(s.net_text[i], bar.x, v < 0 ? bar.y + 6 : bar.y - 3);
                        // верх числа стовпця (над плюсом) — щоб число лінії не лягло на нього
                        tops[i] = v < 0 ? null : bar.y - 3 - 13;
                    }
                });
                // числа лінії «до часток»: дрібніше й сіріше, над точкою; ближче за 2 px до числа стовпця — вище за нього
                ctx.font = `400 11px ${c.font}`;
                ctx.fillStyle = c.line;
                ctx.textBaseline = "bottom";
                (line.data || []).forEach((pt, i) => {
                    const text = (s.base_text || [])[i];
                    if (!pt || !text || text === "—") {
                        return;
                    }
                    let y = pt.y - 5;
                    if (tops[i] !== null && y > tops[i] - 2) {
                        y = tops[i] - 2;
                    }
                    ctx.fillText(text, pt.x, y);
                });
                ctx.restore();
            },
        };
        const band = {
            id: "wsFinanceBand",
            beforeDatasetsDraw: (chart) => {
                if (sel < 0 || !chart.data.labels.length) {
                    return;
                }
                const x = chart.scales.x;
                const area = chart.chartArea;
                const n = chart.data.labels.length;
                const step = n > 1 ? Math.abs(x.getPixelForValue(1) - x.getPixelForValue(0)) : area.right - area.left;
                const left = Math.max(area.left, x.getPixelForValue(sel) - step / 2);
                const right = Math.min(area.right, x.getPixelForValue(sel) + step / 2);
                chart.ctx.save();
                chart.ctx.fillStyle = c.band;
                chart.ctx.fillRect(left, area.top, right - left, area.bottom - area.top);
                chart.ctx.restore();
            },
        };
        this.charts.push(
            new Chart(el, {
                type: "bar",
                data: {
                    labels: s.labels,
                    datasets: [
                        {
                            label: s.net_label,
                            data: s.net,
                            backgroundColor: s.net.map((v, i) => (v < 0 ? c.danger : i === sel ? c.brand : c.soft)),
                            borderRadius: 3,
                            maxBarThickness: 48,
                            order: 2,
                        },
                        {
                            type: "line",
                            label: s.base_label,
                            data: s.base,
                            borderColor: c.line,
                            backgroundColor: c.line,
                            borderWidth: 2,
                            pointRadius: 3,
                            pointHitRadius: 6,
                            tension: 0,
                            order: 1,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    interaction: { mode: "index", intersect: false },
                    layout: { padding: { top: 8, bottom: 4 } },
                    ...this.chartPick(
                        (i) => this.setMonth(s.months[i]),
                        (i) => s.net[i] !== null && s.net[i] !== undefined
                    ),
                    plugins: {
                        legend: {
                            display: true,
                            position: "bottom",
                            onClick: () => {},
                            labels: {
                                color: c.text,
                                boxWidth: 14,
                                boxHeight: 3,
                                generateLabels: () => [
                                    { text: s.net_label, fillStyle: c.brand, strokeStyle: c.brand, lineWidth: 0, fontColor: c.text, hidden: false },
                                    { text: s.base_label, fillStyle: c.line, strokeStyle: c.line, lineWidth: 2, fontColor: c.text, hidden: false },
                                ],
                            },
                        },
                        tooltip: {
                            callbacks: {
                                label: (ctx) => `${ctx.dataset.label || ""}: ${(ctx.datasetIndex === 0 ? s.net_text : s.base_text)[ctx.dataIndex]}`,
                            },
                        },
                    },
                    scales: {
                        x: {
                            ticks: { color: (ctx) => (ctx.index === sel ? c.brand : c.text), font: (ctx) => (ctx.index === sel ? { weight: "600" } : {}) },
                            grid: { display: false },
                        },
                        y: {
                            beginAtZero: true,
                            // місце зверху на два підписи (стовпця й лінії «до часток»)
                            grace: "20%",
                            ticks: { color: c.text },
                            grid: { color: (ctx) => (ctx.tick && ctx.tick.value === 0 ? c.zero : c.grid) },
                        },
                    },
                },
                plugins: [band, values],
            })
        );
    }
}

registry.category("actions").add("workset_finance.owner", FinanceOwner);
