/**
 * Utility functions for handling file downloads in the browser
 */

/**
 * Downloads blob data as a file by creating a temporary download link
 * 
 * @param {Blob} blob - The blob data to download
 * @param {string} filename - The filename for the downloaded file
 * @param {string} [mimeType] - Optional MIME type for the blob (defaults to blob's type)
 * @throws {Error} If blob is invalid or download fails
 */
export const downloadBlobAsFile = (blob, filename, mimeType = null) => {
    if (!blob || !(blob instanceof Blob)) {
        throw new Error('Invalid blob data provided');
    }
    
    if (!filename || typeof filename !== 'string') {
        throw new Error('Valid filename is required');
    }

    try {
        // Create blob with specified MIME type if provided
        const downloadBlob = mimeType ? new Blob([blob], { type: mimeType }) : blob;
        
        // Create object URL for the blob
        const url = window.URL.createObjectURL(downloadBlob);
        
        // Create temporary download link
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.style.display = 'none'; // Hide the link
        
        // Add to DOM, trigger download, then cleanup
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        
        // Cleanup object URL to free memory
        window.URL.revokeObjectURL(url);
        
    } catch (error) {
        throw new Error(`Failed to download file: ${error.message}`);
    }
};

/**
 * Generates a safe filename from a string by removing/replacing invalid characters
 * 
 * @param {string} name - The original name to make safe
 * @param {string} [fallback='file'] - Fallback name if input is empty/invalid
 * @param {string} [extension=''] - Optional file extension to append
 * @returns {string} Safe filename
 */
export const generateSafeFilename = (name, fallback = 'file', extension = '') => {
    if (!name || typeof name !== 'string') {
        return extension ? `${fallback}.${extension}` : fallback;
    }
    
    // Convert to lowercase and replace invalid characters with hyphens
    const safeName = name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')  // Replace non-alphanumeric with hyphens
        .replace(/^-+|-+$/g, '');     // Remove leading/trailing hyphens
    
    // Use fallback if result is empty
    const finalName = safeName || fallback;
    
    return extension ? `${finalName}.${extension}` : finalName;
};

/**
 * Downloads response data as a file, handling both blob and text responses
 * 
 * @param {Object} response - Axios response object
 * @param {string} filename - The filename for the downloaded file
 * @param {string} [defaultMimeType='application/octet-stream'] - Default MIME type if not specified
 * @throws {Error} If response is invalid or download fails
 */
export const downloadResponseAsFile = (response, filename, defaultMimeType = 'application/octet-stream') => {
    if (!response || !response.data) {
        throw new Error('Invalid response data');
    }
    
    let blob;
    
    // Handle different response data types
    if (response.data instanceof Blob) {
        blob = response.data;
    } else if (typeof response.data === 'string') {
        // Convert string to blob
        const mimeType = response.headers?.['content-type'] || defaultMimeType;
        blob = new Blob([response.data], { type: mimeType });
    } else {
        // Convert other data types to JSON blob
        const jsonString = JSON.stringify(response.data, null, 2);
        blob = new Blob([jsonString], { type: 'application/json' });
    }
    
    downloadBlobAsFile(blob, filename);
};