/**
 * Tests for WelcomePanel (SHU-873) — the welcoming personality layer shared by
 * the chat landing screen and the new-chat empty state.
 *
 * Asserts the load-bearing behavior: the name greeting renders (with a safe
 * anonymous fallback), starter chips prefill via onSeedPrompt without
 * auto-sending, the hero New Chat button is landing-only, and the dead-CTA /
 * loading states render instead of a silently-broken button.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';

import WelcomePanel from '../WelcomePanel';
import { STARTER_CHIPS } from '../../chat/ModernChat/utils/welcomeCopy';

const MODELS = [
  { id: 'm1', name: 'GPT-4o', model_name: 'gpt-4o', llm_provider: { name: 'OpenAI' } },
  { id: 'm2', name: 'Claude', model_name: 'claude-opus-4', llm_provider: { name: 'Anthropic' } },
];

const renderPanel = (props = {}) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <WelcomePanel
        variant="landing"
        user={{ name: 'Eric Longville', email: 'eric@example.com' }}
        appDisplayName="Bad Hops LLC"
        brandingLoaded
        availableModelConfigs={MODELS}
        selectedModelConfig="m1"
        onModelChange={vi.fn()}
        modelsLoading={false}
        personalKB={null}
        personalKBLoading={false}
        onSeedPrompt={vi.fn()}
        onCreateConversation={vi.fn()}
        createDisabled={false}
        canStartChat
        {...props}
      />
    </ThemeProvider>
  );

// The chips shown are a random subset each mount; find whichever ones rendered.
// queryAllByRole (not getAllByRole) so it returns [] rather than throwing when
// the reason/loading states render no buttons at all.
const renderedChipButtons = () =>
  screen.queryAllByRole('button').filter((b) => STARTER_CHIPS.some((c) => c.label === b.textContent));

beforeEach(() => {
  window.localStorage.clear();
});

describe('WelcomePanel', () => {
  it('greets the user by derived first name and shows a hero New Chat (landing)', () => {
    renderPanel();
    expect(screen.getByRole('heading', { level: 4 }).textContent).toMatch(/Eric/);
    expect(screen.getByRole('button', { name: /new chat/i })).toBeInTheDocument();
  });

  it('renders a feather glyph and the org name', () => {
    const { container } = renderPanel();
    expect(container.querySelector('svg')).toBeTruthy();
    expect(screen.getByText('Bad Hops LLC')).toBeInTheDocument();
  });

  it('hides the org name until branding has loaded (no "Shu" placeholder flash)', () => {
    renderPanel({ brandingLoaded: false, appDisplayName: 'Shu' });
    expect(screen.queryByText('Shu')).not.toBeInTheDocument();
  });

  it('shows the Personal KB document count when it has documents', () => {
    renderPanel({ personalKB: { id: 'pk', document_count: 3 } });
    expect(screen.getByText(/3 docs/)).toBeInTheDocument();
  });

  it('does not mark an existing-but-empty Personal KB as ready', () => {
    renderPanel({ personalKB: { id: 'pk', document_count: 0 } });
    expect(screen.getByText('Add to Personal Knowledge')).toBeInTheDocument();
    expect(screen.queryByText(/\d+ docs?/)).not.toBeInTheDocument();
  });

  it('disables the model selector while a model switch is in flight', () => {
    renderPanel({ modelSwitchInProgress: true });
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-disabled', 'true');
  });

  it('uses a safe anonymous greeting (no empty-name artifact) when user is missing', () => {
    renderPanel({ user: null });
    const heading = screen.getByRole('heading', { level: 4 }).textContent;
    expect(heading.length).toBeGreaterThan(0);
    expect(heading).not.toContain('{name}');
    // No dangling "Welcome back, ." comma where the name should have been.
    expect(heading).not.toMatch(/,\s*[.?]?\s*$/);
  });

  it('prefills via onSeedPrompt when a starter chip is clicked, and never auto-sends', () => {
    const onSeedPrompt = vi.fn();
    const onCreateConversation = vi.fn();
    renderPanel({ variant: 'empty-chat', onSeedPrompt, onCreateConversation });

    const chips = renderedChipButtons();
    expect(chips.length).toBeGreaterThan(0);

    fireEvent.click(chips[0]);
    const expectedPrompt = STARTER_CHIPS.find((c) => c.label === chips[0].textContent).prompt;
    expect(onSeedPrompt).toHaveBeenCalledWith(expectedPrompt);
    // Prefill only — the empty-state variant has no create/send side effect.
    expect(onCreateConversation).not.toHaveBeenCalled();
  });

  it('fires onSeedPrompt (prefill, no auto-send) when a chip is clicked on the landing variant', () => {
    const onSeedPrompt = vi.fn();
    const onCreateConversation = vi.fn();
    renderPanel({ variant: 'landing', onSeedPrompt, onCreateConversation });

    const chips = renderedChipButtons();
    expect(chips.length).toBeGreaterThan(0);

    fireEvent.click(chips[0]);
    const expectedPrompt = STARTER_CHIPS.find((c) => c.label === chips[0].textContent).prompt;
    expect(onSeedPrompt).toHaveBeenCalledWith(expectedPrompt);
  });

  it('disables landing starter chips while a create is in flight (guards double-create)', () => {
    const onSeedPrompt = vi.fn();
    renderPanel({ variant: 'landing', createDisabled: true, onSeedPrompt });

    const chips = renderedChipButtons();
    expect(chips.length).toBeGreaterThan(0);
    fireEvent.click(chips[0]);
    // Even if MUI still dispatches the click, the seedDisabled guard suppresses it.
    expect(onSeedPrompt).not.toHaveBeenCalled();
    // The hero New Chat is likewise disabled.
    expect(screen.getByRole('button', { name: /new chat/i })).toBeDisabled();
  });

  it('omits the hero New Chat button in the empty-chat variant', () => {
    renderPanel({ variant: 'empty-chat' });
    expect(screen.queryByRole('button', { name: /new chat/i })).not.toBeInTheDocument();
    expect(renderedChipButtons().length).toBeGreaterThan(0);
  });

  it('shows a reason state instead of a dead CTA when no models are configured', () => {
    renderPanel({ availableModelConfigs: [], modelsLoading: false });
    expect(screen.getByText(/no models are configured/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /new chat/i })).not.toBeInTheDocument();
    expect(renderedChipButtons()).toHaveLength(0);
  });

  it('shows loading skeletons while model configs are still fetching', () => {
    const { container } = renderPanel({ modelsLoading: true, availableModelConfigs: [] });
    expect(container.querySelectorAll('.MuiSkeleton-root').length).toBeGreaterThan(0);
    expect(screen.queryByText(/no models are configured/i)).not.toBeInTheDocument();
  });

  it('honors prefers-reduced-motion without crashing (static feather)', () => {
    const matchMediaMock = vi.fn().mockImplementation((query) => ({
      matches: /reduce/.test(query),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    Object.defineProperty(window, 'matchMedia', { writable: true, configurable: true, value: matchMediaMock });

    expect(() => renderPanel()).not.toThrow();
    expect(screen.getByRole('heading', { level: 4 })).toBeInTheDocument();

    delete window.matchMedia;
  });
});
