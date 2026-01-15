import { generateSafeFilename } from '../downloadHelpers';

describe('downloadHelpers', () => {
    describe('generateSafeFilename', () => {
        it('converts name to safe filename', () => {
            expect(generateSafeFilename('My Experience!')).toBe('my-experience');
            expect(generateSafeFilename('Test@#$%Name')).toBe('test-name');
            expect(generateSafeFilename('  Multiple   Spaces  ')).toBe('multiple-spaces');
        });

        it('handles empty or invalid names', () => {
            expect(generateSafeFilename('')).toBe('file');
            expect(generateSafeFilename(null)).toBe('file');
            expect(generateSafeFilename(undefined)).toBe('file');
        });

        it('uses custom fallback', () => {
            expect(generateSafeFilename('', 'custom')).toBe('custom');
        });

        it('adds extension when provided', () => {
            expect(generateSafeFilename('test', 'file', 'yaml')).toBe('test.yaml');
            expect(generateSafeFilename('', 'file', 'txt')).toBe('file.txt');
        });

        it('removes leading and trailing hyphens', () => {
            expect(generateSafeFilename('---test---')).toBe('test');
            expect(generateSafeFilename('!@#test!@#')).toBe('test');
        });

        it('handles complex experience names', () => {
            expect(generateSafeFilename('Morning Briefing v2.1')).toBe('morning-briefing-v2-1');
            expect(generateSafeFilename('User Authentication & Authorization')).toBe('user-authentication-authorization');
            expect(generateSafeFilename('API Integration (OAuth2)')).toBe('api-integration-oauth2');
        });
    });
});