// This file runs BEFORE test files are loaded to set up Jest compatibility
import { vi } from 'vitest';
import { TextEncoder, TextDecoder } from 'util';

// Polyfill TextEncoder/TextDecoder
global.TextEncoder = TextEncoder;
global.TextDecoder = TextDecoder;

// Make vi available globally
global.vi = vi;

// Jest compatibility: just alias jest to vi
// This makes jest.fn(), jest.mock(), etc. work
global.jest = vi;
