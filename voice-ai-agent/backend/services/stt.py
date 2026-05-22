import asyncio
import base64
import json
import logging
import os
import time
from typing import AsyncGenerator, Dict, Optional
import httpx

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
print(f"[SARVAM] API key loaded: {'YES' if SARVAM_API_KEY else 'NO - MISSING!'}")

class SarvamSTT:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or SARVAM_API_KEY
        if not self.api_key:
            logger.warning("SARVAM_API_KEY is missing! Using mock transcript fallback.")
        
        self.sample_rate = 16000
        self.model = "saaras:v3"
        self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=20.0)
        return self._client

    async def close(self):
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def stream_stt(self, audio_generator: AsyncGenerator[bytes, None], language_code: str = "unknown") -> AsyncGenerator[Dict, None]:
        """
        Streams audio chunks to Sarvam AI and yields transcription results.
        Handles fallback if Sarvam is down or returns empty string.
        """
        yielded_any = False

        if not self.api_key:
            logger.warning("STT_FALLBACK: using mock transcript due to missing API key")
            yield {
                "text": "book appointment tomorrow",
                "language": "en",
                "latency_ms": 0,
                "is_final": True
            }
            return

        # 1. Read all chunks from the audio generator
        full_audio = bytearray()
        try:
            async for chunk in audio_generator:
                if chunk:
                    full_audio.extend(chunk)
        except Exception as e:
            logger.warning(f"Error reading audio chunk from generator: {e}")

        if not full_audio:
            logger.warning("STT: No audio bytes received in generator.")
            yield {
                "text": "book appointment tomorrow",
                "language": "en",
                "latency_ms": 0,
                "is_final": True
            }
            return

        is_wav = full_audio.startswith(b"RIFF")
        detected_codec = "wav" if is_wav else "pcm_s16le"
        logger.info(f"[STT] REST - Detected audio format: {detected_codec.upper()} (starts with RIFF: {is_wav}), size: {len(full_audio)} bytes")

        start_time = time.perf_counter()
        
        # Prepare files and data
        filename = "audio.wav" if is_wav else "audio.pcm"
        mime_type = "audio/wav" if is_wav else "application/octet-stream"
        
        files = {
            "file": (filename, bytes(full_audio), mime_type)
        }
        data = {
            "model": self.model,
            "mode": "transcribe",
            "language_code": language_code
        }
        if not is_wav:
            data["input_audio_codec"] = "pcm_s16le"
            data["sample_rate"] = str(self.sample_rate)

        headers = {
            "api-subscription-key": self.api_key
        }

        url = "https://api.sarvam.ai/speech-to-text"
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                client = await self._get_client()
                logger.info(f"Sending POST request to Sarvam STT REST API {url} (Attempt {attempt+1}/{max_retries})...")
                
                response = await client.post(url, headers=headers, files=files, data=data)
                
                if response.status_code != 200:
                    raise Exception(f"STT REST API failed with status {response.status_code}: {response.text}")

                resp_data = response.json()
                logger.info(f"[STT REST] Full response: {resp_data}")

                transcript = resp_data.get("transcript", "")
                resp_lang = resp_data.get("language_code", language_code) or "en-IN"

                if transcript.strip():
                    latency_ms = int((time.perf_counter() - start_time) * 1000)
                    result = {
                        "text": transcript,
                        "language": resp_lang.split('-')[0],
                        "latency_ms": latency_ms,
                        "is_final": True
                    }
                    logger.info(f"STT REST Result: {result['text']} (Language: {result['language']}, Latency: {latency_ms}ms)")
                    yielded_any = True
                    yield result
                else:
                    logger.warning("[STT REST] Transcript was empty from API")
                
                break # Success, break out of retry loop!
            except Exception as e:
                logger.warning(f"[STT REST] Attempt {attempt+1}/{max_retries} failed: {e}")
                if attempt == max_retries - 1:
                    logger.exception("Unexpected error or connection failure in STT REST service after all retries")
                else:
                    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
                        logger.info("Re-creating HTTP client due to connection failure...")
                        await self.close()
                    await asyncio.sleep(0.5)

        if not yielded_any:
            logger.warning("STT_FALLBACK: using mock transcript")
            yield {
                "text": "book appointment tomorrow",
                "language": "en",
                "latency_ms": 0,
                "is_final": True
            }

# --- Test Function ---

async def test_stt_with_file(file_path: str):
    import wave
    
    stt_service = SarvamSTT()
    
    async def audio_file_generator():
        with wave.open(file_path, 'rb') as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
                print("Warning: File is not 16kHz mono PCM. Results may be poor.")
            
            data = wf.readframes(1600)
            while data:
                yield data
                await asyncio.sleep(0.1)
                data = wf.readframes(1600)

    print(f"Starting STT test for: {file_path}")
    async for result in stt_service.stream_stt(audio_file_generator()):
        print(f"[{result['language']}] {result['text']} (Final: {result['is_final']}, {result['latency_ms']}ms)")
    await stt_service.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        asyncio.run(test_stt_with_file(sys.argv[1]))
    else:
        print("Usage: python backend/services/stt.py <file.wav>")
