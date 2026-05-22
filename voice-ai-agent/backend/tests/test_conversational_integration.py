import pytest
import json
import base64
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from main import app, session_memory
from db.database import get_db
from db.models import Base, Patient, Appointment
from agent.tools import seed_demo_data

class MockRedis:
    def __init__(self):
        self.store = {}
        
    async def get(self, key):
        return self.store.get(key)
        
    async def set(self, key, value, *args, **kwargs):
        self.store[key] = value
        return True
        
    async def expire(self, key, ttl):
        return True
        
    async def lpush(self, key, value):
        return True
        
    async def ltrim(self, key, start, end):
        return True

    async def close(self):
        pass

@pytest.mark.asyncio
async def test_multiturn_booking_and_cancellation():
    from sqlalchemy.pool import StaticPool
    from datetime import date, timedelta
    tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
    
    # 1. Setup in-memory SQLite DB with StaticPool to share connection across sessions
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        echo=False
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with async_session() as session:
        await seed_demo_data(session)
        
    async def override_get_db():
        async with async_session() as session:
            yield session
            
    app.dependency_overrides[get_db] = override_get_db

    # 2. Setup mock redis store
    mock_redis = MockRedis()

    # 3. Setup mock services
    mock_stt = MagicMock()
    mock_tts = AsyncMock()
    mock_tts.generate_tts.return_value = "mock_audio_b64"
    mock_groq_client = AsyncMock()
    mock_completions = AsyncMock()
    mock_groq_client.chat.completions = mock_completions

    try:
        with patch("main.stt_service", mock_stt), \
             patch("main.tts_service", mock_tts), \
             patch.object(session_memory, "redis", mock_redis), \
             patch("main.agent.client", mock_groq_client):

            with TestClient(app) as test_client:
                session_id = "test_conversation_session_999"
                patient_phone = "+919876543210" # matches seeded patient Harsh Patel (id: 1)
                
                # --- TURN 1: Check availability for Cardiologist today ---
                async def mock_stream_stt_turn1(*args, **kwargs):
                    yield {"text": "I want to check cardiologist availability today", "language": "en", "is_final": True}
                mock_stt.stream_stt = mock_stream_stt_turn1

                # Mock tool call output from first LLM pass
                mock_tool_call1 = MagicMock()
                mock_tool_call1.function.name = "check_availability"
                mock_tool_call1.function.arguments = json.dumps({
                    "doctor_type": "Cardiologist",
                    "date": tomorrow_str
                })
                
                mock_response1 = MagicMock()
                mock_response1.choices = [
                    MagicMock(message=MagicMock(content="Checking availability...", tool_calls=[mock_tool_call1]))
                ]
                
                # Mock second reasoning pass response text
                mock_response_second1 = MagicMock()
                mock_response_second1.choices = [
                    MagicMock(message=MagicMock(content="We have slots at 14:00, 14:30. Would you like to book one of them?"))
                ]
                
                mock_completions.create.side_effect = [mock_response1, mock_response_second1]

                # Run Turn 1 request
                payload = {
                    "audio_base64": base64.b64encode(b"audio_bytes").decode("utf-8"),
                    "session_id": session_id,
                    "patient_phone": patient_phone
                }
                res1 = test_client.post("/voice/process", json=payload)
                assert res1.status_code == 200
                data1 = res1.json()
                assert data1["response_text"] == "We have slots at 14:00, 14:30. Would you like to book one of them?"
                assert data1["trace"]["intent_detected"] == "check_availability"

                # Check that history has been updated in Redis session
                redis_data = await mock_redis.get(f"session:{session_id}")
                assert redis_data is not None
                session_state = json.loads(redis_data)
                assert len(session_state["messages"]) == 2
                assert session_state["messages"][0]["content"] == "I want to check cardiologist availability today"
                assert session_state["messages"][1]["content"] == "We have slots at 14:00, 14:30. Would you like to book one of them?"
                assert session_state["doctor_type"] == "Cardiologist"
                assert session_state["date"] == tomorrow_str

                # --- TURN 2: Book for 2:30 PM (using context memory) ---
                async def mock_stream_stt_turn2(*args, **kwargs):
                    yield {"text": "Book for 2:30 PM", "language": "en", "is_final": True}
                mock_stt.stream_stt = mock_stream_stt_turn2

                # In Turn 2, the LLM remembers that the doctor type is Cardiologist and date is tomorrow_str
                mock_tool_call2 = MagicMock()
                mock_tool_call2.function.name = "book_appointment"
                mock_tool_call2.function.arguments = json.dumps({
                    "doctor_type": "Cardiologist",
                    "date": tomorrow_str,
                    "time": "14:30"
                })
                
                mock_response2 = MagicMock()
                mock_response2.choices = [
                    MagicMock(message=MagicMock(content="Booking slot...", tool_calls=[mock_tool_call2]))
                ]
                
                mock_response_second2 = MagicMock()
                mock_response_second2.choices = [
                    MagicMock(message=MagicMock(content="Great, your appointment with Cardiologist today at 14:30 is successfully scheduled."))
                ]
                
                mock_completions.create.side_effect = [mock_response2, mock_response_second2]

                res2 = test_client.post("/voice/process", json=payload)
                assert res2.status_code == 200
                data2 = res2.json()
                assert "successfully scheduled" in data2["response_text"]
                assert data2["trace"]["intent_detected"] == "book_appointment"

                # Check Redis memory again
                redis_data2 = await mock_redis.get(f"session:{session_id}")
                session_state2 = json.loads(redis_data2)
                assert len(session_state2["messages"]) == 4
                assert session_state2["messages"][2]["content"] == "Book for 2:30 PM"
                assert "successfully scheduled" in session_state2["messages"][3]["content"]
                assert session_state2["time"] == "14:30"

                # Verify appointment actually written in Postgres
                async with async_session() as db_verif:
                    from sqlalchemy import select
                    stmt_all_patients = select(Patient)
                    patients_res = await db_verif.execute(stmt_all_patients)
                    all_patients = patients_res.scalars().all()
                    print(f"DIAGNOSTIC - All Patients: {[p.to_dict() for p in all_patients]}")

                    stmt_all_appts = select(Appointment)
                    appts_res = await db_verif.execute(stmt_all_appts)
                    all_appts = appts_res.scalars().all()
                    print(f"DIAGNOSTIC - All Appointments: {[a.to_dict() for a in all_appts]}")

                    stmt = select(Appointment).where(Appointment.patient_id == 1)
                    db_res = await db_verif.execute(stmt)
                    appts = db_res.scalars().all()
                    assert len(appts) == 1
                    assert appts[0].doctor_type == "Cardiologist"
                    assert appts[0].time.strftime("%H:%M") == "14:30"
                    
                    # Store ID for turn 3 cancellation
                    appt_id = appts[0].id

                # --- TURN 3: Cancel appointment ---
                async def mock_stream_stt_turn3(*args, **kwargs):
                    yield {"text": "Cancel my cardiologist appointment", "language": "en", "is_final": True}
                mock_stt.stream_stt = mock_stream_stt_turn3

                mock_tool_call3 = MagicMock()
                mock_tool_call3.function.name = "cancel_appointment"
                mock_tool_call3.function.arguments = json.dumps({
                    "appointment_id": appt_id
                })
                
                mock_response3 = MagicMock()
                mock_response3.choices = [
                    MagicMock(message=MagicMock(content="Cancelling appointment...", tool_calls=[mock_tool_call3]))
                ]
                
                mock_response_second3 = MagicMock()
                mock_response_second3.choices = [
                    MagicMock(message=MagicMock(content="Your cardiologist appointment has been successfully cancelled."))
                ]
                
                mock_completions.create.side_effect = [mock_response3, mock_response_second3]

                res3 = test_client.post("/voice/process", json=payload)
                assert res3.status_code == 200
                data3 = res3.json()
                assert "cancelled" in data3["response_text"]
                assert data3["trace"]["intent_detected"] == "cancel_appointment"

                # Verify status updated to cancelled in Postgres
                async with async_session() as db_verif2:
                    stmt = select(Appointment).where(Appointment.id == appt_id)
                    db_res2 = await db_verif2.execute(stmt)
                    cancelled_appt = db_res2.scalar_one()
                    assert cancelled_appt.status == "cancelled"

    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
