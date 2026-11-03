from abc import ABC, abstractmethod
from typing import Optional
from langchain_core.language_models import BaseChatModel
from langchain_core.embeddings import Embeddings
from langchain_ollama.chat_models import ChatOllama
from langchain_ollama.embeddings import OllamaEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
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
        return ChatOllama(model=config.OLLAMA_CHAT_MODEL_NAME, temperature=config.CHAT_TEMPERATURE)


class OllamaEmbeddingModelFactory(BaseModelFactory):
    """
    ollama 向量模型调用前创建
    """
    def generator(self):
        super().generator()
        return OllamaEmbeddings(model=config.OLLAMA_EMBEDDING_MODEL_NAME)

class OllamaSummaryChatFactory(BaseModelFactory):
    """
    ollama 的summary模型，qwen3:8b 模型调用前创建
    """
    def generator(self) -> Optional[BaseChatModel | Embeddings]:
        super().generator()
        return ChatOllama(model=config.OLLAMA_SUMMARY_MODEL_NAME, temperature=config.SUMMARY_TEMPERATURE)

class OpenAIChatFactory(BaseModelFactory):
    def generator(self) -> Optional[BaseChatModel | Embeddings]:
        super().generator()
        return ChatOpenAI(
            model=config.OPENAI_CHAT_MODEL,
            api_key=config.OPENAI_CHAT_API_KEY,
            base_url=config.OPENAI_CHAT_BASE_URL,   # 注意：ChatOpenAI 用 base_url
            temperature=config.CHAT_TEMPERATURE,
        )

class OpenAISummaryChatFactory(BaseModelFactory):
    def generator(self) -> Optional[BaseChatModel | Embeddings]:
        super().generator()
        return ChatOpenAI(
            model=config.OPENAI_SUMMARY_MODEL,
            api_key=config.OPENAI_CHAT_API_KEY,
            base_url=config.OPENAI_CHAT_BASE_URL,   # 注意：ChatOpenAI 用 base_url
            temperature=config.SUMMARY_TEMPERATURE,
        )

class OpenAIEmbeddingModelFactory(BaseModelFactory):
    def generator(self):
        super().generator()
        return OpenAIEmbeddings(
            model=config.OPENAI_EMBEDDING_MODEL,
            api_key=config.OPENAI_EMBEDDING_API_KEY,
            base_url=config.OPENAI_EMBEDDING_BASE_URL,
        )

# 模型
chat_model = OpenAIChatFactory().generator()
summary_model = OpenAISummaryChatFactory().generator()
embedding_model = OllamaEmbeddingModelFactory().generator()


