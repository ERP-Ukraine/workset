/** Числа «Огляду» з роздільником тисяч (слово власника 06.11: цифри — з розділювачами): нерозривний пробіл між трійками цифр (число не
 *  переноситься), дробова частина — digits знаків через decimalPoint мови користувача; trailingZeros = false — без зайвих нулів у кінці.
 *  Чиста функція без Odoo — її виконує node-тест (tools/tests/odoo_workset_rates_module.test.js). */
export function groupNumber(value, { digits = 2, trailingZeros = true, decimalPoint = "," } = {}) {
    const n = Number(value) || 0;
    const text = Math.abs(n).toFixed(digits);
    const [whole, part = ""] = text.split(".");
    const frac = trailingZeros ? part : part.replace(/0+$/, "");
    // мінус — лише коли після округлення лишилось не нуль («−0,00» не показуємо)
    const sign = n < 0 && /[1-9]/.test(text) ? "-" : "";
    return sign + whole.replace(/\B(?=(\d{3})+(?!\d))/g, " ") + (frac ? decimalPoint + frac : "");
}

/** Колір з обчисленого стилю («rgb(r, g, b)» / «rgba(…)», як віддає getComputedStyle) → той самий колір із прозорістю alpha (сітка й
 *  нейтральний сектор графіків «Огляду» — від кольору тексту теми, світлої й темної). Інший запис або порожньо — undefined (Chart.js тоді
 *  бере свій типовий колір). */
export function withAlpha(color, alpha) {
    const m = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i.exec(String(color || "").trim());
    return m ? `rgba(${m[1]}, ${m[2]}, ${m[3]}, ${alpha})` : undefined;
}
