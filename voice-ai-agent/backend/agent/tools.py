import logging
from datetime import datetime, date, time, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Patient, Appointment, DoctorSchedule

# Configure logging
logger = logging.getLogger(__name__)

# --- Custom Clinical Clinic Exceptions ---

class ClinicalClinicError(Exception):
    """Base exception for all clinical clinic errors with patient-friendly messages."""
    pass

class PatientNotFoundError(ClinicalClinicError):
    """Raised when a patient cannot be found in the system."""
    pass

class AppointmentNotFoundError(ClinicalClinicError):
    """Raised when an appointment is not found."""
    pass

class AvailabilityError(ClinicalClinicError):
    """Raised when there are errors checking slot availability."""
    pass

class AppointmentConflictError(ClinicalClinicError):
    """Raised when there is a booking conflict, containing alternative slot suggestion details."""
    def __init__(self, message: str, alternatives: List[Dict[str, Any]]):
        super().__init__(message)
        self.alternatives = alternatives

# --- Internal Helper Functions ---

async def _get_alternative_slots(
    db: AsyncSession,
    doctor_type: str,
    start_date: date,
    start_time: time,
    limit: int = 3
) -> List[Dict[str, Any]]:
    """
    Finds the next N alternative available slots chronologically.
    Filters out any past slots or currently booked slots.
    """
    # Query schedules for the doctor_type on or after start_date
    stmt = (
        select(DoctorSchedule)
        .where(
            DoctorSchedule.doctor_type == doctor_type,
            DoctorSchedule.date >= start_date
        )
        .order_by(DoctorSchedule.date.asc())
    )
    result = await db.execute(stmt)
    schedules = result.scalars().all()
    
    alternatives = []
    current_now = datetime.now()

    for sched in schedules:
        for slot in sched.available_slots:
            if slot in sched.booked_slots:
                continue
                
            slot_time = datetime.strptime(slot, "%H:%M").time()
            
            # If same day, must be after the requested start_time
            if sched.date == start_date and slot_time <= start_time:
                continue
                
            # Filter out any slot in the past relative to system time
            slot_datetime = datetime.combine(sched.date, slot_time)
            if slot_datetime < current_now:
                continue
                
            alternatives.append({
                "date": sched.date.isoformat(),
                "time": slot
            })
            if len(alternatives) == limit:
                return alternatives
                
    # If not enough alternatives from the immediate start window, fall back to future days chronologically
    if len(alternatives) < limit:
        stmt = (
            select(DoctorSchedule)
            .where(
                DoctorSchedule.doctor_type == doctor_type,
                DoctorSchedule.date > start_date
            )
            .order_by(DoctorSchedule.date.asc())
        )
        result = await db.execute(stmt)
        schedules = result.scalars().all()
        for sched in schedules:
            for slot in sched.available_slots:
                if slot in sched.booked_slots:
                    continue
                slot_time = datetime.strptime(slot, "%H:%M").time()
                slot_datetime = datetime.combine(sched.date, slot_time)
                if slot_datetime < current_now:
                    continue
                
                candidate = {"date": sched.date.isoformat(), "time": slot}
                if candidate not in alternatives:
                    alternatives.append(candidate)
                    if len(alternatives) == limit:
                        return alternatives
                        
    return alternatives

# --- Core Appointment Tools ---

