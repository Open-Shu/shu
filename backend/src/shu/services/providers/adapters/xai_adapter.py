import copy
from typing import Any, Dict, List

from shu.models.plugin_execution import CallableTool
from shu.core.logging import get_logger

from ..adapter_base import ProviderCapabilities, ProviderInformation, register_adapter
from .responses_adapter import ResponsesAdapter
from ..parameter_definitions import ArrayParameter, BooleanParameter, EnumParameter, InputField, IntegerParameter, NumberParameter, ObjectParameter, Option, StringParameter

logger = get_logger(__name__)


class XAIAdapter(ResponsesAdapter):
    """Adapter for Grok (xAI) chat completions (OpenAI-compatible)."""

    def get_provider_information(self) -> ProviderInformation:
        return ProviderInformation(key="xai", display_name="xAI")

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tools=True, vision=False)

    def get_api_base_url(self) -> str:
        return "https://api.x.ai/v1"

    def get_authorization_header(self) -> Dict[str, Any]:
        return {
            "scheme": "bearer",
            "headers": {"Authorization": f"Bearer {self.api_key}"},
        }
    
    async def inject_tool_payload(self, tools: List[CallableTool], payload: Dict[str, Any]) -> Dict[str, Any]:
        res: List[Dict[str, Any]] = []
        for tool in tools:
            title = None
            if isinstance(tool.enum_labels, dict):
                title = tool.enum_labels.get(str(tool.op))
            fname = f"{tool.name}__{tool.op}"
            op_schema = copy.deepcopy(tool.schema) if tool.schema else {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }
            props = op_schema.setdefault("properties", {})
            props["op"] = {
                "type": "string",
                "enum": [tool.op],
                "const": tool.op,
                "default": tool.op,
            }
            if isinstance(op_schema.get("required"), list):
                if "op" not in op_schema["required"]:
                    op_schema["required"].append("op")
            else:
                op_schema["required"] = ["op"]
            description = title or f"Run {tool.name}:{tool.op}"
            tool_entry = {
                "type": "function",
                "name": fname,
                "description": description,
                "parameters": op_schema,
            }
            res.append(tool_entry)
        payload["tools"] = payload.get("tools", []) + res
        return payload

    def get_parameter_mapping(self) -> Dict[str, Any]:
        return {
            # --- Sampling / generation controls (chat.completions) ---
            "temperature": NumberParameter(
                min=0,
                max=2,
                default=0.7,
                label="Temperature",
                description=(
                    "Controls randomness in Grok-4's output. Lower values = more deterministic, "
                    "higher values = more creative. 0 is fully deterministic."
                ),
            ),
            "top_p": NumberParameter(
                min=0,
                max=1,
                default=1.0,
                label="Top P",
                description=(
                    "Nucleus sampling. Model considers only the smallest set of tokens whose "
                    "cumulative probability ≥ top_p. Set < 1.0 to truncate the tail of the distribution."
                ),
            ),
            "max_output_tokens": IntegerParameter(
                min=1,
                label="Max output tokens",
                description=(
                    "Maximum number of tokens Grok-4 can generate for this completion "
                    "(output tokens only)."
                ),
            ),
            "reasoning": ObjectParameter(
                label="Reasoning",
                description=(
                    "Reasoning behavior presets (used by reasoning-capable models). Configure summary verbosity and effort level."
                ),
                properties={
                    "summary": EnumParameter(
                        label="Reasoning summary level",
                        description="How detailed the reasoning summary should be.",
                        options=[
                            Option(value="concise", label="Concise"),
                            Option(value="detailed", label="Detailed"),
                        ],
                        default="concise",
                    ),
                    "effort": EnumParameter(
                        label="Reasoning effort",
                        description="How much effort the model should spend on reasoning.",
                        options=[
                            Option(value="low", label="Low"),
                            Option(value="medium", label="Medium"),
                            Option(value="high", label="High"),
                        ],
                        default="medium",
                    ),
                },
            ),

            # --- Function calling (client-side tools) ---

            "tools": ArrayParameter(
                label="Tools",
                description="Built-in tools to enable for this response.",
                options=[
                    Option(
                        value={"type": "web_search"},
                        label="Web Search",
                        help="Allow the model to call the built-in web_search tool.",
                        input_schema=ObjectParameter(
                            label="Web Search Configuration",
                            description="Optional configuration for the web_search tool.",
                            properties={
                                "allowed_domains": ArrayParameter(
                                    label="Allowed domains",
                                    description="List of website domains to allow in the search results. This parameter act as a whitelist where only those websites can be selected. A maximum of 5 websites can be selected.",
                                    items=StringParameter(
                                        label="Handle",
                                        placeholder="example.com",
                                    ),
                                ),
                                "enable_image_understanding": BooleanParameter(
                                    label="Image understanding",
                                    description=(
                                        "Enable image understanding during X search."
                                    ),
                                ),
                                "excluded_domains": ArrayParameter(
                                    label="Excluded domains",
                                    description="List of website domains to exclude from the search results without protocol specification or subdomains. A maximum of 5 websites can be excluded. Note: This parameter cannot be set with allowed_domains",
                                    items=StringParameter(
                                        label="Handle",
                                        placeholder="example.com",
                                    ),
                                ),
                                "external_web_access": BooleanParameter(
                                    label="External web access",
                                    description="Control whether the web search tool fetches live content or uses only cached content. For OpenAI API compatibility ONLY. Request will be rejected if this field is set.",
                                ),
                                "filters": ObjectParameter(
                                    label="Filters",
                                    description=(
                                        "Optional provider-specific filters for web search "
                                        "(e.g. domains, date ranges)."
                                    ),
                                ),
                                "search_context_size": EnumParameter(
                                    label="Search Context Size",
                                    description=(
                                        "How much retrieved web content is fed back to the model."
                                    ),
                                    options=[
                                        Option(value="low", label="Low"),
                                        Option(value="medium", label="Medium"),
                                        Option(value="high", label="High"),
                                    ],
                                    default="medium",
                                ),
                                "user_location": ObjectParameter(
                                    label="User Location",
                                    description="Optional structured location information to bias results toward the user's region.",
                                ),
                            },
                        ),
                    ),
                    Option(
                        value={"type": "x_search"},
                        label="X Search",
                        help="Allow the model to call the built-in X search tool.",
                        input_schema=ObjectParameter(
                            label="Web Search Configuration",
                            description="Optional configuration for the web_search tool.",
                            properties={
                                "allowed_x_handles": ArrayParameter(
                                    label="Allowed X handles",
                                    description="List of handles to consider.",
                                    items=StringParameter(
                                        label="Handle",
                                        placeholder="jack",
                                    ),
                                ),
                                "enable_image_understanding": BooleanParameter(
                                    label="Image understanding",
                                    description=(
                                        "Enable image understanding during X search."
                                    ),
                                ),
                                "enable_video_understanding": BooleanParameter(
                                    label="Video understanding",
                                    description=(
                                        "Enable video understanding during X search."
                                    ),
                                ),
                                "excluded_x_handles": ArrayParameter(
                                    label="Excluded X handles",
                                    description="List of X Handles of the users from whom to exclude the posts. Can not be set with allowed_x_handles.",
                                    items=StringParameter(
                                        label="Handle",
                                        placeholder="jack",
                                    ),
                                ),
                                "from_date": StringParameter(
                                    label="From date",
                                    description="Date from which to consider the results in ISO-8601 YYYY-MM-DD. See <https://en.wikipedia.org/wiki/ISO_8601>.",
                                ),
                                "to_date": StringParameter(
                                    label="To date",
                                    description="Date up to which to consider the results in ISO-8601 YYYY-MM-DD. See <https://en.wikipedia.org/wiki/ISO_8601>.",
                                ),
                            },
                        ),
                    ),
                    Option(
                        value={"type": "code_interpreter"},
                        label="Code Interpreter",
                        help="Allow the model to call the built-in Python code interpreter tool.",
                    ),
                ],
            ),

            "tool_choice": EnumParameter(
                label="Tool Choice",
                description=(
                    "Controls if and how tools are used. 'Auto' lets the model decide. 'None' disables tools. 'Specific function' forces a particular function by name."
                ),
                options=[
                    Option(
                        value="auto",
                        label="Auto",
                        help="Model decides whether and which tools to call.",
                    ),
                    Option(
                        value="none",
                        label="None",
                        help="Disable all tool calls for this response.",
                    ),
                    Option(
                        value={"type": "function", "name": ""},
                        label="Specific function (set name)",
                        help=(
                            "Force the model to call a specific function. The function must also be present in the 'tools' array."
                        ),
                        input_fields=[
                            InputField(
                                path="name",
                                type="string",
                                label="Function name",
                                required=True,
                            )
                        ],
                    ),
                ],
            ),

            "parallel_tool_calls": BooleanParameter(
                label="Parallel Tool Calls",
                default=True,
                description=(
                    "If enabled, the model may request multiple tool calls in parallel during a single step."
                ),
            ),

            # --- User identifier (for logging / abuse monitoring) ---

            "user": StringParameter(
                label="User ID",
                description=(
                    "Optional end-user identifier string for logging and abuse monitoring. "
                    "Passed as the 'user' field to xAI."
                ),
            ),
        }


register_adapter("xai", XAIAdapter)
