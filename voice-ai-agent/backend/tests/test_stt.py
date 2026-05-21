import pytest
import asyncio
import json
import base64
from unittest.mock import MagicMock, patch, AsyncMock
from services.stt import SarvamSTT

@pytest.fixture
def stt_service():
    return SarvamSTT(api_key="test_key")

@pytest.mark.asyncio
async def test_stream_stt_success(stt_service):
    # Mock audio generator
    async def mock_audio_gen():
        yield b"fake_audio_chunk"

    # Mock websocket
    mock_ws = AsyncMock()
    
    # Mock receiving messages using an async generator
    async def mock_recv():
        yield json.dumps({"transcript": "Hello", "is_final": False})
        yield json.dumps({"transcript": "Hello world", "is_final": True})
    
    mock_ws.__aiter__.side_effect = mock_recv

    # Mock connect to be an async context manager
    mock_connect = AsyncMock()
    mock_connect.__aenter__.return_value = mock_ws
    
    with patch("websockets.connect", return_value=mock_connect):
        results = []
        # We need to wrap the generator consumption to avoid infinite loop if not careful
        # But our mock_responses is finite.
        async for res in stt_service.stream_stt(mock_audio_gen()):
            results.append(res)
            if res["is_final"]:
                break
        
        assert len(results) == 2
        assert results[0]["text"] == "Hello"
        assert results[1]["text"] == "Hello world"
        assert results[1]["is_final"] is True
