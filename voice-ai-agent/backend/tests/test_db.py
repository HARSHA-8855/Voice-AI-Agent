import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import Base, Patient, Appointment
from datetime import datetime

@pytest.mark.asyncio
async def test_db_models():
    # Use in-memory sqlite for testing models
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with async_session() as session:
        # Create a patient
        patient = Patient(name="Test Patient")
        session.add(patient)
        await session.commit()
        
        # Create an appointment
        appointment = Appointment(patient_id=patient.id, appointment_time=datetime.now())
        session.add(appointment)
        await session.commit()
        
        # Verify
        result = await session.execute(select(Patient).where(Patient.name == "Test Patient"))
        db_patient = result.scalar_one()
        assert db_patient.name == "Test Patient"
        assert db_patient.id is not None
        
    await engine.dispose()
