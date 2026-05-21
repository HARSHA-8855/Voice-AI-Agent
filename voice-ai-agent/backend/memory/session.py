import redis

class SessionMemory:
    def __init__(self, redis_url):
        self.redis = redis.from_url(redis_url)

    def get_session(self, session_id):
        pass

    def update_session(self, session_id, data):
        pass
