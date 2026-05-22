import pytest
import json
import base64
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from main import app, session_memory
from db.database import get_db
from db.models import Base
from agent.tools import seed_demo_data

@pytest.fixture
def client():
    # Use standard FastAPI TestClient
    return TestClient(app)

def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Voice AI Backend is running" in response.json()["message"]

def test_health_endpoint_fail_scenario(client):
    # Tests health endpoint when services are not reachable
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "redis" in data
    assert "postgres" in data

@pytest.mark.asyncio
async def test_metrics_and_traces_endpoints():
    # Mock redis client calls inside the endpoints
    mock_redis = AsyncMock()
    mock_redis.lrange.return_value = [
        json.dumps({
            "timestamp": "2026-05-21T13:45:22",
            "session_id": "session_test_123",
            "total_ms": 350,
            "breakdown": {"stt_ms": 100, "lang_ms": 10, "memory_ms": 40, "llm_ms": 150, "tts_ms": 50, "total_ms": 350},
            "tool_called": "check_availability",
            "language": "hi"
        })
    ]
    
    with patch.object(session_memory, "redis", mock_redis):
        with TestClient(app) as test_client:
            # Test metrics
            res_metrics = test_client.get("/metrics")
            assert res_metrics.status_code == 200
            metrics_data = res_metrics.json()
            assert len(metrics_data) == 1
            assert metrics_data[0]["session_id"] == "session_test_123"
            assert metrics_data[0]["tool_called"] == "check_availability"
            
            # Test traces
            res_traces = test_client.get("/traces")
            assert res_traces.status_code == 200
            assert len(res_traces.json()) == 1

@pytest.mark.asyncio
async def test_full_voice_pipeline_process():
    # Generate mock audio base64
    mock_audio_b64 = base64.b64encode(b"dummy_wav_pcm_data").decode("utf-8")
    
    # 1. Mock STT Response (using MagicMock to directly assign async generator function)
    mock_stt = MagicMock()
    async def mock_stream_stt(*args, **kwargs):
        yield {"text": "I want a dentist for tomorrow", "language": "en", "is_final": True}
    mock_stt.stream_stt = mock_stream_stt

    # 2. Mock TTS Response
    mock_tts = AsyncMock()
    mock_tts.generate_tts.return_value = "mock_output_audio_base64"

    # 3. Mock Agent Response
    mock_agent = AsyncMock()
    mock_agent.process_request.return_value = {
        "response_text": "I am checking the details for Dentist.",
        "tool_called": "check_availability",
        "tool_result": {
            "args": {
                "doctor_type": "Dentist",
                "date": "2026-05-22"
            }
        }
    }
    # Mock system prompt builder for agent
    mock_agent._build_system_prompt.return_value = "system prompt"
    # Mock client and chat completions inside agent
    mock_groq_client = AsyncMock()
    mock_completions = AsyncMock()
    mock_choices = MagicMock()
    mock_choices.choices = [
        MagicMock(message=MagicMock(content="Certainly, dentist appointment on May 22nd is confirmed."))
    ]
    mock_completions.create.return_value = mock_choices
    mock_groq_client.chat.completions = mock_completions
    mock_agent.client = mock_groq_client
    mock_agent.model = "llama-3.1-70b-versatile"

    # 4. Mock Redis
    mock_redis = AsyncMock()
    mock_redis.get.return_value = json.dumps({
        "intent": None, "doctor_type": None, "date": None, "time": None, "language": "en", "turn_count": 0
    })

    # Setup database inside test directly to bypass autouse fixture warnings
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with async_session() as session:
        await seed_demo_data(session)
        
    async def override_get_db():
        async with async_session() as session:
            yield session
            
    app.dependency_overrides[get_db] = override_get_db

    try:
        # Patches
        with patch("main.stt_service", mock_stt), \
             patch("main.tts_service", mock_tts), \
             patch("main.agent", mock_agent), \
             patch.object(session_memory, "redis", mock_redis):
             
            with TestClient(app) as test_client:
                payload = {
                    "audio_base64": mock_audio_b64,
                    "session_id": "test_session_123",
                    "patient_phone": "+919876543210"
                }
                response = test_client.post("/voice/process", json=payload)
                
                assert response.status_code == 200
                data = response.json()
                assert data["audio_base64"] == "mock_output_audio_base64"
                assert "latency_breakdown" in data
                assert "stt_ms" in data["latency_breakdown"]
                assert "tts_ms" in data["latency_breakdown"]
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
