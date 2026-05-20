import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { billingAPI, extractDataFromResponse } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import log from '../utils/log';

const POLL_INTERVAL_MS = 60_000;

// Decimal fields arrive as strings from the backend (stringified to dodge
// JSON-number precision loss). Number('') and Number('abc') yield 0 and NaN
// respectively, both of which would silently feed into .toFixed() in the
// trial banner. Normalise to null on anything non-finite so consumers can
// treat absence and parse-failure the same way.
const parseDecimalField = (value) => {
  if (value === null || value === undefined) {
    return null;
  }
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
};

const HEALTHY_STATE = {
  paymentFailedAt: null,
  graceDeadline: null,
  servicePaused: false,
  isTrial: false,
  trialDeadline: null,
  totalGrantAmount: null,
  remainingGrantAmount: null,
  seatPriceUsd: null,
  userCount: 0,
};

const BillingStatusContext = createContext(null);

export const useBillingStatus = () => {
  const ctx = useContext(BillingStatusContext);
  if (!ctx) {
    throw new Error('useBillingStatus must be used within a BillingStatusProvider');
  }
  return ctx;
};

export const BillingStatusProvider = ({ children }) => {
  const { isAuthenticated } = useAuth();
  const [status, setStatus] = useState(HEALTHY_STATE);
  const [loading, setLoading] = useState(true);

  const fetchBillingStatus = useCallback(async () => {
    try {
      const response = await billingAPI.getSubscription();
      const data = extractDataFromResponse(response) || {};
      setStatus({
        paymentFailedAt: data.payment_failed_at ?? null,
        graceDeadline: data.grace_deadline ?? null,
        servicePaused: Boolean(data.service_paused),
        isTrial: Boolean(data.is_trial),
        trialDeadline: data.trial_deadline ?? null,
        // Decimals are stringified by the backend (JSON has no Decimal type).
        // parseDecimalField normalises both null and non-finite strings to
        // null so the banner never renders "NaN" or "0" on a malformed value.
        totalGrantAmount: parseDecimalField(data.total_grant_amount),
        remainingGrantAmount: parseDecimalField(data.remaining_grant_amount),
        seatPriceUsd: parseDecimalField(data.seat_price_usd),
        userCount: data.user_count ?? 0,
      });
    } catch (error) {
      // Swallow: transient API hiccups must not flash the banner. Keep last-known-healthy state.
      log.warn('Failed to fetch billing status, keeping last-known state:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Belt-and-suspenders with the auth boundary in App.js — once a session
    // expires, isAuthenticated flips before the unmount completes, and we
    // don't want a queued tick to fire a guaranteed-401 between those events.
    if (!isAuthenticated) {
      return undefined;
    }

    fetchBillingStatus();
    const intervalId = setInterval(fetchBillingStatus, POLL_INTERVAL_MS);
    window.addEventListener('focus', fetchBillingStatus);

    return () => {
      clearInterval(intervalId);
      window.removeEventListener('focus', fetchBillingStatus);
    };
  }, [isAuthenticated, fetchBillingStatus]);

  const value = {
    paymentFailedAt: status.paymentFailedAt,
    graceDeadline: status.graceDeadline,
    servicePaused: status.servicePaused,
    isTrial: status.isTrial,
    trialDeadline: status.trialDeadline,
    totalGrantAmount: status.totalGrantAmount,
    remainingGrantAmount: status.remainingGrantAmount,
    seatPriceUsd: status.seatPriceUsd,
    userCount: status.userCount,
    loading,
    // Exposed for trial-exit actions (upgrade-now / cancel-trial) so the
    // banner can pull fresh state immediately after success rather than
    // waiting for the next 60s polling tick.
    refetch: fetchBillingStatus,
  };

  return <BillingStatusContext.Provider value={value}>{children}</BillingStatusContext.Provider>;
};

export default BillingStatusContext;
