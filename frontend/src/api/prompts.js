/**
 * API client for the generalized prompt system.
 * 
 * This module provides functions for interacting with the new unified
 * prompt management API that supports multiple entity types.
 */

import api from '../services/api';

/**
 * Entity types supported by the prompt system
 */
export const ENTITY_TYPES = {
  KNOWLEDGE_BASE: 'knowledge_base',  // For KB context prompts (assigned via model configs)
  LLM_MODEL: 'llm_model',
  AGENT: 'agent',
  WORKFLOW: 'workflow',
  TOOL: 'tool'
};

/**
 * Prompt API client
 */
export const promptAPI = {
  /**
   * Create a new prompt
   * @param {Object} promptData - Prompt creation data
   * @param {string} promptData.name - Prompt name
   * @param {string} promptData.description - Prompt description (optional)
   * @param {string} promptData.content - Prompt content/template
   * @param {string} promptData.entity_type - Entity type (knowledge_base, llm_model, etc.)
   * @param {boolean} promptData.is_active - Whether the prompt is active
   * @returns {Promise<Object>} Created prompt
   */
  async create(promptData) {
    const response = await api.post('/prompts', promptData);
    return response.data;
  },

  /**
   * Get all prompts with filtering and pagination
   * @param {Object} params - Query parameters
   * @param {string} params.entity_type - Filter by entity type (optional)
   * @param {string} params.entity_id - Filter by entity ID (optional)
   * @param {boolean} params.is_active - Filter by active status (optional)
   * @param {string} params.search - Search in name and description (optional)
   * @param {number} params.limit - Maximum number of results (default: 50)
   * @param {number} params.offset - Number of results to skip (default: 0)
   * @returns {Promise<Object>} List of prompts with metadata
   */
  async list(params = {}) {
    const response = await api.get('/prompts', { params });
    return response.data;
  },

  /**
   * Get a specific prompt by ID
   * @param {string} promptId - Prompt ID
   * @returns {Promise<Object>} Prompt data
   */
  async get(promptId) {
    const response = await api.get(`/prompts/${promptId}`);
    return response.data;
  },

  /**
   * Update an existing prompt
   * @param {string} promptId - Prompt ID
   * @param {Object} updateData - Update data
   * @param {string} updateData.name - Prompt name (optional)
   * @param {string} updateData.description - Prompt description (optional)
   * @param {string} updateData.content - Prompt content (optional)
   * @param {boolean} updateData.is_active - Active status (optional)
   * @returns {Promise<Object>} Updated prompt
   */
  async update(promptId, updateData) {
    const response = await api.put(`/prompts/${promptId}`, updateData);
    return response.data;
  },

  /**
   * Delete a prompt and all its assignments
   * @param {string} promptId - Prompt ID
   * @returns {Promise<void>}
   */
  async delete(promptId) {
    await api.delete(`/prompts/${promptId}`);
  },

  /**
   * Assign a prompt to an entity
   * @param {string} promptId - Prompt ID
   * @param {Object} assignmentData - Assignment data
   * @param {string} assignmentData.entity_id - Entity ID to assign to
   * @param {boolean} assignmentData.is_active - Whether assignment is active
   * @returns {Promise<Object>} Assignment data
   */
  async assign(promptId, assignmentData) {
    const response = await api.post(`/prompts/${promptId}/assignments`, assignmentData);
    return response.data;
  },

  /**
   * Unassign a prompt from an entity
   * @param {string} promptId - Prompt ID
   * @param {string} entityId - Entity ID to unassign from
   * @returns {Promise<void>}
   */
  async unassign(promptId, entityId) {
    await api.delete(`/prompts/${promptId}/assignments/${entityId}`);
  },

  /**
   * Get all prompts assigned to a specific entity
   * @param {string} entityId - Entity ID
   * @param {string} entityType - Entity type
   * @param {boolean} activeOnly - Return only active prompts (default: true)
   * @returns {Promise<Array>} List of assigned prompts
   */
  async getEntityPrompts(entityId, entityType, activeOnly = true) {
    const params = { entity_type: entityType, active_only: activeOnly };
    const response = await api.get(`/prompts/entities/${entityId}`, { params });
    return response.data;
  },

  /**
   * Get system-wide prompt statistics
   * @returns {Promise<Object>} System statistics
   */
  async getStats() {
    const response = await api.get('/prompts/stats');
    return response.data;
  }
};

/**
 * Knowledge Base specific prompt utilities
 * These provide convenience methods for working with knowledge base prompts
 */
/**
 * Knowledge Base specific prompt utilities
 * These provide convenience methods for working with knowledge base context prompts
 * Note: KB prompts are now assigned via model configurations, not directly to KBs
 */
export const knowledgeBasePromptAPI = {
  /**
   * Create a knowledge base context prompt
   * @param {Object} promptData - Prompt data (entity_type will be set automatically)
   * @returns {Promise<Object>} Created prompt
   */
  async create(promptData) {
    return promptAPI.create({
      ...promptData,
      entity_type: ENTITY_TYPES.KNOWLEDGE_BASE
    });
  },

  /**
   * Get all knowledge base context prompts
   * @param {Object} params - Query parameters
   * @returns {Promise<Object>} List of knowledge base prompts
   */
  async list(params = {}) {
    return promptAPI.list({
      ...params,
      entity_type: ENTITY_TYPES.KNOWLEDGE_BASE
    });
  },

  // NOTE: KB prompt assignments are now managed at the model configuration level
  // Use modelConfigAPI.assignKBPrompt() and modelConfigAPI.removeKBPrompt() instead
};

/**
 * LLM Model specific prompt utilities (for future use)
 * These provide convenience methods for working with LLM model prompts
 */
export const llmModelPromptAPI = {
  /**
   * Create an LLM model prompt
   * @param {Object} promptData - Prompt data (entity_type will be set automatically)
   * @returns {Promise<Object>} Created prompt
   */
  async create(promptData) {
    return promptAPI.create({
      ...promptData,
      entity_type: ENTITY_TYPES.LLM_MODEL
    });
  },

  /**
   * Get all LLM model prompts
   * @param {Object} params - Query parameters
   * @returns {Promise<Object>} List of LLM model prompts
   */
  async list(params = {}) {
    return promptAPI.list({
      ...params,
      entity_type: ENTITY_TYPES.LLM_MODEL
    });
  },

  /**
   * Get prompts for a specific LLM model
   * @param {string} modelId - LLM model ID
   * @param {boolean} activeOnly - Return only active prompts
   * @returns {Promise<Array>} List of assigned prompts
   */
  async getForModel(modelId, activeOnly = true) {
    return promptAPI.getEntityPrompts(modelId, ENTITY_TYPES.LLM_MODEL, activeOnly);
  },

  /**
   * Assign a prompt to an LLM model
   * @param {string} promptId - Prompt ID
   * @param {string} modelId - LLM model ID
   * @param {boolean} isActive - Whether assignment is active
   * @returns {Promise<Object>} Assignment data
   */
  async assignToModel(promptId, modelId, isActive = true) {
    return promptAPI.assign(promptId, {
      entity_id: modelId,
      is_active: isActive
    });
  },

  /**
   * Unassign a prompt from an LLM model
   * @param {string} promptId - Prompt ID
   * @param {string} modelId - LLM model ID
   * @returns {Promise<void>}
   */
  async unassignFromModel(promptId, modelId) {
    return promptAPI.unassign(promptId, modelId);
  }
};

export default promptAPI;
