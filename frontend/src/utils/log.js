/* Simple logging utility gated by environment
 * - DEBUG mode if REACT_APP_DEBUG==='true' or NODE_ENV!=='production'
 * - error() always logs
 */
const DEBUG = process.env.REACT_APP_DEBUG === 'true' || process.env.NODE_ENV !== 'production';

export const log = {
  debug: (...args) => {
    if (DEBUG) {
      console.log(...args);
    }
  },
  info: (...args) => {
    if (DEBUG) {
      console.info(...args);
    }
  },
  warn: (...args) => {
    if (DEBUG) {
      console.warn(...args);
    }
  },
  error: (...args) => {
    console.error(...args);
  },
};

export default log;
