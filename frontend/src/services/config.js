/**
 * Configuration service for fetching app config from backend
 */

import { getApiBaseUrl } from './baseUrl';

import { log } from '../utils/log';

const API_BASE_URL = getApiBaseUrl();
const API_KEY = process.env.REACT_APP_API_KEY; // Optional API key

class ConfigService {
  constructor() {
    this.config = null;
    this.loading = false;
    this.error = null;
  }

  async fetchConfig() {
    if (this.config) {
      return this.config;
    }

    if (this.loading) {
      // Wait for existing request to complete
      while (this.loading) {
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      return this.config;
    }

    this.loading = true;
    this.error = null;

    try {
      const headers = {
        'Content-Type': 'application/json',
      };

      // Add API key if configured
      if (API_KEY) {
        headers['Authorization'] = `Bearer ${API_KEY}`;
      }

      const response = await fetch(`${API_BASE_URL.replace(/\/$/, '')}/api/v1/config/public`, {
        headers
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch config: ${response.status} ${response.statusText}`);
      }

      const result = await response.json();

      if (result.data) {
        this.config = result.data;
        return this.config;
      } else {
        throw new Error('Invalid config response format - missing data field');
      }
    } catch (error) {
      this.error = error.message;
      log.error('Failed to fetch app config:', error);
      throw error;
    } finally {
      this.loading = false;
    }
  }

  getGoogleClientId() {
    return this.config?.google_client_id;
  }

  isGoogleSsoEnabled() {
    const clientId = this.getGoogleClientId();
    return typeof clientId === 'string' && clientId.trim().length > 0;
  }

  getAppName() {
    // Fallback to backend default to avoid stale hardcoding
    return this.config?.app_name || 'Shu';
  }

  getVersion() {
    return this.config?.version || '1.0.0';
  }

  getEnvironment() {
    return this.config?.environment || 'development';
  }

  isLoaded() {
    return this.config !== null;
  }

  hasError() {
    return this.error !== null;
  }

  getError() {
    return this.error;
  }

  // Clear cache (useful for testing or when config changes)
  clearCache() {
    this.config = null;
    this.error = null;
  }
}

// Export singleton instance
export const configService = new ConfigService();
export default configService;
