import { useState, useEffect, useMemo, useCallback } from "react";

const DEFAULT_MIN_TERM_LENGTH = 3;
const DEFAULT_MAX_TOKENS = 10;
const DEFAULT_DEBOUNCE_MS = 300;

export default function useSummarySearch({
  minTokenLength = DEFAULT_MIN_TERM_LENGTH,
  maxTokens = DEFAULT_MAX_TOKENS,
  debounceMs = DEFAULT_DEBOUNCE_MS,
} = {}) {
  const [searchInput, setSearchInput] = useState("");
  const [tokens, setTokens] = useState([]);
  const [feedback, setFeedback] = useState(null);

  const effectiveMinTokenLength = useMemo(
    () => Math.max(minTokenLength ?? DEFAULT_MIN_TERM_LENGTH, 1),
    [minTokenLength],
  );

  const effectiveMaxTokens = useMemo(
    () => Math.max(maxTokens ?? DEFAULT_MAX_TOKENS, 1),
    [maxTokens],
  );

  const normalizeTokens = useCallback(
    (rawValue) => {
      if (!rawValue || typeof rawValue !== "string") {
        return { tokens: [], feedback: null };
      }

      const trimmed = rawValue.trim();
      if (!trimmed) {
        return { tokens: [], feedback: null };
      }

      const nextTokens = [];
      let ignoredShort = 0;
      let ignoredOverflow = 0;

      trimmed.split(/\s+/).forEach((segment) => {
        const token = segment.toLowerCase();
        if (token.length < effectiveMinTokenLength) {
          ignoredShort += 1;
          return;
        }
        if (nextTokens.length >= effectiveMaxTokens) {
          ignoredOverflow += 1;
          return;
        }
        nextTokens.push(token);
      });

      const notices = [];
      if (ignoredShort > 0) {
        notices.push(
          `${ignoredShort} short word${ignoredShort > 1 ? "s" : ""} ignored (minimum ${effectiveMinTokenLength} characters)`,
        );
      }
      if (ignoredOverflow > 0) {
        notices.push(
          `Only the first ${effectiveMaxTokens} keywords are applied`,
        );
      }
      if (!nextTokens.length) {
        notices.push(
          `Enter keywords with at least ${effectiveMinTokenLength} characters`,
        );
      }

      const nextFeedback = notices.length ? `${notices.join(". ")}.` : null;
      return { tokens: nextTokens, feedback: nextFeedback };
    },
    [effectiveMinTokenLength, effectiveMaxTokens],
  );

  useEffect(() => {
    const handle = setTimeout(() => {
      const { tokens: nextTokens, feedback: nextFeedback } =
        normalizeTokens(searchInput);
      setFeedback(nextFeedback);
      setTokens((prev) => {
        if (
          prev.length === nextTokens.length &&
          prev.every((value, index) => value === nextTokens[index])
        ) {
          return prev;
        }
        return nextTokens;
      });
    }, debounceMs);

    return () => clearTimeout(handle);
  }, [searchInput, normalizeTokens, debounceMs]);

  const summaryQuery = useMemo(
    () => (tokens.length ? tokens.join(" ") : null),
    [tokens],
  );

  return {
    searchInput,
    setSearchInput,
    summaryTokens: tokens,
    summaryFeedback: feedback,
    summaryQuery,
    effectiveMinTokenLength,
    effectiveMaxTokens,
  };
}
