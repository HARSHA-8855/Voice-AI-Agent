import pytest
from datetime import datetime, date, time, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from db.models import Base, Patient, Appointment, DoctorSchedule
from agent.tools import (
    check_availability,
    book_appointment,
    cancel_appointment,
    reschedule_appointment,
    seed_demo_data,
    ClinicalClinicError,
    PatientNotFoundError,
    AppointmentNotFoundError,
    AppointmentConflictError
)

@pytest.mark.asyncio
async def test_tools_full_workflow():
    # 1. Initialize SQLite in-memory async engine
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with async_session() as session:
        # --- TEST DATABASE SEEDING ---
        await seed_demo_data(session)
        
        # Verify seeding populated schedules and patient
        from sqlalchemy import select
        res_sched = await session.execute(select(DoctorSchedule))
        schedules = res_sched.scalars().all()
        assert len(schedules) > 0
        
        res_patient = await session.execute(select(Patient))
        patients = res_patient.scalars().all()
        assert len(patients) == 1
        patient = patients[0]
        assert patient.name == "Harsh Patel"
        patient_id = patient.id

        # --- TEST CHECK AVAILABILITY ---
        today_str = date.today().isoformat()
        tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
        
        # Check availability for Cardiologist (should have slots)
        avail = await check_availability(session, "Cardiologist", tomorrow_str)
        assert avail["status"] == "success"
        assert avail["doctor_type"] == "Cardiologist"
        assert "9:00 AM" in avail["available_slots"]
        
        # Check past date error
        past_date_str = (date.today() - timedelta(days=1)).isoformat()
        with pytest.raises(ClinicalClinicError, match="past"):
            await check_availability(session, "Cardiologist", past_date_str)
            
        # Check invalid doctor type error
        with pytest.raises(ClinicalClinicError, match="department"):
            await check_availability(session, "BrainSurgeon", today_str)

        # --- TEST BOOK APPOINTMENT ---
        # Select an available slot for tomorrow
        target_slot = avail["available_slots"][0] # "9:00 AM"
        
        # Book appointment
        booking = await book_appointment(session, patient_id, "Cardiologist", tomorrow_str, target_slot)
        assert booking["status"] == "success"
        assert booking["doctor_type"] == "Cardiologist"
        assert booking["date"] == tomorrow_str
        assert booking["time"] == target_slot
        appt_id = booking["appointment_id"]
        
        # Verify the slot is now marked as booked
        stmt = select(DoctorSchedule).where(
            DoctorSchedule.doctor_type == "Cardiologist",
            DoctorSchedule.date == date.today() + timedelta(days=1)
        )
        res = await session.execute(stmt)
        sched = res.scalar_one()
        assert "09:00" in sched.booked_slots
        
        # Book already booked slot error (Conflict)
        with pytest.raises(AppointmentConflictError) as exc_info:
            await book_appointment(session, patient_id, "Cardiologist", tomorrow_str, target_slot)
        assert len(exc_info.value.alternatives) > 0
        assert exc_info.value.alternatives[0]["time"] != target_slot

        # --- TEST CANCEL APPOINTMENT ---
        # Cancel appointment
        cancellation = await cancel_appointment(session, appt_id, patient_id)
        assert cancellation["status"] == "success"
        
        # Verify slot is released in schedule
        res = await session.execute(stmt)
        sched = res.scalar_one()
        assert "09:00" not in sched.booked_slots
        
        # Verify appointment status is cancelled
        res_appt = await session.execute(select(Appointment).where(Appointment.id == appt_id))
        appt = res_appt.scalar_one()
        assert appt.status == "cancelled"
        
        # Already cancelled error
        with pytest.raises(ClinicalClinicError, match="cancelled"):
            await cancel_appointment(session, appt_id, patient_id)

        # --- TEST RESCHEDULE APPOINTMENT ---
        # Book a new appointment to reschedule
        booking = await book_appointment(session, patient_id, "Dentist", tomorrow_str, "10:00")
        appt_id_resch = booking["appointment_id"]
        
        # Reschedule to a new slot
        rescheduling = await reschedule_appointment(
            session, appt_id_resch, patient_id, tomorrow_str, "11:30"
        )
        assert rescheduling["status"] == "success"
        assert rescheduling["time"] == "11:30 AM"
        
        # Verify old slot ("10:00") is freed, and new slot ("11:30") is booked
        stmt_dentist = select(DoctorSchedule).where(
            DoctorSchedule.doctor_type == "Dentist",
            DoctorSchedule.date == date.today() + timedelta(days=1)
        )
        res = await session.execute(stmt_dentist)
        sched_dentist = res.scalar_one()
        assert "10:00" not in sched_dentist.booked_slots
        assert "11:30" in sched_dentist.booked_slots
        
        # Verify appointment record is updated
        res_appt = await session.execute(select(Appointment).where(Appointment.id == appt_id_resch))
        appt = res_appt.scalar_one()
        assert appt.time.strftime("%H:%M") == "11:30"
        assert appt.status == "rescheduled"
        
    await engine.dispose()


@pytest.mark.asyncio
async def test_patient_double_booking_conflict():
    # Initialize SQLite in-memory async engine
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with async_session() as session:
        await seed_demo_data(session)
        
        # Fetch patient
        from sqlalchemy import select
        res_patient = await session.execute(select(Patient))
        patient = res_patient.scalars().first()
        patient_id = patient.id
        
        tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
        
        # 1. Book first appointment at 10:00 AM with Dentist
        booking1 = await book_appointment(session, patient_id, "Dentist", tomorrow_str, "10:00 AM")
        assert booking1["status"] == "success"
        
        # 2. Attempt to book another appointment at 10:00 AM with Cardiologist for the same patient
        # This must raise AppointmentConflictError owing to patient double-booking!
        with pytest.raises(AppointmentConflictError) as exc_info:
            await book_appointment(session, patient_id, "Cardiologist", tomorrow_str, "10:00 AM")
            
        assert "already have an appointment" in str(exc_info.value)
        assert len(exc_info.value.alternatives) > 0
        
    await engine.dispose()
