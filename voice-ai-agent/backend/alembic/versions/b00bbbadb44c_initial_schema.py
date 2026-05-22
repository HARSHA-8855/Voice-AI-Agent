"""Initial schema

Revision ID: b00bbbadb44c
Revises: 
Create Date: 2026-05-21 13:18:33.958020

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b00bbbadb44c'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Create patients table
    op.create_table(
        'patients',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=False),
        sa.Column('preferred_language', sa.String(length=10), nullable=True, server_default='en'),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_patients_phone'), 'patients', ['phone'], unique=True)

    # 2. Create appointments table
    op.create_table(
        'appointments',
        sa.Column('id', sa.Integer(), sa.Identity(always=False), autoincrement=True, nullable=False),
        sa.Column('patient_id', sa.Integer(), nullable=False),
        sa.Column('doctor_type', sa.String(length=50), nullable=False),
        sa.Column('doctor_id', sa.String(length=50), nullable=True),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('time', sa.Time(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=True, server_default='scheduled'),
        sa.Column('reminder_sent', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_appointments_patient_id'), 'appointments', ['patient_id'], unique=False)
    op.create_index(op.f('ix_appointments_date'), 'appointments', ['date'], unique=False)

    # 3. Create doctor_schedule table
    op.create_table(
        'doctor_schedule',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('doctor_type', sa.String(length=50), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('available_slots', sa.JSON(), nullable=False),
        sa.Column('booked_slots', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_doctor_schedule_doctor_type'), 'doctor_schedule', ['doctor_type'], unique=False)
    op.create_index(op.f('ix_doctor_schedule_date'), 'doctor_schedule', ['date'], unique=False)

    # 4. Create interaction_logs table
    op.create_table(
        'interaction_logs',
        sa.Column('id', sa.Integer(), sa.Identity(always=False), autoincrement=True, nullable=False),
        sa.Column('patient_id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.String(length=100), nullable=False),
        sa.Column('summary', sa.String(length=500), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_interaction_logs_patient_id'), 'interaction_logs', ['patient_id'], unique=False)
    op.create_index(op.f('ix_interaction_logs_session_id'), 'interaction_logs', ['session_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_interaction_logs_session_id'), table_name='interaction_logs')
    op.drop_index(op.f('ix_interaction_logs_patient_id'), table_name='interaction_logs')
    op.drop_table('interaction_logs')
    
    op.drop_index(op.f('ix_doctor_schedule_date'), table_name='doctor_schedule')
    op.drop_index(op.f('ix_doctor_schedule_doctor_type'), table_name='doctor_schedule')
    op.drop_table('doctor_schedule')
    
    op.drop_index(op.f('ix_appointments_date'), table_name='appointments')
    op.drop_index(op.f('ix_appointments_patient_id'), table_name='appointments')
    op.drop_table('appointments')
    
    op.drop_index(op.f('ix_patients_phone'), table_name='patients')
    op.drop_table('patients')
