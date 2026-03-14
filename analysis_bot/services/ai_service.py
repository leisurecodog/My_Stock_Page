import google.genai as genai
from ollama import AsyncClient as OllamaAsyncClient
from google.genai import types
from typing import List, Union, Any
import random
import logging
from pathlib import Path
from enum import Enum
from ..config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


class RequestType(Enum):
    TEXT = 1
    IMAGE = 2
    FILE = 3
    AUDIO = 4
    VIDEO = 5


class AIService:
    """Service for AI interactions with switchable provider (Ollama / Gemini)."""

    def __init__(self):
        self.gemini_keys = settings.GEMINI_API_KEYS or []
        self.current_key_idx = 0

        # Initialize Gemini Clients
        self.gemini_clients = [
            self._create_gemini_client(key) for key in self.gemini_keys
        ]
        self.gemini_model = "gemini-2.0-flash-exp"

        # Initialize Ollama Client (Cloud or Local)
        ollama_headers = {}
        if settings.OLLAMA_API_KEY:
            ollama_headers = {"Authorization": f"Bearer {settings.OLLAMA_API_KEY}"}

        self.ollama_client = OllamaAsyncClient(
            host=settings.OLLAMA_BASE_URL,
            headers=ollama_headers,
            timeout=120.0,  # Overall timeout for Ollama API calls
        )
        self.ollama_model = settings.OLLAMA_MODEL

        # Provider: "ollama" or "gemini"
        self.provider = settings.AI_PROVIDER.lower()
        logger.info(f"AIService initialized with provider: {self.provider}")

    def _create_gemini_client(self, key: str):
        return genai.Client(api_key=key)

    async def generate_content(self, prompt: str) -> str:
        """Simple text generation (legacy support)"""
        return await self.call(RequestType.TEXT, contents=prompt)

    # ========== Ollama ==========
    async def _call_ollama(
        self, contents: str, prompt: str = None, use_search: bool = False
    ) -> str:
        """Call Ollama for text generation."""
        import asyncio
        import time

        final_prompt = (
            prompt + "\n" + contents
            if prompt and isinstance(contents, str)
            else str(contents)
        )

        # Web search (Ollama Cloud only) — 15s hard timeout, skip if slow
        if use_search and settings.OLLAMA_API_KEY:
            try:
                logger.info(f"Ollama web_search: {final_prompt[:80]}...")
                t0 = time.time()
                search_res = await asyncio.wait_for(
                    self.ollama_client.web_search(final_prompt, max_results=10),
                    timeout=15.0,
                )
                elapsed = time.time() - t0
                if search_res:
                    # Log search result titles & URLs
                    if hasattr(search_res, "results") and search_res.results:
                        for i, r in enumerate(search_res.results, 1):
                            logger.info(
                                f"  [{i}] {getattr(r, 'title', 'N/A')} - {getattr(r, 'url', '')}"
                            )
                    final_prompt = f"Background Information:\n{search_res}\n\nUser Request:\n{final_prompt}"
                logger.info(
                    f"Ollama web_search completed in {elapsed:.2f}s ({len(getattr(search_res, 'results', []))} results)"
                )
            except asyncio.TimeoutError:
                logger.warning("Ollama web_search timed out (>15s). Skipping search.")
            except Exception as e:
                logger.warning(
                    f"Ollama web_search failed: {e}. Proceeding without search."
                )

        t1 = time.time()
        response = await self.ollama_client.chat(
            model=self.ollama_model,
            messages=[{"role": "user", "content": final_prompt}],
        )
        chat_elapsed = time.time() - t1
        logger.info(f"Ollama chat completed in {chat_elapsed:.2f}s")

        if response and hasattr(response, "message") and response.message:
            return response.message.content
        raise ValueError(f"Invalid Ollama response: {response}")

    # ========== Gemini ==========
    async def _call_gemini(
        self,
        req_type: RequestType,
        contents,
        prompt: str = None,
        use_search: bool = False,
    ) -> str:
        """Call Gemini with key rotation and model fallback."""
        if not self.gemini_clients:
            raise RuntimeError("No Gemini keys configured.")

        models_to_try = [
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-3-flash-preview",
            "gemini-3.1-pro",
        ]
        max_total_attempts = 10
        attempt = 0

        import asyncio

        while attempt < max_total_attempts:
            client = self.gemini_clients[self.current_key_idx]
            current_model = models_to_try[0]

            try:
                if attempt > len(self.gemini_clients) * 2:
                    current_model = models_to_try[1]
                if attempt > len(self.gemini_clients) * 4:
                    current_model = models_to_try[2]

                config = None
                if use_search:
                    config = types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                    )

                if req_type == RequestType.TEXT:
                    final_prompt = (
                        prompt + "\n" + contents
                        if prompt and isinstance(contents, str)
                        else str(contents)
                    )
                    response = client.models.generate_content(
                        model=current_model,
                        contents=final_prompt,
                        config=config,
                    )
                    return response.text

                elif req_type in [
                    RequestType.FILE,
                    RequestType.IMAGE,
                    RequestType.AUDIO,
                ]:
                    parts = []
                    if isinstance(contents, list):
                        for mime, data in contents:
                            parts.append(
                                types.Part.from_bytes(data=data, mime_type=mime)
                            )
                    elif isinstance(contents, (Path, str)):
                        p = Path(contents)
                        if p.exists():
                            mime = "application/pdf"
                            if p.suffix.lower() == ".mp3":
                                mime = "audio/mp3"
                            elif p.suffix.lower() in [".jpg", ".jpeg", ".png"]:
                                mime = "image/jpeg"
                            elif p.suffix.lower() == ".wav":
                                mime = "audio/wav"
                            with open(p, "rb") as f:
                                parts.append(
                                    types.Part.from_bytes(data=f.read(), mime_type=mime)
                                )

                    parts.append(prompt if prompt else "請分析這份檔案/多媒體內容。")
                    response = client.models.generate_content(
                        model=current_model,
                        contents=parts,
                        config=config,
                    )
                    return response.text

            except Exception as e:
                error_str = str(e)
                logger.warning(
                    f"Gemini Attempt {attempt + 1}/{max_total_attempts} failed (Key {self.current_key_idx}, Model {current_model}): {error_str}"
                )

                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    self.current_key_idx = (self.current_key_idx + 1) % len(
                        self.gemini_clients
                    )
                    wait_time = min(2 * (1.5**attempt) + random.uniform(0, 1), 15)
                    logger.info(
                        f"Rate limit hit. Rotating key, waiting {wait_time:.2f}s..."
                    )
                    await asyncio.sleep(wait_time)
                elif (
                    "404" in error_str
                    or "not found" in error_str.lower()
                    or "400" in error_str
                ):
                    logger.warning(
                        f"Model {current_model} failed. Switching fallback..."
                    )
                    await asyncio.sleep(1)
                else:
                    self.current_key_idx = (self.current_key_idx + 1) % len(
                        self.gemini_clients
                    )
                    await asyncio.sleep(1)

                attempt += 1

        raise RuntimeError("Gemini: All attempts failed.")

    # ========== Unified Dispatch ==========
    async def call(
        self,
        req_type: RequestType,
        contents: Union[str, List[Any], Path],
        prompt: str = None,
        use_search: bool = False,
        force_provider: str = None,
    ) -> str:
        """
        Unified AI dispatch:
        - TEXT requests: use AI_PROVIDER setting, fallback to the other on failure.
        - FILE/IMAGE/AUDIO: always use Gemini (multimodal).
        """
        # Multimodal → always Gemini
        if req_type != RequestType.TEXT:
            try:
                return await self._call_gemini(req_type, contents, prompt, use_search)
            except Exception as e:
                logger.error(f"Gemini multimodal failed: {e}")
                return f"Error: Multimodal analysis failed - {e}"

        # TEXT → use configured provider, fallback to other
        primary = force_provider or self.provider  # "ollama" or "gemini"
        fallback = "gemini" if primary == "ollama" else "ollama"

        # Try primary
        try:
            if primary == "ollama":
                return await self._call_ollama(contents, prompt, use_search)
            else:
                return await self._call_gemini(req_type, contents, prompt, use_search)
        except Exception as e:
            logger.warning(
                f"Primary provider [{primary}] failed: {type(e).__name__} - {e}. Trying fallback [{fallback}]."
            )

        # Try fallback
        try:
            if fallback == "ollama":
                return await self._call_ollama(contents, prompt, use_search)
            else:
                return await self._call_gemini(req_type, contents, prompt, use_search)
        except Exception as e:
            logger.error(
                f"Fallback provider [{fallback}] also failed: {type(e).__name__} - {e}"
            )
            return "Error: All AI providers failed. Service unavailable."
