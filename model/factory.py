from abc import ABC, abstractmethod
from typing import Optional
from langchain_core.language_models import BaseChatModel
from langchain_core.embeddings import Embeddings
from langchain_ollama.chat_models import ChatOllama
from langchain_openai import ChatOpenAI
import config


class BaseModelFactory(ABC):
    @abstractmethod
    def generator(self) -> Optional[BaseChatModel | Embeddings]:
        pass


class OllamaChatFactory(BaseModelFactory):
    """
    ollama 的chat模型，qwen3:8b 模型调用前创建
    """
    def generator(self) -> Optional[BaseChatModel | Embeddings]:
        super().generator()
        return ChatOllama(model=config.CHAT_MODEL_NAME, temperature=config.CHAT_TEMPERATURE)


class OllamaEmbeddingModelFactory(BaseModelFactory):
    """
    ollama 向量模型调用前创建
    """
    def generator(self):
        super().generator(self)
        pass

class OllamaSummaryChatFactory(BaseModelFactory):
    """
    ollama 的summary模型，qwen3:8b 模型调用前创建
    """
    def generator(self) -> Optional[BaseChatModel | Embeddings]:
        super().generator()
        return ChatOllama(model=config.SUMMARY_MODEL_NAME, temperature=config.SUMMARY_TEMPERATURE)

class DeepseekChatFactory(BaseModelFactory):
    def generator(self) -> Optional[BaseChatModel | Embeddings]:
        super().generator()
        return ChatOpenAI(
            model=config.DEEPSEEK_CHAT_MODEL,
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,   # 注意：ChatOpenAI 用 base_url
            temperature=config.CHAT_TEMPERATURE,
        )

class DeepseekSummaryChatFactory(BaseModelFactory):
    def generator(self) -> Optional[BaseChatModel | Embeddings]:
        super().generator()
        return ChatOpenAI(
            model=config.DEEPSEEK_SUMMARY_MODEL,
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,   # 注意：ChatOpenAI 用 base_url
            temperature=config.SUMMARY_TEMPERATURE,
        )

deepseek_chat_model = DeepseekChatFactory().generator()
deepseek_summary_model = DeepseekSummaryChatFactory().generator()

ollama_chat_model = OllamaChatFactory().generator()
ollama_summary_model = OllamaSummaryChatFactory().generator()