async def check_availability(db: AsyncSession, doctor_type: str, date_str: str) -> Dict[str, Any]:
    """
    Checks available slots for a given doctor type and date.
    If no slots are available, returns the next 3 available dates containing free slots.
    """
    # 1. Parse date string
    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise ClinicalClinicError("The provided date format is invalid. Please use YYYY-MM-DD format.")

    # 2. Check if date is in the past
    if parsed_date < date.today():
        raise ClinicalClinicError("Cannot check availability for a date in the past.")

    # 3. Verify doctor type exists in the database
    valid_stmt = select(DoctorSchedule.doctor_type).distinct()
    valid_res = await db.execute(valid_stmt)
    valid_types = valid_res.scalars().all()
    if valid_types and doctor_type not in valid_types:
        raise ClinicalClinicError(
            f"We do not have a {doctor_type} department in our clinic. "
            f"Available departments are: {', '.join(valid_types)}."
        )

    # 4. Query schedule for the specific date
    stmt = select(DoctorSchedule).where(
        DoctorSchedule.doctor_type == doctor_type,
        DoctorSchedule.date == parsed_date
    )
    result = await db.execute(stmt)
    schedule = result.scalar_one_or_none()

    # 5. Extract and filter free slots
    free_slots = []
    if schedule:
        current_now = datetime.now()
        for slot in schedule.available_slots:
            if slot not in schedule.booked_slots:
                # Double check that the slot is not in the past if it is today
                slot_time = datetime.strptime(slot, "%H:%M").time()
                slot_datetime = datetime.combine(parsed_date, slot_time)
                if slot_datetime >= current_now:
                    free_slots.append(slot)

    # 6. Return slots or alternative future dates
    if free_slots:
        logger.info(f"Availability check for {doctor_type} on {date_str}: {len(free_slots)} slots found.")
        return {
            "status": "success",
            "doctor_type": doctor_type,
            "date": date_str,
            "available_slots": free_slots
        }
    else:
        logger.info(f"No slots available for {doctor_type} on {date_str}. Querying future alternatives.")
        # Find next 3 available dates with free slots
        stmt = (
            select(DoctorSchedule)
            .where(
                DoctorSchedule.doctor_type == doctor_type,
                DoctorSchedule.date > parsed_date
            )
            .order_by(DoctorSchedule.date.asc())
        )
        result = await db.execute(stmt)
        future_schedules = result.scalars().all()
        
        alternative_dates = []
        for f_sched in future_schedules:
            f_free = [s for s in f_sched.available_slots if s not in f_sched.booked_slots]
            # Ensure slots aren't past (safety check)
            f_valid = []
            for s in f_free:
                s_time = datetime.strptime(s, "%H:%M").time()
                if datetime.combine(f_sched.date, s_time) >= datetime.now():
                    f_valid.append(s)
            
            if f_valid:
                alternative_dates.append({
                    "date": f_sched.date.isoformat(),
                    "slots": f_valid
                })
                if len(alternative_dates) == 3:
                    break
                    
        return {
            "status": "no_availability",
            "message": f"No available slots for a {doctor_type} on {date_str}.",
            "alternative_dates": alternative_dates
        }

async def book_appointment(
    db: AsyncSession,
    patient_id: int,
    doctor_type: str,
    date_str: str,
    time_str: str
) -> Dict[str, Any]:
    """
    Books an appointment for a patient.
    Validates slot timing, schedule existence, and double bookings.
    On conflict, raises AppointmentConflictError containing alternative slots.
    """
    # 1. Parse and validate date and time format
    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        parsed_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        raise ClinicalClinicError("Invalid date or time format. Use YYYY-MM-DD for date and HH:MM for time.")

    # 2. Check if the slot is in the past
    requested_datetime = datetime.combine(parsed_date, parsed_time)
    if requested_datetime < datetime.now():
        raise ClinicalClinicError("Cannot book an appointment in the past.")

    # 3. Verify patient exists
    patient_stmt = select(Patient).where(Patient.id == patient_id)
    patient_res = await db.execute(patient_stmt)
    patient = patient_res.scalar_one_or_none()
    if not patient:
        raise PatientNotFoundError(f"Patient with ID {patient_id} does not exist.")

    # 4. Fetch the doctor's schedule for that date
    sched_stmt = select(DoctorSchedule).where(
        DoctorSchedule.doctor_type == doctor_type,
        DoctorSchedule.date == parsed_date
    )
    sched_res = await db.execute(sched_stmt)
    schedule = sched_res.scalar_one_or_none()

    # 5. Conflict checks: schedule not found, slot doesn't exist, or slot is already booked
    is_conflict = False
    error_msg = ""
    
    if not schedule:
        is_conflict = True
        error_msg = f"No schedule exists for a {doctor_type} on {date_str}."
    elif time_str not in schedule.available_slots:
        is_conflict = True
        error_msg = f"The time slot {time_str} is not a valid clinic slot for a {doctor_type} on {date_str}."
    elif time_str in schedule.booked_slots:
        is_conflict = True
        error_msg = f"The requested time slot {time_str} is already booked for a {doctor_type} on {date_str}."

    if is_conflict:
        # Search for 3 alternative slots
        alternatives = await _get_alternative_slots(db, doctor_type, parsed_date, parsed_time)
        raise AppointmentConflictError(error_msg, alternatives)

    # 6. Book slot: Update booked_slots list
    updated_booked = list(schedule.booked_slots)
    updated_booked.append(time_str)
    schedule.booked_slots = updated_booked

    # 7. Write to appointments table
    new_appt = Appointment(
        patient_id=patient_id,
        doctor_type=doctor_type,
        date=parsed_date,
        time=parsed_time,
        status="scheduled"
    )
    db.add(new_appt)
    await db.commit()
    await db.refresh(new_appt)

    logger.info(f"Booked appointment ID {new_appt.id} for patient {patient_id} on {date_str} at {time_str}")
    return {
        "status": "success",
        "appointment_id": new_appt.id,
        "patient_name": patient.name,
        "doctor_type": doctor_type,
        "date": date_str,
        "time": time_str,
        "confirmation_message": f"Appointment successfully scheduled with a {doctor_type} on {date_str} at {time_str}."
    }

