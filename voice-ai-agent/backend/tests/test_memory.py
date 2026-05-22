import pytest
from unittest.mock import AsyncMock, patch
import json
from datetime import datetime, date, time
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from db.models import Base, Patient, Appointment, InteractionLog
from memory.session import SessionMemory
from memory.persistent import PersistentMemory

# --- SessionMemory Tests ---

@pytest.mark.asyncio
async def test_session_memory_get_not_found():
    # Mock redis client
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        session_mem = SessionMemory(redis_url="redis://localhost:6379/0")
        session = await session_mem.get_session("session_123")
        
        # Should return default session structure
        assert session["intent"] is None
        assert session["language"] == "en"
        assert session["turn_count"] == 0
        mock_redis.get.assert_called_once_with("session:session_123")

@pytest.mark.asyncio
async def test_session_memory_get_existing():
    # Mock redis client returning JSON data
    mock_redis = AsyncMock()
    existing_data = {
        "intent": "book",
        "doctor_type": "Cardiologist",
        "date": "2024-05-22",
        "time": "10:30",
        "language": "hi",
        "turn_count": 2
    }
    mock_redis.get.return_value = json.dumps(existing_data)
    
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        session_mem = SessionMemory(redis_url="redis://localhost:6379/0")
        session = await session_mem.get_session("session_123")
        
        assert session["intent"] == "book"
        assert session["doctor_type"] == "Cardiologist"
        assert session["language"] == "hi"
        assert session["turn_count"] == 2
        mock_redis.get.assert_called_once_with("session:session_123")
        # Should refresh TTL
        mock_redis.expire.assert_called_once_with("session:session_123", 1800)

@pytest.mark.asyncio
async def test_session_memory_update():
    mock_redis = AsyncMock()
    # Initial state is empty/default
    mock_redis.get.return_value = None
    
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        session_mem = SessionMemory(redis_url="redis://localhost:6379/0")
        
        update_data = {"intent": "reschedule", "doctor_type": "Dentist"}
        updated_session = await session_mem.update_session("session_123", update_data)
        
        assert updated_session["intent"] == "reschedule"
        assert updated_session["doctor_type"] == "Dentist"
        assert updated_session["language"] == "en" # From default
        
        # Redis set should have been called with serialized JSON and TTL
        mock_redis.set.assert_called_once()
        args, kwargs = mock_redis.set.call_args
        assert args[0] == "session:session_123"
        assert "reschedule" in args[1]
        assert kwargs["ex"] == 1800

# --- PersistentMemory Tests ---

@pytest.mark.asyncio
async def test_persistent_memory_operations():
    # Use SQLite memory engine for testing persistent queries
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with async_session() as session:
        pm = PersistentMemory(session)
        
        # 1. Test get_patient on non-existent
        patient = await pm.get_patient("+919876543210")
        assert patient is None
        
        # 2. Add a patient manually
        new_patient = Patient(name="Harsh", phone="+919876543210", preferred_language="en")
        session.add(new_patient)
        await session.commit()
        
        # 3. Test get_patient on existent
        patient = await pm.get_patient("+919876543210")
        assert patient is not None
        assert patient.name == "Harsh"
        assert patient.preferred_language == "en"
        
        # 4. Test upsert_language_preference
        updated_patient = await pm.upsert_language_preference(patient.id, "ta")
        assert updated_patient.preferred_language == "ta"
        
        # 5. Add historical appointments
        appt1 = Appointment(patient_id=patient.id, doctor_type="Dentist", date=date(2024, 5, 21), time=time(10, 0), status="completed")
        appt2 = Appointment(patient_id=patient.id, doctor_type="Cardiologist", date=date(2024, 5, 22), time=time(14, 30), status="scheduled")
        session.add_all([appt1, appt2])
        await session.commit()
        
        # 6. Test get_patient_history
        history = await pm.get_patient_history(patient.id, limit=2)
        assert len(history) == 2
        assert history[0].doctor_type == "Cardiologist"  # Sorted by created_at desc (added second)
        
        # 7. Test save_interaction
        interaction = await pm.save_interaction(patient.id, "session_123", "Patient booked cardiologist appointment in Tamil.")
        assert interaction is not None
        assert interaction.session_id == "session_123"
        assert "cardiologist" in interaction.summary
        
    await engine.dispose()
