// Money arithmetic shared by the server and the claim editor in the browser.
//
// Every amount is an integer number of minor units (pence, cents). Exchange rates are
// integers too: hundredths of a penny per minor unit (EUR 0.86 -> 86), so a converted line
// is an exact integer of hundredths and nothing is rounded until a total is shown.

export const SYMBOLS = Object.freeze({ GBP: "£", EUR: "€", USD: "$" });
export const CURRENCIES = Object.freeze(Object.keys(SYMBOLS));

const AMOUNT = /^(\d{1,10})(?:\.(\d{1,2}))?$/;

/** "42" -> 4200, "18.4" -> 1840; null for anything that is not a plain amount. */
export function parseMinor(text) {
  const match = AMOUNT.exec(String(text ?? "").trim());
  if (!match) return null;
  return Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0"));
}

/** Rates as configured ("0.86") -> hundredths of a penny per minor unit (86). */
export function parseRates(table) {
  const rates = {};
  for (const code of CURRENCIES) {
    const rate = parseMinor(table[code]);
    if (rate === null) throw new Error(`no usable rate for ${code}`);
    rates[code] = rate;
  }
  return rates;
}

/** One line's value in hundredths of a penny: exact, unrounded. */
export function sterlingHundredths(minor, currency, rates) {
  return minor * rates[currency];
}

/** Hundredths of a penny -> whole pence, half up. */
export function roundToPence(hundredths) {
  return Math.floor((hundredths + 50) / 100);
}

/** 123456 -> "1,234.56" */
export function formatMinor(minor) {
  const sign = minor < 0 ? "-" : "";
  const whole = Math.floor(Math.abs(minor) / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const part = String(Math.abs(minor) % 100).padStart(2, "0");
  return `${sign}${whole}.${part}`;
}

export function money(currency, minor) {
  return `${SYMBOLS[currency]}${formatMinor(minor)}`;
}
