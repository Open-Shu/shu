/**
 * Streaming error handling utilities for chat SSE streams.
 *
 * These utilities provide structured error handling for fetch-based streaming
 * that bypasses Axios interceptors, enabling proper handling of JWT expiration,
 * rate limiting, and other HTTP errors.
 *
 * Error Hierarchy:
 * - StreamingError: HTTP-level errors (401, 429, 500, etc.) from the initial response
 * - ServerStreamingError: Errors sent by the server via SSE error events during streaming
 * - TypeError (with fetch/network message): Network connectivity errors
 * - Error: Generic/unknown errors
 */

/**
 * Error class for HTTP-level streaming errors with structured metadata.
 * Used when the initial fetch response indicates an error (non-2xx status).
 */
export class StreamingError extends Error {
  constructor(message, { status = null, retryable = false, retryAfter = null, userMessage = null } = {}) {
    super(message);
    this.name = 'StreamingError';
    this.status = status;
    this.retryable = retryable;
    this.retryAfter = retryAfter;
    this.userMessage = userMessage || message;
  }
}

/**
 * Error class for server-sent streaming errors.
 * Used when the server sends an error event during SSE streaming.
 * These errors contain user-friendly messages from the backend.
 */
export class ServerStreamingError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ServerStreamingError';
    this.userMessage = message;
  }
}

/**
 * HTTP status codes and their user-friendly error mappings.
 */
const ERROR_MESSAGES = {
  401: {
    // User only sees this if auto-refresh failed - they need to re-authenticate
    userMessage: 'Your session has expired. Please sign in again.',
    retryable: false,
  },
  403: {
    userMessage: 'You do not have permission to perform this action.',
    retryable: false,
  },
  404: {
    userMessage: 'The conversation could not be found.',
    retryable: false,
  },
  408: {
    userMessage: 'The request timed out. Please try again.',
    retryable: true,
  },
  429: {
    userMessage: 'Too many requests. Please wait before trying again.',
    retryable: true,
  },
  500: {
    userMessage: 'An unexpected server error occurred. Please try again later.',
    retryable: true,
  },
  502: {
    userMessage: 'The service is temporarily unavailable. Please try again.',
    retryable: true,
  },
  503: {
    userMessage: 'The service is temporarily unavailable. Please try again.',
    retryable: true,
  },
  504: {
    userMessage: 'The request timed out. Please try again.',
    retryable: true,
  },
};

/**
 * Parse error details from a fetch Response object.
 * Attempts to extract structured error info from the response body.
 */
async function parseResponseError(response) {
  const retryAfterHeader = response.headers.get('Retry-After');
  const retryAfter = retryAfterHeader ? parseInt(retryAfterHeader, 10) : null;

  let bodyError = null;
  try {
    const contentType = response.headers.get('Content-Type') || '';
    if (contentType.includes('application/json')) {
      const json = await response.json();
      // Handle envelope format: { error: { message: "...", code: "..." } }
      if (json?.error?.message) {
        bodyError = typeof json.error.message === 'string' ? json.error.message : JSON.stringify(json.error.message);
      } else if (json?.detail) {
        // FastAPI HTTPException format
        bodyError = typeof json.detail === 'string' ? json.detail : JSON.stringify(json.detail);
      } else if (json?.message) {
        bodyError = json.message;
      }
    }
  } catch {
    // Body parsing failed, continue with status-based message
  }

  return { bodyError, retryAfter };
}

/**
 * Create a StreamingError from a fetch Response object.
 */
export async function createStreamingErrorFromResponse(response) {
  const { bodyError, retryAfter } = await parseResponseError(response);
  const status = response.status;
  const statusInfo = ERROR_MESSAGES[status] || {
    userMessage: `Request failed (${status})`,
    retryable: status >= 500,
  };

  let userMessage = statusInfo.userMessage;

  // For 429, include retry-after info if available
  if (status === 429 && retryAfter) {
    userMessage = `Too many requests. Please try again in ${retryAfter} seconds.`;
  }

  // If we got a specific error message from the body, use it
  if (bodyError) {
    userMessage = bodyError;
  }

  return new StreamingError(`HTTP ${status}: ${response.statusText}`, {
    status,
    retryable: statusInfo.retryable,
    retryAfter,
    userMessage,
  });
}

/**
 * Format any error into a user-friendly message for display.
 * Handles all error types in the streaming error hierarchy.
 *
 * @param {Error} error - The error to format
 * @returns {{message: string, retryable: boolean, retryAfter: number|null, status: number|null, isNetworkError: boolean, isServerError: boolean}}
 */
export function formatStreamingError(error) {
  // HTTP-level streaming error (non-2xx response)
  if (error instanceof StreamingError) {
    return {
      message: error.userMessage,
      retryable: error.retryable,
      retryAfter: error.retryAfter,
      status: error.status,
      isNetworkError: false,
      isServerError: false,
    };
  }

  // Server-sent error via SSE (backend sent an error event)
  if (error instanceof ServerStreamingError) {
    return {
      message: error.userMessage,
      retryable: false,
      retryAfter: null,
      status: null,
      isNetworkError: false,
      isServerError: true,
    };
  }

  // Network/fetch errors - catch various network failure patterns
  if (error?.name === 'TypeError') {
    const msg = error?.message?.toLowerCase() || '';
    // Common network error messages: "Failed to fetch", "NetworkError", "Network request failed", etc.
    if (msg.includes('fetch') || msg.includes('network') || msg.includes('connection')) {
      return {
        message: 'Network error. Please check your connection and try again.',
        retryable: true,
        retryAfter: null,
        status: null,
        isNetworkError: true,
        isServerError: false,
      };
    }
  }

  // AbortError (user cancelled or timeout)
  if (error?.name === 'AbortError') {
    return {
      message: 'Request was cancelled.',
      retryable: true,
      retryAfter: null,
      status: null,
      isNetworkError: false,
      isServerError: false,
    };
  }

  // Standard Error object
  if (error instanceof Error) {
    return {
      message: error.message || 'An unexpected error occurred.',
      retryable: false,
      retryAfter: null,
      status: null,
      isNetworkError: false,
      isServerError: false,
    };
  }

  // Unknown error type
  return {
    message: String(error) || 'An unexpected error occurred.',
    retryable: false,
    retryAfter: null,
    status: null,
    isNetworkError: false,
    isServerError: false,
  };
}
