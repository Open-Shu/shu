import { Box, LinearProgress } from '@mui/material';

import { KpiTile, pickUsedColor } from './KpiTiles';
import { formatFullTokens } from '../../utils/billingFormatters';

// Clamp at 100% — concurrent plugin/feed batch ingestion can transiently
// overshoot the cap, so usage isn't guaranteed <= limit.
function buildStat(label, used, cap) {
  const u = Math.max(used ?? 0, 0);
  const percent = Math.min(Math.round((u / cap) * 100), 100);
  return { key: label, label, value: `${formatFullTokens(u)} / ${formatFullTokens(cap)}`, percent };
}

/**
 * Workspace (tenant-wide) Knowledge Base limit tiles for My Usage (SHU-844):
 * documents-used / cap and KBs-used / cap, each with a color-banded bar.
 *
 * These are TENANT-shared counts — the kb_count/document_count + limits blocks
 * on /billing/subscription are tenant-wide RLS counts, not per-user — so they're
 * framed "Across your workspace", mirroring the Shared Pool tile. Renders only
 * when control-plane supplies the limits/usage blocks (hidden self-hosted) and
 * only for caps > 0 (0 is a real zero cap on cold-start, not "unlimited").
 */
export default function KbLimitsTile({ usage, limits }) {
  if (!usage || !limits) {
    return null;
  }

  const stats = [];
  if (limits.document_count_limit > 0) {
    stats.push(buildStat('Documents', usage.document_count, limits.document_count_limit));
  }
  if (limits.kb_count_limit > 0) {
    stats.push(buildStat('Knowledge Bases', usage.kb_count, limits.kb_count_limit));
  }
  if (!stats.length) {
    return null;
  }

  return (
    <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)' } }}>
      {stats.map((s) => (
        <KpiTile
          key={s.key}
          label={s.label}
          value={s.value}
          ariaLabel={`${s.label}: ${s.value} across your workspace`}
          subline="Across your workspace"
          bottom={
            <LinearProgress
              variant="determinate"
              value={s.percent}
              color={pickUsedColor(s.percent)}
              aria-label={`${s.percent}% of ${s.label.toLowerCase()} limit used`}
            />
          }
        />
      ))}
    </Box>
  );
}
