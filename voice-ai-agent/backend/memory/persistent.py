class PersistentMemory:
    def __init__(self, db_session):
        self.db = db_session

    async def get_patient_history(self, patient_id):
        pass
