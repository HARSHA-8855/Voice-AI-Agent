import os
import logging
import httpx
from typing import Optional
import struct
import base64

logger = logging.getLogger(__name__)

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
print(f"[SARVAM] API key loaded: {'YES' if SARVAM_API_KEY else 'NO - MISSING!'}")

def get_silent_wav() -> str:
    # 1 second of 16kHz mono 16-bit PCM silence
    num_channels = 1
    sample_width = 2
    frame_rate = 16000
    num_frames = 16000
    data_size = num_frames * num_channels * sample_width # 32000
    
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        36 + data_size,
        b'WAVE',
        b'fmt ',
        16,
        1, # PCM
        num_channels,
        frame_rate,
        frame_rate * num_channels * sample_width,
        num_channels * sample_width,
        sample_width * 8,
        b'data',
        data_size
    )
    data = b'\x00' * data_size
    return base64.b64encode(header + data).decode('utf-8')

MOCK_SILENT_WAV_BASE64 = get_silent_wav()

class SarvamTTS:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or SARVAM_API_KEY
        if not self.api_key:
            logger.warning("SARVAM_API_KEY is missing! Using mock silent audio fallback.")
        self.url = "https://api.sarvam.ai/text-to-speech"
        self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=20.0)
        return self._client

    async def close(self):
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def generate_tts(self, text: str, language_code: str = "en-IN") -> str:
        """
        Calls Sarvam AI's Text-to-Speech API to convert text to speech.
        Returns the base64-encoded audio string (WAV format).
        If it fails or times out, catches the exception and returns a pre-generated silent audio base64.
        """
        if not self.api_key:
            logger.warning("TTS_FALLBACK: missing API key, returning mock silent audio")
            return MOCK_SILENT_WAV_BASE64

        headers = {
            "api-subscription-key": self.api_key,
            "Content-Type": "application/json"
        }
        
        # Mapping standard language tags to Sarvam's expected locale formats
        lang_mapping = {
            "en": "en-IN",
            "hi": "hi-IN",
            "ta": "ta-IN",
            "english": "en-IN",
            "hindi": "hi-IN",
            "tamil": "ta-IN"
        }
        target_lang = lang_mapping.get(language_code.lower(), language_code)
        
        payload = {
            "text": text,
            "speaker": "ritu",
            "target_language_code": target_lang,
            "model": "bulbul:v3"
        }
        
        logger.info(f"Requesting Sarvam TTS for text length: {len(text)} (Language: {target_lang})...")
        try:
            client = await self._get_client()
            response = await client.post(self.url, headers=headers, json=payload)
            if response.status_code != 200:
                raise Exception(f"TTS API failed with status {response.status_code}: {response.text}")
            
            data = response.json()
            audios = data.get("audios", [])
            if not audios:
                raise Exception("Sarvam TTS response did not return any audio data.")
                
            return audios[0]
        except Exception as e:
            print(f"[TTS_ERROR] Sarvam TTS failed or timed out: {e}")
            logger.error(f"TTS_FALLBACK: returning mock silent audio due to error: {e}")
            return MOCK_SILENT_WAV_BASE64

    async def stream_tts(self, text: str, language_code: str = "en-IN"):
        """
        Original placeholder method wrapper for streaming compatibility.
        """
        return await self.generate_tts(text, language_code)
