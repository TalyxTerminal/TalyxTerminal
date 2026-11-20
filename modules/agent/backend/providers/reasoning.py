"""Chat Completions providers that return thinking as `reasoning_content` (DeepSeek, Kimi, Zhipu, LM Studio).

ChatOpenAI drops that field. These providers also require it back on earlier assistant turns when tools are
in use, so it is kept on the message and returned with the history. The messages are not tagged as OpenAI's,
so LangChain reads that field as the message's reasoning instead of applying OpenAI's own block format.
"""

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

PROVIDER = "openai-compatible"


class ReasoningChatOpenAI(ChatOpenAI):
    reasoning_field: str | None = "reasoning_content"
    catalog_model_id: str | None = None

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        data = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(result.generations, data["choices"], strict=True):
            generation.message.response_metadata["model_provider"] = PROVIDER
            message = choice["message"]
            for key in ("reasoning_content", "reasoning"):
                if isinstance(message.get(key), str):
                    generation.message.additional_kwargs[key] = message[key]
                    if self.catalog_model_id:
                        generation.message.response_metadata["talyx_reasoning_model"] = {self.catalog_model_id: True}
        return result

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        generation = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        if generation is None:
            return None
        generation.message.response_metadata["model_provider"] = PROVIDER
        choices = chunk["choices"] if "choices" in chunk else []
        if choices and "delta" in choices[0]:
            delta = choices[0]["delta"]
            for key in ("reasoning_content", "reasoning"):
                if isinstance(delta.get(key), str):
                    generation.message.additional_kwargs[key] = delta[key]
                    if self.catalog_model_id:
                        generation.message.response_metadata["talyx_reasoning_model"] = {self.catalog_model_id: True}
        return generation

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "messages" in payload:
            messages = self._convert_input(input_).to_messages()
            for message, sent in zip(messages, payload["messages"], strict=True):
                if isinstance(message, AIMessage):
                    origin = message.response_metadata.get("talyx_reasoning_model")
                    same_model = isinstance(origin, dict) and self.catalog_model_id in origin
                    if not self.reasoning_field and not same_model:
                        continue
                    for key in ("reasoning_content", "reasoning"):
                        if isinstance(message.additional_kwargs.get(key), str):
                            sent[self.reasoning_field or key] = message.additional_kwargs[key]
                            break
        return payload
