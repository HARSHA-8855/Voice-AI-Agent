import pytest
import asyncio
import json
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

    # Mock Response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "transcript": "Hello world",
        "language_code": "en-IN"
    }

    # Mock client
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch.object(stt_service, "_get_client", return_value=mock_client):
        results = []
        async for res in stt_service.stream_stt(mock_audio_gen()):
            results.append(res)
            if res["is_final"]:
                break
        
        assert len(results) == 1
        assert results[0]["text"] == "Hello world"
        assert results[0]["language"] == "en"
        assert results[0]["is_final"] is True
