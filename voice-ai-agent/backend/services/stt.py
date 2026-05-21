import asyncio
import base64
import json
import logging
import os
import time
from typing import AsyncGenerator, Dict, Optional
import websockets

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SarvamSTT:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SARVAM_API_KEY")
        if not self.api_key:
            raise ValueError("SARVAM_API_KEY must be provided or set in environment variables")
        
        self.ws_url = "wss://api.sarvam.ai/speech-to-text/ws"
        self.sample_rate = 16000
        self.model = "saaras:v3"

    async def stream_stt(self, audio_generator: AsyncGenerator[bytes, None], language_code: str = "hi-IN") -> AsyncGenerator[Dict, None]:
        """
        Streams audio chunks to Sarvam AI and yields transcription results.
        Handles reconnection and measures latency.
        """
        reconnect_delay = 1
        max_reconnect_delay = 16

        while True:
            try:
                # Prepare headers with subscription key
                headers = {"api-subscription-key": self.api_key}
                
                logger.info(f"Connecting to Sarvam STT WebSocket at {self.ws_url}...")
                async with websockets.connect(self.ws_url, extra_headers=headers) as ws:
                    logger.info("Connected to Sarvam STT WebSocket")
                    reconnect_delay = 1  # Reset delay on success
                    
                    # 1. Send the required configuration packet first
                    config_payload = {
                        "model": self.model,
                        "language_code": language_code,
                        "mode": "transcribe",
                        "sample_rate": self.sample_rate
                    }
                    await ws.send(json.dumps(config_payload))
                    logger.info(f"Sent STT config payload: {config_payload}")
                    
                    first_chunk_time: Optional[float] = None
                    
                    # Task to send audio chunks
                    async def send_audio():
                        nonlocal first_chunk_time
                        async for chunk in audio_generator:
                            if first_chunk_time is None:
                                first_chunk_time = time.perf_counter()
                            
                            # Format as a base64 encoded audio payload in JSON
                            payload = {
                                "audio": base64.b64encode(chunk).decode("utf-8")
                            }
                            await ws.send(json.dumps(payload))
                        
                        # Stream finished, close/flush if needed
                        # For Sarvam, sending a flush message signals end of audio stream
                        await ws.send(json.dumps({"type": "flush"}))

                    # Task to receive transcripts
                    send_task = asyncio.create_task(send_audio())
                    
                    try:
                        async for message in ws:
                            data = json.loads(message)
                            
                            # Extract transcript and metadata robustly from direct or nested structure
                            transcript = ""
                            is_final = False
                            resp_lang = language_code.split('-')[0]
                            
                            if "transcript" in data:
                                transcript = data["transcript"]
                                is_final = data.get("is_final", False)
                                resp_lang = data.get("language_code", resp_lang)
                            elif "data" in data and isinstance(data["data"], dict):
                                nested_data = data["data"]
                                transcript = nested_data.get("transcript", "")
                                is_final = nested_data.get("is_final", False) or data.get("is_final", False)
                                resp_lang = nested_data.get("language_code", resp_lang)
                            
                            if transcript.strip():
                                latency_ms = 0
                                if first_chunk_time:
                                    latency_ms = int((time.perf_counter() - first_chunk_time) * 1000)
                                
                                result = {
                                    "text": transcript,
                                    "language": resp_lang.split('-')[0],
                                    "latency_ms": latency_ms,
                                    "is_final": is_final
                                }
                                
                                logger.info(f"STT Result: {result['text']} (Final: {is_final}, Latency: {latency_ms}ms)")
                                yield result
                                
                                # If it's a final transcript (end of utterance via VAD), we can reset first_chunk_time
                                if is_final:
                                    first_chunk_time = None

                    except websockets.exceptions.ConnectionClosed:
                        logger.warning("WebSocket connection closed unexpectedly")
                    finally:
                        send_task.cancel()
                        break # Exit the while True loop if everything finished normally

            except (websockets.exceptions.WebSocketException, OSError) as e:
                logger.error(f"Connection error: {e}. Reconnecting in {reconnect_delay}s...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
            except Exception as e:
                logger.error(f"Unexpected error in STT service: {e}")
                break

# --- Test Function ---

async def test_stt_with_file(file_path: str):
    import wave
    
    stt_service = SarvamSTT()
    
    async def audio_file_generator():
        with wave.open(file_path, 'rb') as wf:
            # Ensure it's 16kHz mono 16-bit
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
                print("Warning: File is not 16kHz mono PCM. Results may be poor.")
            
            chunk_size = 3200 # 100ms of audio at 16kHz 16-bit mono
            data = wf.readframes(1600)
            while data:
                yield data
                await asyncio.sleep(0.1) # Simulate real-time streaming
                data = wf.readframes(1600)

    print(f"Starting STT test for: {file_path}")
    async for result in stt_service.stream_stt(audio_file_generator()):
        print(f"[{result['language']}] {result['text']} (Final: {result['is_final']}, {result['latency_ms']}ms)")

if __name__ == "__main__":
    # To run the test: python backend/services/stt.py <path_to_wav>
    import sys
    if len(sys.argv) > 1:
        asyncio.run(test_stt_with_file(sys.argv[1]))
    else:
        print("Usage: python backend/services/stt.py <file.wav>")
