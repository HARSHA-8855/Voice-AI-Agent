import pytest
import os

# Set dummy environment variables for initializers
os.environ["GROQ_API_KEY"] = "dummy_groq_key"
os.environ["SARVAM_API_KEY"] = "dummy_sarvam_key"

from datetime import datetime, date, time, timedelta
from unittest.mock import MagicMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from db.models import Base, Patient, Appointment, InteractionLog
from scheduler.campaigns import run_outbound_campaign
from agent.agent import ClinicalAgent

@pytest.mark.asyncio
async def test_run_outbound_campaign_workflow():
    # 1. Initialize SQLite in-memory async engine
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    # Seed mock patient and appointment
    async with async_session() as seed_session:
        patient = Patient(name="Test Patient", phone="+917777777777", preferred_language="en")
        seed_session.add(patient)
        await seed_session.commit()
        await seed_session.refresh(patient)
        
        tomorrow = date.today() + timedelta(days=1)
        appt = Appointment(
            patient_id=patient.id,
            doctor_type="General Physician",
            date=tomorrow,
            time=time(10, 0),
            status="scheduled",
            reminder_sent=False
        )
        seed_session.add(appt)
        await seed_session.commit()
        await seed_session.refresh(appt)
        
        patient_id = patient.id
        appt_id = appt.id

    # Mock Redis client
    mock_redis = MagicMock()
    
    # Mock ClinicalAgent process_request response
    mock_agent_res = {
        "response_text": "I have successfully logged your reminder response.",
        "tool_called": None,
        "tool_result": None,
        "latency_ms": 150
    }
    
    # Patch AsyncSessionLocal to be our in-memory session maker
    with patch("db.database.AsyncSessionLocal", new=async_session), \
         patch("scheduler.campaigns.redis_client", mock_redis), \
         patch.object(ClinicalAgent, "process_request", return_value=mock_agent_res) as mock_process:
         
         await run_outbound_campaign(campaign_id="test_unit_campaign")
         
         # Verify agent was processed with the correct seeded message
         mock_process.assert_called_once()
         called_args = mock_process.call_args[1]
         assert "Test Patient" in called_args["user_text"]
         assert "General Physician" in called_args["user_text"]
         assert "10:00" in called_args["user_text"]
         
         # Verify Redis logs were pushed
         mock_redis.lpush.assert_called_once()
         mock_redis.ltrim.assert_called_once()
         mock_redis.expire.assert_called_once()
         
    # Query database to verify changes in a fresh verification session
    async with async_session() as verify_session:
        from sqlalchemy import select
        
        # Verify appointment updated
        res_appt = await verify_session.execute(select(Appointment).where(Appointment.id == appt_id))
        updated_appt = res_appt.scalar_one()
        assert updated_appt.reminder_sent is True
        
        # Verify interaction logs
        res_log = await verify_session.execute(select(InteractionLog))
        logs = res_log.scalars().all()
        assert len(logs) == 1
        assert logs[0].patient_id == patient_id
        assert "Outbound Reminder" in logs[0].summary
        assert "Test Patient" in logs[0].summary
        assert "I have successfully logged" in logs[0].summary
