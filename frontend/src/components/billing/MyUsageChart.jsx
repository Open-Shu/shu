import { useMemo, useState } from 'react';
import { Box, Chip, Stack, Typography } from '@mui/material';
import { LineChart } from '@mui/x-charts/LineChart';

import { buildDailySeries } from '../../utils/myUsageChart';
import { formatCurrency } from '../../utils/billingFormatters';

// Distinct, dark-theme-friendly categorical palette. Each model gets a stable
// color by its index in the FULL series list (not the visible subset), so
// colors don't reshuffle as lines are toggled. 16 entries — wider than
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

/**
 * Daily cost-over-time chart for My Usage (SHU-844): one line per model with a
 * click-to-toggle legend. Imported lazily by MyUsagePage so @mui/x-charts and
 * its d3 vendor bundle stay code-split to this route.
 *
 * The toggle chips double as the legend (each carries its line's color dot), so
 * the chart's built-in legend is hidden — with many models it wrapped over the
 * plot area and y-axis labels.
 */
export default function MyUsageChart({ byDay, modelsMap }) {
  const { dates, series } = useMemo(() => buildDailySeries(byDay, modelsMap), [byDay, modelsMap]);
  // Models hidden via the legend toggle; default all visible.
  const [hidden, setHidden] = useState(() => new Set());

  if (!dates.length) {
    return (
      <Box sx={{ py: 4, textAlign: 'center' }}>
        <Typography variant="body2" color="text.secondary">
          No usage to chart in this billing period yet.
        </Typography>
      </Box>
    );
  }

  const toggle = (modelId) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(modelId)) {
        next.delete(modelId);
      } else {
        next.add(modelId);
      }
      return next;
    });

  // Order biggest-spend first so the chart, legend, and tooltip all read
  // most-to-least (the tooltip renders series in array order). Colors are then
  // assigned by rank — stable across toggles, since the full list never changes.
  const periodTotal = (s) => s.data.reduce((sum, v) => sum + (v || 0), 0);
  const colored = [...series]
    .sort((a, b) => periodTotal(b) - periodTotal(a))
    .map((s, i) => ({ ...s, color: SERIES_COLORS[i % SERIES_COLORS.length] }));

  const chartSeries = colored
    .filter((s) => !hidden.has(s.modelId))
    .map((s) => ({
      data: s.data,
      label: s.label,
      color: s.color,
      showMark: false,
      valueFormatter: (v) => (v === null || v === undefined ? '' : formatCurrency(v)),
    }));

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
      <LineChart
        height={300}
        xAxis={[{ data: dates, scaleType: 'point', label: 'Day (UTC)' }]}
        yAxis={[{ valueFormatter: (v) => formatCurrency(v) }]}
        series={
          chartSeries.length
            ? chartSeries
            : [{ data: dates.map(() => 0), label: 'No models selected', showMark: false }]
        }
        slotProps={{ legend: { hidden: true } }}
        margin={{ left: 72 }}
      />
    </Box>
  );
}
