import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Box, Chip, Stack, Typography } from '@mui/material';
import { BarChart } from '@mui/x-charts/BarChart';

import { buildDailySeries } from '../../utils/myUsageChart';
import { formatCurrency } from '../../utils/billingFormatters';

// Distinct, dark-theme-friendly categorical palette. Each model gets a stable
// color by its index in the FULL series list (not the visible subset), so
// colors don't reshuffle as series are toggled. 16 entries — wider than
// @mui/x-charts' default cycle, which repeated hues once more than ~8 models
// were charted, making similar lines hard to tell apart.
const SERIES_COLORS = [
  '#60a5fa',
  '#f59e0b',
  '#34d399',
  '#f472b6',
  '#a78bfa',
  '#facc15',
  '#22d3ee',
  '#fb7185',
  '#a3e635',
  '#c084fc',
  '#fdba74',
  '#2dd4bf',
  '#e879f9',
  '#38bdf8',
  '#4ade80',
  '#fca5a5',
];

// Stable references for the chart's props, defined once at module scope, plus
// the per-render derived props (series, xAxis) memoized below — @mui/x-charts
// re-runs internal layout effects when its prop identities change, so keeping
// them stable avoids needless re-renders.
const seriesValueFormatter = (v) => (v === null || v === undefined ? '' : formatCurrency(v));
const Y_AXIS = [{ valueFormatter: (v) => formatCurrency(v) }];
const CHART_MARGIN = { left: 72 };

// Format a "YYYY-MM-DD" UTC day key into a short axis label like "Jun 1". v8
// drops x-axis tick labels that would overlap their neighbour
// (ChartsXAxis/getVisibleLabels); full ISO strings are wide enough that nearly
// all of them collapse, so we render a narrow label that fits. Parsed at UTC
// midnight to match the "Day (UTC)" axis (a bare "YYYY-MM-DD" parses as local
// and would shift a day in negative-offset zones).
const fmtDay = (iso) => {
  const d = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' });
};

const periodTotal = (s) => s.data.reduce((sum, v) => sum + (v || 0), 0);

/**
 * Daily cost chart for My Usage (SHU-844): one stacked bar per day, split by
 * model, with a click-to-toggle legend. Stacked bars (not a line) because daily
 * cost buckets are discrete — a line would imply continuity between days that
 * doesn't exist. Imported lazily by MyUsagePage so @mui/x-charts and its d3
 * vendor bundle stay code-split to this route.
 *
 * The toggle chips double as the legend (each carries its segment's color dot),
 * so the chart's built-in legend is hidden — with many models it wrapped over
 * the plot area and y-axis labels.
 */
export default function MyUsageChart({ byDay, modelsMap }) {
  const { dates, series } = useMemo(() => buildDailySeries(byDay, modelsMap), [byDay, modelsMap]);
  // Models hidden via the legend toggle; default all visible.
  const [hidden, setHidden] = useState(() => new Set());

  // Animate only the initial mount (the load grow-in); make legend toggles
  // instant. v8 animates a series entering the series array from the x-axis
  // baseline upward (useAnimateBar), which on a stacked chart reads as the
  // toggled slice flying up through the other colors — and there's no enter-only
  // animation knob. So animate the first render, then switch to instant updates.
  // A ref (not state) so flipping it never triggers an extra render that could
  // cut the load animation short.
  const mountedRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
  }, []);

  // Order biggest-spend first so the chart, legend, and tooltip all read
  // most-to-least (the tooltip renders series in array order). Colors are then
  // assigned by rank — stable across toggles, since the full list never changes.
  // Memoized so the identity is stable across tooltip-driven re-renders.
  const colored = useMemo(
    () =>
      [...series]
        .sort((a, b) => periodTotal(b) - periodTotal(a))
        .map((s, i) => ({ ...s, color: SERIES_COLORS[i % SERIES_COLORS.length] })),
    [series]
  );

  // Visible series only; toggled-off models drop from both the chart and the
  // tooltip. Shared stack key → one stacked bar per day (height = daily total).
  // Stable `id` (the model id) so v8 diffs series by identity across toggles
  // rather than re-keying them by array index.
  const chartSeries = useMemo(
    () =>
      colored
        .filter((s) => !hidden.has(s.modelId))
        .map((s) => ({
          id: s.modelId,
          data: s.data,
          label: s.label,
          color: s.color,
          stack: 'cost',
          valueFormatter: seriesValueFormatter,
        })),
    [colored, hidden]
  );

  // Slim the bars when only a few days are present (e.g. the start of a billing
  // period) so they don't balloon to fill their band; let them fill out as days
  // accrue. x-charts has no absolute max-bar-width knob, so this gap ratio —
  // bar width as a fraction of the band — is the available lever.
  const xAxis = useMemo(
    () => [
      {
        data: dates,
        scaleType: 'band',
        label: 'Day (UTC)',
        valueFormatter: fmtDay,
        categoryGapRatio: dates.length <= 3 ? 0.7 : 0.3,
        // v8 splits the axis height between the axis label and the tick labels;
        // at the default 45px, "Day (UTC)" leaves too little for the dates and
        // shortenLabels ellipsizes them to nothing. Reserve enough for both.
        height: 60,
      },
    ],
    [dates]
  );

  const toggle = useCallback(
    (modelId) =>
      setHidden((prev) => {
        const next = new Set(prev);
        if (next.has(modelId)) {
          next.delete(modelId);
        } else {
          next.add(modelId);
        }
        return next;
      }),
    []
  );

  if (!dates.length) {
    return (
      <Box sx={{ py: 4, textAlign: 'center' }}>
        <Typography variant="body2" color="text.secondary">
          No usage to chart in this billing period yet.
        </Typography>
      </Box>
    );
  }

  return (
    <Box>
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mb: 1.5 }}>
        {colored.map((s) => {
          const isHidden = hidden.has(s.modelId);
          return (
            <Chip
              key={s.modelId}
              size="small"
              variant={isHidden ? 'outlined' : 'filled'}
              onClick={() => toggle(s.modelId)}
              aria-pressed={!isHidden}
              icon={
                <Box
                  component="span"
                  sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: s.color, flexShrink: 0 }}
                />
              }
              label={s.label}
              sx={{ opacity: isHidden ? 0.45 : 1 }}
            />
          );
        })}
      </Stack>
      <BarChart
        height={300}
        xAxis={xAxis}
        yAxis={Y_AXIS}
        series={chartSeries}
        hideLegend
        skipAnimation={mountedRef.current}
        margin={CHART_MARGIN}
      />
    </Box>
  );
}
