/**
 * Morning Briefing YAML template for Quick Start integration
 * This template includes placeholders that will be resolved by the import wizard
 */

export const MORNING_BRIEFING_YAML = `# Experience Export: Morning Briefing
# Generated on: 2026-01-08 16:48:12
# 
# This YAML file contains placeholders for user-specific values:
# - {{ trigger_type }}: How the experience will be triggered (Cron, Scheduled, Manual)
# - {{ trigger_config }}: The actual trigger value, depending on the schedule type
# - {{ llm_provider_id }}: Choose your LLM provider
# - {{ model_name }}: Choose your model
# - {{ max_run_seconds }}: The total amount of time the experience is allowed to run
#
# To import this experience, use the Experience Import wizard in Shu.

experience_yaml_version: 1
id: morning-briefing-v1
name: Morning Briefing
description: Daily summary of Google emails, calendar, and chats
version: 1
visibility: draft
trigger_type: '{{ trigger_type }}'
trigger_config: {{ trigger_config }}
include_previous_run: false
llm_provider_id: '{{ llm_provider_id }}'
model_name: '{{ model_name }}'
inline_prompt_template: "Synthesize a morning briefing for {{ user.display_name }} based on the \`gmail_digest\`, \`calendar_events\`, and \`gchat_digest\` data.\\n\\n  ## Instructions\\n  - Review all emails, calendar events, and chat messages\\n  - Highlight important action items and urgent matters first\\n  - Group by category (email priorities, meetings, chat highlights)\\n  - Flag likely spam/bulk emails under a separate \\"Likely Spam\\" section with brief reasons\\n  - Keep it concise but comprehensive\\n\\n  Please synthesize this information into a clear, actionable morning briefing."
max_run_seconds: {{ max_run_seconds }}
steps:
- step_key: gmail_digest
  step_type: plugin
  order: 0
  plugin_name: gmail_digest
  plugin_op: list
  params_template:
    max_results: '50'
    since_hours: '48'
- step_key: calendar_events
  step_type: plugin
  order: 1
  plugin_name: calendar_events
  plugin_op: list
  params_template:
    max_results: '50'
    since_hours: '48'
- step_key: gchat_digest
  step_type: plugin
  order: 2
  plugin_name: gchat_digest
  plugin_op: list
  params_template:
    since_hours: '48'
    max_messages_per_space: '10'`;