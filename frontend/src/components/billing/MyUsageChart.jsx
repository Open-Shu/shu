import { useMemo, useState } from 'react';
import { Box, Chip, Stack, Typography } from '@mui/material';
import { LineChart } from '@mui/x-charts/LineChart';

import { buildDailySeries } from '../../utils/myUsageChart';
import { formatCurrency } from '../../utils/billingFormatters';

/**
 * Daily cost-over-time chart for My Usage (SHU-844): one line per model with a
 * click-to-toggle legend. Imported lazily by MyUsagePage so @mui/x-charts and
 * its d3 vendor bundle stay code-split to this route — they are not in the
 * app's initial bundle.
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

  const chartSeries = series
    .filter((s) => !hidden.has(s.modelId))
    .map((s) => ({
      data: s.data,
      label: s.label,
      showMark: false,
      valueFormatter: (v) => (v === null || v === undefined ? '' : formatCurrency(v)),
    }));

  return (
    <Box>
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mb: 1.5 }}>
        {series.map((s) => {
          const isHidden = hidden.has(s.modelId);
          return (
            <Chip
              key={s.modelId}
              label={s.label}
              size="small"
              variant={isHidden ? 'outlined' : 'filled'}
              color={isHidden ? 'default' : 'primary'}
              onClick={() => toggle(s.modelId)}
              aria-pressed={!isHidden}
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
        margin={{ left: 72 }}
      />
    </Box>
  );
}
