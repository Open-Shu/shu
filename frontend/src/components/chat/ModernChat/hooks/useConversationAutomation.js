import { useEffect, useRef } from 'react';

const useConversationAutomation = ({
  selectedConversation,
  assistantReplyCount,
  lastAssistantMessageId,
  automationSettings,
  messagesMatchSelectedConversation,
  summaryIsLoading,
  autoRenameIsLoading,
  runSummaryAsync,
  runAutoRenameAsync,
  buildRenamePayload,
  isConversationFresh,
}) => {
  // Track cadence executions to avoid double-triggering for the same assistant count
  const lastProcessedAssistantCountRef = useRef(new Map()); // convoId -> count

  // Cadence: after first assistant reply -> summary (if enabled);
  // after second and then every N thereafter -> summary then rename (if enabled)
  useEffect(() => {
    const convoId = selectedConversation?.id;
    if (!convoId) return;
    if (!assistantReplyCount) return;

    const prev = lastProcessedAssistantCountRef.current.get(convoId) || 0;
    if (assistantReplyCount <= prev) return;
    if (!messagesMatchSelectedConversation) return;

    const isFreshConversation = isConversationFresh(convoId);
    const isTitleLocked = Boolean(selectedConversation?.meta?.title_locked);
    const shouldRunSummaryOnly =
      automationSettings.firstAssistantSummary && assistantReplyCount === 1;
    const interval = Number(automationSettings.cadenceInterval) || 0;
    const hitsInterval =
      interval > 0 &&
      assistantReplyCount >= interval &&
      assistantReplyCount % interval === 0;
    const shouldRunBoth = hitsInterval;

    const run = async () => {
      let summaryRan = false;
      try {
        if (shouldRunSummaryOnly || shouldRunBoth) {
          if (!summaryIsLoading && lastAssistantMessageId) {
            await runSummaryAsync({
              conversationId: convoId,
              payload: { last_message_id: lastAssistantMessageId },
            });
            summaryRan = true;
          }
        }

        const shouldRenameAfterSummary =
          summaryRan && !isTitleLocked && lastAssistantMessageId;
        const shouldRenameCadence =
          shouldRunBoth && !isTitleLocked && lastAssistantMessageId;
        const allowRename = !isFreshConversation || summaryRan;

        if (
          (shouldRenameAfterSummary || shouldRenameCadence) &&
          allowRename &&
          !autoRenameIsLoading
        ) {
          await runAutoRenameAsync({
            conversationId: convoId,
            payload: {
              ...buildRenamePayload(),
              last_message_id: lastAssistantMessageId,
            },
          });
        }
      } catch (_) {
        // errors handled inside mutations
      } finally {
        // Mark this assistant reply count as processed even when side-calls
        // fail, so we don't spam retries for the same message.
        lastProcessedAssistantCountRef.current.set(convoId, assistantReplyCount);
      }
    };

    run();
  }, [
    assistantReplyCount,
    selectedConversation?.id,
    selectedConversation?.meta?.title_locked,
    lastAssistantMessageId,
    automationSettings,
    messagesMatchSelectedConversation,
    summaryIsLoading,
    autoRenameIsLoading,
    runSummaryAsync,
    runAutoRenameAsync,
    buildRenamePayload,
    isConversationFresh,
  ]);
};

export default useConversationAutomation;

