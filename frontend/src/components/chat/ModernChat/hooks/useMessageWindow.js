import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { CHAT_WINDOW_SIZE, CHAT_OVERSCAN } from '../utils/chatConfig';

const clamp = (value, min, max) => Math.max(min, Math.min(value, max));

const useMessageWindow = (
  messages,
  {
    windowSize = CHAT_WINDOW_SIZE,
    overscan = CHAT_OVERSCAN,
    pinned = true,
  } = {}
) => {
  const total = Array.isArray(messages) ? messages.length : 0;
  const [startIndex, setStartIndex] = useState(() => Math.max(total - windowSize, 0));
  const prevLengthRef = useRef(total);

  useEffect(() => {
    const length = Array.isArray(messages) ? messages.length : 0;
    if (pinned) {
      const start = Math.max(length - windowSize, 0);
      setStartIndex(start);
    } else if (length !== prevLengthRef.current) {
      setStartIndex((prev) => {
        const adjusted = clamp(prev, 0, Math.max(length - 1, 0));
        return Math.max(adjusted, 0);
      });
    }
    prevLengthRef.current = length;
  }, [messages, pinned, windowSize]);

  const expandWindow = useCallback((count) => {
    if (!count) return;
    setStartIndex((prev) => Math.max(prev - count, 0));
  }, []);

  const advanceWindow = useCallback((count) => {
    if (!count) return;
    const length = Array.isArray(messages) ? messages.length : 0;
    setStartIndex((prev) => clamp(prev + count, 0, Math.max(length - 1, 0)));
  }, [messages]);


  const visibleRange = useMemo(() => {
    const length = Array.isArray(messages) ? messages.length : 0;
    const start = clamp(startIndex, 0, Math.max(length - 1, 0));
    const end = clamp(start + windowSize + overscan, 0, length);
    const overscannedStart = clamp(start - overscan, 0, length);
    return { overscannedStart, end };
  }, [messages, overscan, startIndex, windowSize]);

  const visibleMessages = useMemo(() => {
    if (!Array.isArray(messages)) return [];
    return messages.slice(visibleRange.overscannedStart, visibleRange.end);
  }, [messages, visibleRange.end, visibleRange.overscannedStart]);

  return {
    visibleMessages,
    expandWindow,
    advanceWindow,
    visibleOffset: visibleRange.overscannedStart,
    windowStart: startIndex,
  };
};

export default useMessageWindow;
