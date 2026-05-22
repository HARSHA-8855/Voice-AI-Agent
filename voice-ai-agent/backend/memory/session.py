import os
import json
import logging
from typing import Dict, Any, Optional
import redis.asyncio as aioredis

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SessionMemory:
    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis = aioredis.from_url(self.redis_url, decode_responses=True)
        self.ttl = 1800  # 30 minutes in seconds
        logger.info(f"Initialized SessionMemory with Redis URL: {self.redis_url}")

    def _get_key(self, session_id: str) -> str:
        return f"session:{session_id}"

    def _get_default_session(self) -> Dict[str, Any]:
        return {
            "intent": None,
            "doctor_type": None,
            "date": None,
            "time": None,
            "language": "en",
            "turn_count": 0
        }

    async def get_session(self, session_id: str) -> Dict[str, Any]:
        """
        Retrieves the session state from Redis.
        Auto-refreshes the 30-minute expiration upon access.
        """
        key = self._get_key(session_id)
        try:
            data = await self.redis.get(key)
            if data:
                session = json.loads(data)
                # Refresh TTL on active access
                await self.redis.expire(key, self.ttl)
                logger.info(f"Retrieved session {session_id} (Refreshed TTL)")
                return session
            
            # If session doesn't exist, return default structure
            logger.info(f"Session {session_id} not found. Returning default state.")
            return self._get_default_session()
        except Exception as e:
            logger.error(f"Error reading session {session_id} from Redis: {e}")
            return self._get_default_session()

    async def update_session(self, session_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Updates specific keys in the session data or writes a new session state.
        Resets the 30-minute expiration window.
        """
        key = self._get_key(session_id)
        try:
            current_session = await self.get_session(session_id)
            # Update specific keys
            current_session.update(data)
            
            # Save back to Redis with TTL
            await self.redis.set(key, json.dumps(current_session), ex=self.ttl)
            logger.info(f"Updated session {session_id}: {data}")
            return current_session
        except Exception as e:
            logger.error(f"Error updating session {session_id} in Redis: {e}")
            return self._get_default_session()

    async def clear_session(self, session_id: str) -> bool:
        """
        Deletes the session data from Redis.
        """
        key = self._get_key(session_id)
        try:
            deleted = await self.redis.delete(key)
            logger.info(f"Cleared session {session_id}. Success: {deleted > 0}")
            return deleted > 0
        except Exception as e:
            logger.error(f"Error clearing session {session_id} in Redis: {e}")
            return False

    async def close(self):
        """
        Closes the Redis connection pool.
        """
        await self.redis.close()
