/* Simple logging utility gated by environment
 * - DEBUG mode if VITE_DEBUG==='true' or MODE!=='production'
 * - error() always logs
 */
const DEBUG = import.meta.env.VITE_DEBUG === 'true' || import.meta.env.MODE !== 'production';

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