async def cancel_appointment(db: AsyncSession, appointment_id: int, patient_id: int) -> Dict[str, Any]:
    """
    Cancels an existing appointment.
    Ensures ownership belonging to the requesting patient and frees up the booked slot in the schedule.
    """
    # 1. Retrieve the appointment
    appt_stmt = select(Appointment).where(Appointment.id == appointment_id)
    appt_res = await db.execute(appt_stmt)
    appointment = appt_res.scalar_one_or_none()

    if not appointment:
        raise AppointmentNotFoundError(f"Appointment with ID {appointment_id} could not be found.")

    # 2. Check ownership and current status
    if appointment.patient_id != patient_id:
        raise ClinicalClinicError("Access denied. This appointment does not belong to you.")
        
    if appointment.status == "cancelled":
        raise ClinicalClinicError("This appointment has already been cancelled.")

    # 3. Update appointment status
    appointment.status = "cancelled"

    # 4. Free the slot in the doctor schedule
    sched_stmt = select(DoctorSchedule).where(
        DoctorSchedule.doctor_type == appointment.doctor_type,
        DoctorSchedule.date == appointment.date
    )
    sched_res = await db.execute(sched_stmt)
    schedule = sched_res.scalar_one_or_none()

    if schedule:
        time_str = appointment.time.strftime("%H:%M")
        if time_str in schedule.booked_slots:
            updated_booked = [slot for slot in schedule.booked_slots if slot != time_str]
            schedule.booked_slots = updated_booked
            logger.info(f"Released time slot {time_str} for {appointment.doctor_type} on {appointment.date.isoformat()}")

    await db.commit()
    logger.info(f"Cancelled appointment ID {appointment_id} for patient {patient_id}")
    return {
        "status": "success",
        "message": "Your appointment has been successfully cancelled.",
        "appointment_id": appointment_id
    }

