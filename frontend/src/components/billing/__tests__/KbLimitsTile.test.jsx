/**
 * Tests for KbLimitsTile (SHU-844): tenant-wide KB document/KB-count limits.
 * Renders only when CP supplies limits + usage, and only for caps > 0.
 */

import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';

import KbLimitsTile from '../KbLimitsTile';

const renderTile = (usage, limits) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <KbLimitsTile usage={usage} limits={limits} />
    </ThemeProvider>
  );

describe('KbLimitsTile', () => {
  describe('shown', () => {
    it('renders documents and KB tiles with used/cap and a workspace caption', () => {
      renderTile({ kb_count: 2, document_count: 40 }, { kb_count_limit: 5, document_count_limit: 1000 });
      expect(screen.getByText('Documents')).toBeInTheDocument();
      expect(screen.getByText('Knowledge Bases')).toBeInTheDocument();
      expect(screen.getByText('40 / 1,000')).toBeInTheDocument();
      expect(screen.getByText('2 / 5')).toBeInTheDocument();
      expect(screen.getAllByText('Across your workspace')).toHaveLength(2);
      expect(screen.getAllByRole('progressbar')).toHaveLength(2);
    });

    it('clamps the bar at 100% when usage exceeds the cap (batch overshoot)', () => {
      renderTile({ kb_count: 1, document_count: 1500 }, { kb_count_limit: 5, document_count_limit: 1000 });
      expect(screen.getByText('1,500 / 1,000')).toBeInTheDocument();
      expect(screen.getByLabelText(/100% of documents limit used/)).toBeInTheDocument();
    });
  });

  describe('hidden', () => {
    it('returns null when limits is absent (self-hosted / CP-less)', () => {
      const { container } = renderTile({ kb_count: 2, document_count: 40 }, null);
      expect(container.firstChild).toBeNull();
    });

    it('returns null when usage is absent', () => {
      const { container } = renderTile(null, { kb_count_limit: 5, document_count_limit: 1000 });
      expect(container.firstChild).toBeNull();
    });

    it('returns null when both caps are zero (cold-start fail-closed)', () => {
      const { container } = renderTile(
        { kb_count: 0, document_count: 0 },
        { kb_count_limit: 0, document_count_limit: 0 }
      );
      expect(container.firstChild).toBeNull();
    });

    it('omits a stat whose cap is zero', () => {
      renderTile({ kb_count: 2, document_count: 40 }, { kb_count_limit: 5, document_count_limit: 0 });
      expect(screen.queryByText('Documents')).not.toBeInTheDocument();
      expect(screen.getByText('Knowledge Bases')).toBeInTheDocument();
      expect(screen.getByText('2 / 5')).toBeInTheDocument();
    });
  });
});
