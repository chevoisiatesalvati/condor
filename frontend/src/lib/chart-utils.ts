/** lightweight-charts requires strictly ascending unique timestamps. */

export function dedupSortedByTime<T extends { time: number }>(data: T[]): T[] {
  if (data.length <= 1) return data;
  const sorted = [...data].sort((a, b) => a.time - b.time);
  const result: T[] = [sorted[0]];
  for (let i = 1; i < sorted.length; i++) {
    const point = sorted[i];
    const last = result[result.length - 1];
    if (point.time === last.time) {
      result[result.length - 1] = point;
    } else if (point.time > last.time) {
      result.push(point);
    }
  }
  return result;
}

export interface ChartCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

/** Close price from the candle nearest ``timestampSec`` (within 10 minutes). */
export function candlePriceAt(
  timestampSec: number,
  candles: Array<{ timestamp?: number; time?: number; close: number }>,
): number {
  if (!timestampSec || !candles.length) return 0;
  const target =
    timestampSec > 1e12 ? Math.floor(timestampSec / 1000) : Math.floor(timestampSec);
  let bestClose = 0;
  let bestDist = Infinity;
  for (const c of candles) {
    const raw = c.timestamp ?? c.time ?? 0;
    const t = raw > 1e12 ? Math.floor(raw / 1000) : Math.floor(raw);
    if (!t) continue;
    const dist = Math.abs(t - target);
    if (dist < bestDist) {
      bestDist = dist;
      bestClose = c.close;
    }
  }
  return bestDist <= 600 ? bestClose : 0;
}

/** Merge duplicate candle timestamps (keep open from first, close from last). */
export function dedupCandlesByTime<T extends ChartCandle>(data: T[]): T[] {
  if (data.length <= 1) return data;
  const sorted = [...data].sort((a, b) => a.time - b.time);
  const result: T[] = [sorted[0]];
  for (let i = 1; i < sorted.length; i++) {
    const cur = sorted[i];
    const last = result[result.length - 1];
    if (cur.time === last.time) {
      result[result.length - 1] = {
        ...last,
        high: Math.max(last.high, cur.high),
        low: Math.min(last.low, cur.low),
        close: cur.close,
      };
    } else if (cur.time > last.time) {
      result.push(cur);
    }
  }
  return result;
}
