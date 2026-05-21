import pytest
import json
from unittest.mock import MagicMock, patch
from agent.agent import ClinicalAgent

@pytest.fixture
def agent():
    return ClinicalAgent(api_key="test_key")

@pytest.mark.asyncio
async def test_process_request_text_only(agent):
    # Mock Groq response
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content="Hello, how can I help you?", tool_calls=None))
    ]
    
    with patch.object(agent.client.chat.completions, 'create', return_value=mock_response):
        result = await agent.process_request(
            "Hi", 
            session_context={}, 
            patient_history={}, 
            detected_language="English"
        )
        
        assert result["response_text"] == "Hello, how can I help you?"
        assert result["tool_called"] is None
        assert "latency_ms" in result

@pytest.mark.asyncio
async def test_process_request_with_tool_call(agent):
    # Mock Groq response with tool call
    mock_tool_call = MagicMock()
    mock_tool_call.function.name = "check_availability"
    mock_tool_call.function.arguments = json.dumps({"doctor_type": "Dentist", "date": "2024-05-22"})
    
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content=None, tool_calls=[mock_tool_call]))
    ]
    
    with patch.object(agent.client.chat.completions, 'create', return_value=mock_response):
        result = await agent.process_request(
            "I need a dentist for tomorrow", 
            session_context={}, 
            patient_history={}, 
            detected_language="English"
        )
        
        assert result["tool_called"] == "check_availability"
        assert result["tool_result"]["args"]["doctor_type"] == "Dentist"
        assert "checking the details" in result["response_text"]

def test_build_system_prompt(agent):
    prompt = agent._build_system_prompt("Hindi", {"last": "visit"}, {"step": 1})
    assert "Hindi" in prompt
    assert "visit" in prompt
    assert "step" in prompt
