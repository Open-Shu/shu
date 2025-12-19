import { useState, useMemo, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from 'react-query';

import {
  chatAPI,
  extractDataFromResponse,
  formatError,
  modelConfigAPI,
} from '../../../../services/api';
import { CONVERSATION_LIST_LIMIT } from '../utils/chatConfig';

const buildConversationListParams = (summaryQuery) => (
  summaryQuery ? { limit: CONVERSATION_LIST_LIMIT, summary_query: summaryQuery } : { limit: CONVERSATION_LIST_LIMIT }
);

const normalizeModelConfigs = (raw) => {
  if (!raw) {
    return [];
  }
  if (Array.isArray(raw)) {
    return raw;
  }
  if (Array.isArray(raw.items)) {
    return raw.items;
  }
  return [];
};

const filterAvailableModelConfigs = (configs) => configs.filter((config) => !config.is_side_call && !config.is_ocr_call);

const useConversationData = ({
  summaryQuery,
  onQueryError,
  onMutationError,
  markFreshConversation,
}) => {
  const queryClient = useQueryClient();
  const [lastCreatedConversation, setLastCreatedConversation] = useState(null);

  const conversationListParams = useMemo(
    () => buildConversationListParams(summaryQuery),
    [summaryQuery]
  );

  const conversationQueryKey = useMemo(
    () => ['conversations', summaryQuery || ''],
    [summaryQuery]
  );

  const { data: conversationsResponse, isLoading: loadingConversations } = useQuery(
    conversationQueryKey,
    () => chatAPI.listConversations(conversationListParams),
    {
      onError: (err) => {
        if (onQueryError) {
          onQueryError(formatError(err).message);
        }
      },
    }
  );

  const conversations = useMemo(() => {
    const arr = extractDataFromResponse(conversationsResponse) || [];
    return Array.isArray(arr) ? arr.filter((c) => c?.is_active !== false) : [];
  }, [conversationsResponse]);

  const {
    data: modelConfigsResponse,
    isLoading: loadingConfigs,
  } = useQuery(
    'model-configurations',
    () => modelConfigAPI.list({ is_active: true }),
    {
      onError: (err) => {
        if (onQueryError) {
          onQueryError(formatError(err).message);
        }
      },
    }
  );

  const modelConfigs = useMemo(
    () => normalizeModelConfigs(extractDataFromResponse(modelConfigsResponse)),
    [modelConfigsResponse]
  );

  const availableModelConfigs = useMemo(
    () => filterAvailableModelConfigs(modelConfigs),
    [modelConfigs]
  );

  const createConversationMutation = useMutation(
    (data) => chatAPI.createConversationWithModelConfig(data),
    {
      onSuccess: (response) => {
        const newConversation = extractDataFromResponse(response);
        if (newConversation && newConversation.id) {
          markFreshConversation?.(newConversation.id);
        }
        setLastCreatedConversation(newConversation);
        queryClient.invalidateQueries('conversations');
      },
      onError: (err) => {
        if (onMutationError) {
          onMutationError(formatError(err).message);
        }
      },
    }
  );

  const resetLastCreatedConversation = useCallback(() => {
    setLastCreatedConversation(null);
  }, []);

  return {
    conversations,
    conversationQueryKey,
    loadingConversations,
    modelConfigs,
    availableModelConfigs,
    loadingConfigs,
    createConversationMutation,
    lastCreatedConversation,
    resetLastCreatedConversation,
  };
};

export default useConversationData;
