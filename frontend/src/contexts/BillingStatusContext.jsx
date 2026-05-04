import React, { createContext, useContext, useEffect, useState, useCallback, useRef } from 'react';
import { billingAPI, extractDataFromResponse } from '../services/api';
import log from '../utils/log';

const POLL_INTERVAL_MS = 60_000;

const HEALTHY_STATE = {
  paymentFailedAt: null,
  graceDeadline: null,
  servicePaused: false,
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
  const [status, setStatus] = useState(HEALTHY_STATE);
  const [loading, setLoading] = useState(true);

  // Ref'd so the interval/focus handlers always call the latest callback without
  // re-binding the interval each render.
  const fetchRef = useRef(null);

  const fetchBillingStatus = useCallback(async () => {
    try {
      const response = await billingAPI.getSubscription();
      const data = extractDataFromResponse(response) || {};
      setStatus({
        paymentFailedAt: data.payment_failed_at ?? null,
        graceDeadline: data.grace_deadline ?? null,
        servicePaused: Boolean(data.service_paused),
      });
    } catch (error) {
      // Swallow: transient API hiccups must not flash the banner. Keep last-known-healthy state.
      log.warn('Failed to fetch billing status, keeping last-known state:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRef.current = fetchBillingStatus;
  }, [fetchBillingStatus]);

  useEffect(() => {
    const tick = () => {
      if (fetchRef.current) {
        fetchRef.current();
      }
    };

    tick();
    const intervalId = setInterval(tick, POLL_INTERVAL_MS);
    window.addEventListener('focus', tick);

    return () => {
      clearInterval(intervalId);
      window.removeEventListener('focus', tick);
    };
  }, []);

  const value = {
    paymentFailedAt: status.paymentFailedAt,
    graceDeadline: status.graceDeadline,
    servicePaused: status.servicePaused,
    loading,
  };

  return <BillingStatusContext.Provider value={value}>{children}</BillingStatusContext.Provider>;
};

export default BillingStatusContext;
