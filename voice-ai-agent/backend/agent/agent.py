import os
import json
import time
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from groq import AsyncGroq

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ClinicalAgent:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.client = AsyncGroq(api_key=self.api_key)
        self.model = "llama-3.1-70b-versatile"
        
        # Tool Schemas for Groq
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "check_availability",
                    "description": "Check available appointment slots for a specific doctor type and date.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "doctor_type": {
                                "type": "string",
                                "description": "Type of doctor (e.g., General Physician, Cardiologist, Dentist)"
                            },
                            "date": {
                                "type": "string",
                                "description": "Date to check availability for (YYYY-MM-DD)"
                            }
                        },
                        "required": ["doctor_type", "date"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "book_appointment",
                    "description": "Book a new appointment for a patient.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "patient_id": {"type": "string"},
                            "doctor_type": {"type": "string"},
                            "date": {"type": "string", "description": "YYYY-MM-DD"},
                            "time": {"type": "string", "description": "HH:MM"}
                        },
                        "required": ["patient_id", "doctor_type", "date", "time"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "cancel_appointment",
                    "description": "Cancel an existing appointment.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "appointment_id": {"type": "string"}
                        },
                        "required": ["appointment_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "reschedule_appointment",
                    "description": "Reschedule an existing appointment to a new date and time.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "appointment_id": {"type": "string"},
                            "new_date": {"type": "string", "description": "YYYY-MM-DD"},
                            "new_time": {"type": "string", "description": "HH:MM"}
                        },
                        "required": ["appointment_id", "new_date", "new_time"]
                    }
                }
            }
        ]

    def _build_system_prompt(self, language: str, history: Dict, session: Dict) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        prompt = f"""You are a healthcare appointment assistant for an Indian clinic.
Current language: {language}. Respond ONLY in this language.
Patient history: {json.dumps(history)}
Current session state: {json.dumps(session)}
Today's date: {today}
Available actions: book, cancel, reschedule, check availability.
Always confirm details before booking. Suggest alternatives on conflict."""
        return prompt

    async def process_request(
        self, 
        user_text: str, 
        session_context: Dict, 
        patient_history: Dict, 
        detected_language: str
    ) -> Dict[str, Any]:
        """
        Processes a user request using Groq LLM with tool-calling.
        """
        start_time = time.perf_counter()
        
        system_prompt = self._build_system_prompt(detected_language, patient_history, session_context)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ]

        try:
            # 1. First LLM call to see if tools are needed
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.tools,
                tool_choice="auto",
                temperature=0.2
            )
            
            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls
            
            result_data = {
                "response_text": response_message.content or "",
                "tool_called": None,
                "tool_result": None,
                "latency_ms": 0
            }

            if tool_calls:
                # For this implementation, we handle the first tool call
                tool_call = tool_calls[0]
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                logger.info(f"Reasoning: User requested action requiring {function_name} with args {function_args}")
                
                result_data["tool_called"] = function_name
                
                # Mock tool execution (In reality, these would call backend/agent/tools.py)
                # Since we are just building the agent here, we simulate a successful tool result
                # but in a real app, this would be passed to the tools.py functions.
                result_data["tool_result"] = {
                    "status": "success",
                    "message": f"Simulated execution of {function_name}",
                    "args": function_args
                }
                
                # Usually, we'd feed the tool result back to the LLM for a final response,
                # but the requirement asks to return the tool_called and tool_result directly.
                # If no content was generated (common with tool calls), we might need a second call
                # or just describe the intent.
                if not result_data["response_text"]:
                    result_data["response_text"] = f"I am checking the details for your {function_name.replace('_', ' ')} request."

            latency_ms = int((time.perf_counter() - start_time) * 1000)
            result_data["latency_ms"] = latency_ms
            
            logger.info(f"LLM Latency: {latency_ms}ms")
            return result_data

        except Exception as e:
            logger.error(f"Error in ClinicalAgent: {e}")
            return {
                "response_text": "I'm sorry, I'm having trouble processing that right now.",
                "tool_called": None,
                "tool_result": {"error": str(e)},
                "latency_ms": int((time.perf_counter() - start_time) * 1000)
            }

# Simple test function
if __name__ == "__main__":
    import asyncio
    
    async def test():
        agent = ClinicalAgent()
        history = {"last_visit": "2024-01-10", "condition": "Dental checkup"}
        session = {"current_step": "greeting"}
        
        print("Testing check availability...")
        res = await agent.process_request(
            "I want to book an appointment with a dentist tomorrow",
            session,
            history,
            "English"
        )
        print(json.dumps(res, indent=2))
        
        print("\nTesting Hindi response...")
        res_hi = await agent.process_request(
            "कल के लिए डेंटिस्ट के साथ अपॉइंटमेंट बुक करें",
            session,
            history,
            "Hindi"
        )
        print(json.dumps(res_hi, indent=2))

    asyncio.run(test())
