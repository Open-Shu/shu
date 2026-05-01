import {
  Box,
  LinearProgress,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';

import {
  formatCompactTokens,
  formatCurrency,
  formatFullTokens,
  computeSharePercent,
} from '../../utils/billingFormatters';

const UNATTRIBUTED_TOOLTIP =
  'Usage where the model could not be resolved (e.g. provider returned no model identifier).';

/**
 * Build a stable, presentation-ready row from a raw `by_model` entry plus
 * the resolved `modelsMap`. Pure so it can be exported and unit-tested.
 */
export function buildModelRow(rawRow, modelsMap, totalCost) {
  const isUnattributed = !rawRow.model_id;
  let displayName;
  let providerName = null;

  if (isUnattributed) {
    displayName = 'Unattributed';
  } else {
    const resolved = modelsMap?.get?.(rawRow.model_id);
    if (resolved && resolved.display_name) {
      displayName = resolved.display_name;
      providerName = resolved.provider_name || null;
    } else if (rawRow.model_name) {
      // Backend snapshots `model_name` per usage row at insert time (SHU-727)
      // specifically so deleted models still surface a readable label here.
      // Prefer this over a truncated UUID when the live catalog can't resolve
      // the id (e.g., model was deleted; lookup hasn't loaded yet).
      displayName = rawRow.model_name;
    } else {
      // Last-resort fallback when the row predates the snapshot column or
      // the snapshot is empty. Short truncated UUID still lets admins
      // cross-reference against the database.
      const id = String(rawRow.model_id);
      displayName = id.length > 12 ? `model_${id.slice(0, 8)}` : `model_${id}`;
    }
  }

  return {
    key: rawRow.model_id || '__unattributed__',
    displayName,
    providerName,
    cost: rawRow.cost_usd ?? 0,
    inputTokens: rawRow.input_tokens ?? 0,
    outputTokens: rawRow.output_tokens ?? 0,
    requestCount: rawRow.request_count ?? 0,
    sharePercent: computeSharePercent(rawRow.cost_usd ?? 0, totalCost),
    isUnattributed,
  };
}

/**
 * Order rows for display: by cost desc, with any "unattributed" row pinned
 * to the bottom regardless of its cost. Pure helper for unit testing.
 */
export function orderRows(rows) {
  const named = rows.filter((r) => !r.isUnattributed);
  const unattributed = rows.filter((r) => r.isUnattributed);
  named.sort((a, b) => (b.cost ?? 0) - (a.cost ?? 0));
  return [...named, ...unattributed];
}

function ShareBar({ percent }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1} sx={{ minWidth: 140 }}>
      <Box sx={{ flexGrow: 1 }}>
        <LinearProgress
          variant="determinate"
          value={percent}
          color="primary"
          aria-label={`${percent}% of total cost`}
        />
      </Box>
      <Typography variant="body2" color="text.secondary" sx={{ minWidth: 32, textAlign: 'right' }}>
        {percent}%
      </Typography>
    </Stack>
  );
}

function ModelCell({ row }) {
  const nameNode = (
    <Typography variant="body2" sx={{ fontWeight: 500 }}>
      {row.displayName}
      {row.isUnattributed && (
        <Tooltip title={UNATTRIBUTED_TOOLTIP} arrow>
          <InfoOutlinedIcon
            data-testid="unattributed-info-icon"
            fontSize="inherit"
            sx={{ ml: 0.5, verticalAlign: 'middle', color: 'text.secondary' }}
          />
        </Tooltip>
      )}
    </Typography>
  );

  return (
    <>
      {nameNode}
      {row.providerName && (
        <Typography variant="caption" color="text.secondary">
          {row.providerName}
        </Typography>
      )}
    </>
  );
}

const EmptyState = ({ children }) => (
  <Box sx={{ py: 4, textAlign: 'center' }}>
    <Typography variant="body2" color="text.secondary">
      {children}
    </Typography>
  </Box>
);

/**
 * Cost by Model table — sorted by cost desc with `<LinearProgress>` share
 * bars and "Unattributed" rows pinned to the bottom.
 *
 * Render is gated only on the usage query, not on the models/providers
 * lookup. Each row's label resolves through three tiers (live catalog
 * display name → backend snapshot model_name → truncated UUID) so the
 * table can display meaningful labels even before /llm/models loads or
 * for models that have since been deleted.
 */
function CostByModelTable({ usageQuery, modelsMap }) {
  if (usageQuery.isLoading) {
    return (
      <Box>
        <Skeleton variant="rounded" height={48} sx={{ mb: 1 }} />
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} variant="rounded" height={56} sx={{ mb: 1 }} />
        ))}
      </Box>
    );
  }

  const data = usageQuery.data;
  if (!data) {
    return <EmptyState>No data available.</EmptyState>;
  }

  if (data.current_period_unknown) {
    return <EmptyState>Cost data will appear here once a billing period is active.</EmptyState>;
  }

  const byModel = Array.isArray(data.by_model) ? data.by_model : [];
  if (byModel.length === 0) {
    return <EmptyState>No LLM usage recorded in this billing period yet.</EmptyState>;
  }

  const totalCost = data.total_cost_usd ?? 0;
  const rows = orderRows(byModel.map((row) => buildModelRow(row, modelsMap, totalCost)));

  return (
    <TableContainer>
      <Table size="small" aria-label="Cost by model">
        <TableHead>
          <TableRow>
            <TableCell>Model</TableCell>
            <TableCell align="right">Cost</TableCell>
            <TableCell sx={{ minWidth: 180 }}>Share</TableCell>
            <TableCell align="right">Input Tokens</TableCell>
            <TableCell align="right">Output Tokens</TableCell>
            <TableCell align="right">Requests</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.key} hover>
              <TableCell>
                <ModelCell row={row} />
              </TableCell>
              <TableCell align="right">{formatCurrency(row.cost)}</TableCell>
              <TableCell>
                <ShareBar percent={row.sharePercent} />
              </TableCell>
              <TableCell align="right">
                <Tooltip title={formatFullTokens(row.inputTokens)} arrow>
                  <span aria-label={`Input tokens: ${formatFullTokens(row.inputTokens)}`}>
                    {formatCompactTokens(row.inputTokens)}
                  </span>
                </Tooltip>
              </TableCell>
              <TableCell align="right">
                <Tooltip title={formatFullTokens(row.outputTokens)} arrow>
                  <span aria-label={`Output tokens: ${formatFullTokens(row.outputTokens)}`}>
                    {formatCompactTokens(row.outputTokens)}
                  </span>
                </Tooltip>
              </TableCell>
              <TableCell align="right">{formatFullTokens(row.requestCount)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

export default CostByModelTable;
