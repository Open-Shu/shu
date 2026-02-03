export function formatLocalShort(ts) {
  if (!ts) {
    return "-";
  }
  const d = new Date(ts);
  if (isNaN(d)) {
    return String(ts);
  }
  const parts = new Intl.DateTimeFormat(undefined, {
    year: "2-digit",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).formatToParts(d);
  const pick = (type) => parts.find((p) => p.type === type)?.value || "";
  const mm = pick("month");
  const dd = pick("day");
  const yy = pick("year");
  const hh = pick("hour");
  const min = pick("minute");
  const dayPeriod = pick("dayPeriod");
  return `${mm}/${dd}/${yy} ${hh}:${min} ${dayPeriod}`;
}
