"""OpenAI subscription transport using credentials maintained by Codex."""

from openai import AsyncOpenAI
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from .config import Settings, get_codex_auth

CODEX_RESPONSES_BASE_URL = "https://chatgpt.com/backend-api/codex"


class CodexResponsesModel(OpenAIResponsesModel):
    """Responses model adapted to the Codex backend's streaming-only contract."""

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        async with self.request_stream(
            messages, model_settings, model_request_parameters
        ) as response:
            async for _ in response:
                pass
            return response.get()


def build_codex_model(settings: Settings) -> CodexResponsesModel:
    auth = get_codex_auth()
    if auth is None:
        raise ValueError("Codex OAuth credentials not found; run `codex login`")

    client = AsyncOpenAI(
        api_key=auth.access_token,
        base_url=settings.base_url.strip() or CODEX_RESPONSES_BASE_URL,
        default_headers={
            "ChatGPT-Account-Id": auth.account_id,
            "originator": "specfill",
        },
    )
    return CodexResponsesModel(
        settings.model,
        provider=OpenAIProvider(openai_client=client),
        settings={"openai_store": False},
    )