async def reschedule_appointment(
    db: AsyncSession,
    appointment_id: int,
    patient_id: int,
    new_date_str: str,
    new_time_str: str
) -> Dict[str, Any]:
    """
    Reschedules an existing appointment to a new date and time atomically.
    Validates availability of the new slot, releasing the old slot temporarily to avoid self-collision.
    """
    # 1. Retrieve the original appointment
    appt_stmt = select(Appointment).where(Appointment.id == appointment_id)
    appt_res = await db.execute(appt_stmt)
    appointment = appt_res.scalar_one_or_none()

    if not appointment:
        raise AppointmentNotFoundError(f"Appointment with ID {appointment_id} could not be found.")

    # 2. Basic ownership and cancellation validations
    if appointment.patient_id != patient_id:
        raise ClinicalClinicError("Access denied. This appointment does not belong to you.")
        
    if appointment.status == "cancelled":
        raise ClinicalClinicError("Cannot reschedule an appointment that has already been cancelled.")

    # Parse and validate new date/time format
    try:
        parsed_new_date = datetime.strptime(new_date_str, "%Y-%m-%d").date()
        parsed_new_time = datetime.strptime(new_time_str, "%H:%M").time()
    except ValueError:
        raise ClinicalClinicError("Invalid date or time format. Use YYYY-MM-DD for date and HH:MM for time.")

    # Verify slot is not in the past
    new_requested_datetime = datetime.combine(parsed_new_date, parsed_new_time)
    if new_requested_datetime < datetime.now():
        raise ClinicalClinicError("Cannot reschedule an appointment to a slot in the past.")

    # Save original details in case of rollback/restoration
    old_doctor_type = appointment.doctor_type
    old_date = appointment.date
    old_time_str = appointment.time.strftime("%H:%M")

    # 3. Release the old slot temporarily in the database session
    old_sched_stmt = select(DoctorSchedule).where(
        DoctorSchedule.doctor_type == old_doctor_type,
        DoctorSchedule.date == old_date
    )
    old_sched_res = await db.execute(old_sched_stmt)
    old_schedule = old_sched_res.scalar_one_or_none()
    
    if old_schedule and old_time_str in old_schedule.booked_slots:
        old_schedule.booked_slots = [slot for slot in old_schedule.booked_slots if slot != old_time_str]

    # 4. Fetch the schedule for the new slot
    new_sched_stmt = select(DoctorSchedule).where(
        DoctorSchedule.doctor_type == old_doctor_type,
        DoctorSchedule.date == parsed_new_date
    )
    new_sched_res = await db.execute(new_sched_stmt)
    new_schedule = new_sched_res.scalar_one_or_none()

    # 5. Check conflicts on the new slot
    is_conflict = False
    error_msg = ""

    if not new_schedule:
        is_conflict = True
        error_msg = f"No schedule exists for a {old_doctor_type} on {new_date_str}."
    elif new_time_str not in new_schedule.available_slots:
        is_conflict = True
        error_msg = f"The time slot {new_time_str} is not a valid clinic slot for a {old_doctor_type} on {new_date_str}."
    elif new_time_str in new_schedule.booked_slots:
        is_conflict = True
        error_msg = f"The requested time slot {new_time_str} is already booked for a {old_doctor_type} on {new_date_str}."

    if is_conflict:
        # Roll back released old slot before raising conflict error
        await db.rollback()
        # Look for alternative slots chronologically starting from the requested time
        alternatives = await _get_alternative_slots(db, old_doctor_type, parsed_new_date, parsed_new_time)
        raise AppointmentConflictError(error_msg, alternatives)

    # 6. Apply new booking slot updates
    updated_new_booked = list(new_schedule.booked_slots)
    updated_new_booked.append(new_time_str)
    new_schedule.booked_slots = updated_new_booked

    # 7. Update appointment record details
    appointment.date = parsed_new_date
    appointment.time = parsed_new_time
    appointment.status = "rescheduled"

    await db.commit()
    await db.refresh(appointment)

    logger.info(f"Rescheduled appointment ID {appointment_id} to {new_date_str} at {new_time_str}")
    return {
        "status": "success",
        "appointment_id": appointment.id,
        "doctor_type": old_doctor_type,
        "date": new_date_str,
        "time": new_time_str,
        "confirmation_message": f"Appointment successfully rescheduled to {new_date_str} at {new_time_str}."
    }

# --- Database Seeding Function for Demo Purposes ---

async def seed_demo_data(db: AsyncSession) -> None:
    """
    Seeds the database with three doctor types and schedule slots for demo and test purposes.
    Sets schedules starting from today up to the next 5 days.
    """
    # 1. Check if schedule data already exists
    stmt = select(DoctorSchedule).limit(1)
    res = await db.execute(stmt)
    if res.scalar_one_or_none() is not None:
        logger.info("Database already seeded with doctor schedules.")
        return

    logger.info("Seeding database with sample doctor schedules and clinic details...")
    today = date.today()
    
    # Generate schedule slots for the next 6 days (today + 5 days)
    for dt in range(6):
        target_date = today + timedelta(days=dt)
        
        # Seed Cardiologist
        slots_cardio = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "14:00", "14:30", "15:00"]
        booked_cardio = ["09:30"] if dt % 2 == 0 else []
        db.add(DoctorSchedule(
            doctor_type="Cardiologist",
            date=target_date,
            available_slots=slots_cardio,
            booked_slots=booked_cardio
        ))
        
        # Seed Dentist
        slots_dentist = ["10:00", "10:30", "11:00", "11:30", "12:00", "15:00", "15:30", "16:00"]
        booked_dentist = ["11:00", "15:30"] if dt % 3 == 0 else ["10:30"]
        db.add(DoctorSchedule(
            doctor_type="Dentist",
            date=target_date,
            available_slots=slots_dentist,
            booked_slots=booked_dentist
        ))
        
        # Seed General Physician
        slots_gp = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00"]
        booked_gp = ["12:00"] if dt % 2 == 1 else []
        db.add(DoctorSchedule(
            doctor_type="General Physician",
            date=target_date,
            available_slots=slots_gp,
            booked_slots=booked_gp
        ))

    # 2. Check if a sample patient exists, if not seed one
    stmt_patient = select(Patient).limit(1)
    res_patient = await db.execute(stmt_patient)
    if res_patient.scalar_one_or_none() is None:
        logger.info("Seeding sample patient data...")
        db.add(Patient(
            name="Harsh Patel",
            phone="+919876543210",
            preferred_language="en"
        ))
        
    await db.commit()
    logger.info("Successfully seeded database with clinical scheduling and patient details.")
