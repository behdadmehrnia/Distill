from fastapi import FastAPI, Request, Form, Body, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
import httpx
import json
import uuid
from pydantic import BaseModel
from typing import Dict, Optional
from datetime import datetime
import asyncio

app = FastAPI()

# Pydantic models for request validation
class ConfigRequest(BaseModel):
    api_url: str
    api_key: str

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

# Store Dify configuration
DIFY_CONFIG = {
    "api_key": "app-MOdwbliMghRXL7aXUBy47ZI0",
    "api_url": "https://llm.internal.example/v1/chat-messages"
}

# Store conversations with session_id as key
CONVERSATIONS: Dict[str, Dict] = {}

# HTML template as a string
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Dify LLM Chat</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            text-align: center;
        }
        h2 {
            color: #555;
            font-size: 16px;
            margin: 10px 0;
        }
        .config-section {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 5px;
            margin-bottom: 20px;
        }
        .config-section h3 {
            margin-top: 0;
        }
        input, textarea, button {
            width: 100%;
            padding: 12px;
            margin: 8px 0;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 16px;
            box-sizing: border-box;
        }
        textarea {
            height: 120px;
            resize: vertical;
        }
        button {
            background-color: #007bff;
            color: white;
            border: none;
            cursor: pointer;
            font-weight: bold;
            transition: background-color 0.3s;
        }
        button:hover {
            background-color: #0056b3;
        }
        button.secondary {
            background-color: #6c757d;
            margin-top: 5px;
        }
        button.secondary:hover {
            background-color: #545b62;
        }
        .response-section {
            margin-top: 30px;
            padding: 20px;
            background: #e9ecef;
            border-radius: 5px;
            white-space: pre-wrap;
            word-wrap: break-word;
            max-height: 400px;
            overflow-y: auto;
        }
        .loading {
            display: none;
            text-align: center;
            color: #007bff;
            margin: 10px 0;
        }
        .status {
            padding: 10px;
            border-radius: 5px;
            margin: 10px 0;
            text-align: center;
            display: none;
        }
        .success {
            background-color: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        .error {
            background-color: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        .info {
            background-color: #d1ecf1;
            color: #0c5460;
            border: 1px solid #bee5eb;
        }
        .session-info {
            background-color: #e2e3e5;
            padding: 10px;
            border-radius: 5px;
            margin: 10px 0;
            font-size: 14px;
        }
        .conversation-history {
            margin-top: 20px;
            border-top: 1px solid #ddd;
            padding-top: 15px;
        }
        .message {
            padding: 10px;
            margin: 5px 0;
            border-radius: 5px;
        }
        .user-message {
            background-color: #d1e7ff;
            margin-left: 20px;
        }
        .assistant-message {
            background-color: #f8f9fa;
            margin-right: 20px;
        }
        .timestamp {
            font-size: 12px;
            color: #666;
            float: right;
        }
        .conversation-controls {
            display: flex;
            gap: 10px;
            margin-bottom: 10px;
        }
        .conversation-controls button {
            flex: 1;
        }
        .hidden {
            display: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Dify LLM Chat Interface</h1>
        
        <div class="session-info">
            <strong>Session ID:</strong> <span id="sessionId">Loading...</span>
            <button class="secondary" onclick="copySessionId()" style="width: auto; padding: 5px 10px; float: right;">Copy Session ID</button>
            <div style="clear: both;"></div>
            <div id="conversationStatus">No active conversation</div>
        </div>
        
        <div class="config-section">
            <h3>🔧 Configuration</h3>
            <div id="configStatus" class="status"></div>
            <input type="text" id="apiUrl" placeholder="Enter Dify API URL (e.g., https://api.dify.ai/v1/chat-messages)" value="{{ api_url }}">
            <input type="password" id="apiKey" placeholder="Enter Dify API Key" value="{{ api_key }}">
            <button onclick="saveConfig()">Save Configuration</button>
            <div id="configInfo" class="status info">
                💡 Get your API key from Dify → Applications → API Access
            </div>
        </div>
        
        <div class="chat-section">
            <h3>💬 Chat with LLM</h3>
            <div class="conversation-controls">
                <button class="secondary" onclick="startNewConversation()">🔄 New Conversation</button>
                <button class="secondary" onclick="loadConversationHistory()">📋 Show History</button>
            </div>
            <textarea id="userInput" placeholder="Enter your message here..."></textarea>
            <button onclick="sendToDify()">Send to Dify LLM</button>
            <div id="loading" class="loading">⏳ Processing your request...</div>
        </div>
        
        <div id="historySection" class="conversation-history hidden">
            <h3>📜 Conversation History</h3>
            <div id="conversationHistory"></div>
        </div>
        
        <div class="response-section">
            <h3>📄 Response</h3>
            <div id="response">Enter a message and click "Send to Dify LLM"</div>
        </div>
    </div>

    <script>
        let sessionId = '';
        let conversationId = '';
        
        // Generate or load session ID
        function initializeSession() {
            sessionId = localStorage.getItem('dify_session_id');
            if (!sessionId) {
                sessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
                localStorage.setItem('dify_session_id', sessionId);
            }
            document.getElementById('sessionId').textContent = sessionId;
            
            // Load saved conversation ID for this session
            conversationId = localStorage.getItem(`dify_conversation_${sessionId}`);
            updateConversationStatus();
        }
        
        function updateConversationStatus() {
            const statusDiv = document.getElementById('conversationStatus');
            if (conversationId) {
                statusDiv.innerHTML = `<span style="color: green;">✓ Active conversation: ${conversationId.substring(0, 20)}...</span>`;
            } else {
                statusDiv.innerHTML = `<span style="color: orange;">No active conversation - a new one will be created</span>`;
            }
        }
        
        function copySessionId() {
            navigator.clipboard.writeText(sessionId).then(() => {
                alert('Session ID copied to clipboard!');
            });
        }
        
        // Show config info on load
        document.getElementById('configInfo').style.display = 'block';
        
        function showStatus(elementId, message, isError = false) {
            const element = document.getElementById(elementId);
            element.textContent = message;
            element.className = 'status ' + (isError ? 'error' : 'success');
            element.style.display = 'block';
            setTimeout(() => {
                element.style.display = 'none';
            }, 5000);
        }

        async function saveConfig() {
            const apiUrl = document.getElementById('apiUrl').value.trim();
            const apiKey = document.getElementById('apiKey').value.trim();
            
            if (!apiUrl || !apiKey) {
                showStatus('configStatus', '❌ Please enter both API URL and API Key', true);
                return;
            }
            
            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        api_url: apiUrl,
                        api_key: apiKey
                    })
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    showStatus('configStatus', '✅ Configuration saved successfully!', false);
                } else {
                    showStatus('configStatus', `❌ ${data.detail || 'Failed to save configuration'}`, true);
                }
            } catch (error) {
                showStatus('configStatus', '❌ Network error: ' + error.message, true);
            }
        }

        async function sendToDify() {
            const userInput = document.getElementById('userInput').value.trim();
            const responseDiv = document.getElementById('response');
            const loadingDiv = document.getElementById('loading');
            
            if (!userInput) {
                responseDiv.innerHTML = '<div class="error">Please enter a message</div>';
                return;
            }
            
            loadingDiv.style.display = 'block';
            responseDiv.innerHTML = '';
            
            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        message: userInput,
                        session_id: sessionId
                    })
                });
                
                const data = await response.json();
                loadingDiv.style.display = 'none';
                
                if (response.ok) {
                    if (data.success) {
                        // Save conversation ID if returned
                        if (data.conversation_id) {
                            conversationId = data.conversation_id;
                            localStorage.setItem(`dify_conversation_${sessionId}`, conversationId);
                            updateConversationStatus();
                        }
                        
                        // Format the response nicely
                        let formattedResponse = data.response;
                        if (typeof formattedResponse === 'object') {
                            formattedResponse = JSON.stringify(formattedResponse, null, 2);
                        }
                        
                        // Add to conversation history display
                        addToHistory('user', userInput);
                        addToHistory('assistant', formattedResponse);
                        
                        responseDiv.innerHTML = `<div class="success">✅ Success!</div>
                                               <pre style="background: white; padding: 15px; border-radius: 5px; margin-top: 10px;">${formattedResponse}</pre>`;
                        
                        // Clear input
                        document.getElementById('userInput').value = '';
                    } else {
                        responseDiv.innerHTML = `<div class="error">❌ Error: ${data.error}</div>`;
                    }
                } else {
                    responseDiv.innerHTML = `<div class="error">❌ HTTP Error: ${response.status}</div>
                                           <pre style="background: #f8f9fa; padding: 10px; margin-top: 10px; border-radius: 5px;">${JSON.stringify(data, null, 2)}</pre>`;
                }
            } catch (error) {
                loadingDiv.style.display = 'none';
                responseDiv.innerHTML = `<div class="error">❌ Network error: ${error.message}</div>`;
            }
        }
        
        async function startNewConversation() {
            // Clear current conversation ID
            conversationId = '';
            localStorage.removeItem(`dify_conversation_${sessionId}`);
            localStorage.removeItem(`dify_session_id`);
            sessionId = '';
            document.getElementById('sessionId').textContent = sessionId;
            updateConversationStatus();
            
            // Clear history display
            document.getElementById('conversationHistory').innerHTML = '';
            document.getElementById('historySection').classList.add('hidden');
            
            showStatus('configStatus', '🔄 New conversation started', false);
            document.getElementById('userInput').focus();
        }
        
        async function loadConversationHistory() {
            try {
                const response = await fetch(`/api/conversation/${sessionId}`);
                const data = await response.json();
                
                const historyDiv = document.getElementById('conversationHistory');
                historyDiv.innerHTML = '';
                
                if (data.success && data.messages && data.messages.length > 0) {
                    data.messages.forEach(msg => {
                        addToHistory(msg.role, msg.content, msg.timestamp, false);
                    });
                    
                    document.getElementById('historySection').classList.remove('hidden');
                } else {
                    historyDiv.innerHTML = '<div class="info">No conversation history found for this session.</div>';
                    document.getElementById('historySection').classList.remove('hidden');
                }
            } catch (error) {
                console.error('Error loading history:', error);
            }
        }
        
        function addToHistory(role, content, timestamp = null, scroll = true) {
            const historyDiv = document.getElementById('conversationHistory');
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${role === 'user' ? 'user-message' : 'assistant-message'}`;
            
            const timeStr = timestamp || new Date().toLocaleTimeString();
            messageDiv.innerHTML = `
                <div><strong>${role === 'user' ? '👤 You' : '🤖 Assistant'}:</strong> ${content}</div>
                <div class="timestamp">${timeStr}</div>
                <div style="clear: both;"></div>
            `;
            
            historyDiv.appendChild(messageDiv);
            
            if (scroll) {
                historyDiv.scrollTop = historyDiv.scrollHeight;
            }
        }
        
        // Allow sending with Ctrl+Enter
        document.getElementById('userInput').addEventListener('keydown', function(e) {
            if (e.ctrlKey && e.key === 'Enter') {
                sendToDify();
            }
        });
        
        // Load saved config on page load
        window.addEventListener('load', function() {
            initializeSession();
            
            fetch('/api/config')
                .then(response => response.json())
                .then(data => {
                    if (data.api_url) {
                        document.getElementById('apiUrl').value = data.api_url;
                    }
                    if (data.api_key) {
                        document.getElementById('apiKey').value = data.api_key;
                    }
                });
        });
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def get_home(request: Request):
    """Serve the main HTML page"""
    # Render template with current config
    html_content = HTML_TEMPLATE.replace(
        "{{ api_url }}", DIFY_CONFIG.get("api_url", "")
    ).replace(
        "{{ api_key }}", DIFY_CONFIG.get("api_key", "")
    )
    
    return HTMLResponse(content=html_content)

@app.get("/api/config")
async def get_config():
    """Get current Dify configuration"""
    return DIFY_CONFIG

@app.post("/api/config")
async def save_config(config: ConfigRequest):
    """Save Dify configuration"""
    DIFY_CONFIG["api_url"] = config.api_url
    DIFY_CONFIG["api_key"] = config.api_key
    return {"message": "Configuration saved successfully", "success": True}

@app.post("/api/chat")
async def chat_with_dify(chat_request: ChatRequest):
    """Send message to Dify LLM and return response"""
    
    # Check if configuration is set
    if not DIFY_CONFIG["api_url"] or not DIFY_CONFIG["api_key"]:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "Please configure API URL and API Key first"
            }
        )
    
    message = chat_request.message
    session_id = chat_request.session_id or "default_session"
    
    if not message:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "Message cannot be empty"
            }
        )
    
    try:
        # Get conversation ID for this session
        if session_id not in CONVERSATIONS:
            CONVERSATIONS[session_id] = {
                "conversation_id": "",
                "messages": []
            }
        
        conversation_data = CONVERSATIONS[session_id]
        
        # Prepare the request for Dify API
        headers = {
            "Authorization": f"Bearer {DIFY_CONFIG['api_key']}",
           "Content-Type": "application/json"
        }
        
        # Dify API payload structure
        payload = {
            "inputs": {},
            "query": message,
            "response_mode": "blocking",
            "conversation_id": conversation_data["conversation_id"],
            "user": session_id
        }
        
        # Add user message to conversation history
        conversation_data["messages"].append({
            "role": "user",
            "content": message,
            "timestamp": datetime.now().isoformat()
        })
        
        print(f"Sending request to: {DIFY_CONFIG['api_url']}")
        print(f"Conversation ID: {conversation_data['conversation_id']}")
        print(f"Session ID: {session_id}")
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                DIFY_CONFIG["api_url"],
                headers=headers,
                json=payload
            )
            
            print(f"Response status: {response.status_code}")
            
            if response.status_code == 200:
                response_data = response.json()
                
                # Extract the response and conversation ID
                llm_response = ""
                new_conversation_id = conversation_data["conversation_id"]
                
                if "answer" in response_data:
                    llm_response = response_data["answer"]
                elif "data" in response_data and "answer" in response_data["data"]:
                    llm_response = response_data["data"]["answer"]
                elif "text" in response_data:
                    llm_response = response_data["text"]
                elif "result" in response_data:
                    llm_response = response_data["result"]
                elif "message" in response_data:
                    llm_response = response_data["message"]
                else:
                    llm_response = response_data
                
                # Try to extract conversation ID from response
                if "conversation_id" in response_data:
                    new_conversation_id = response_data["conversation_id"]
                elif "data" in response_data and "conversation_id" in response_data["data"]:
                    new_conversation_id = response_data["data"]["conversation_id"]
                
                # Update conversation ID if we got a new one
                if new_conversation_id and new_conversation_id != conversation_data["conversation_id"]:
                    conversation_data["conversation_id"] = new_conversation_id
                    print(f"Updated conversation ID to: {new_conversation_id}")
                
                # Add assistant response to conversation history
                conversation_data["messages"].append({
                    "role": "assistant",
                    "content": llm_response,
                    "timestamp": datetime.now().isoformat()
                })
                
                # Update the conversation storage
                CONVERSATIONS[session_id] = conversation_data
                
                return {
                    "success": True,
                    "response": llm_response,
                    "conversation_id": conversation_data["conversation_id"],
                    "session_id": session_id,
                    "message_count": len(conversation_data["messages"])
                }
            else:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = error_json.get("message", error_json.get("error", error_detail))
                except:
                    pass
                    
                return JSONResponse(
                    status_code=response.status_code,
                    content={
                        "success": False,
                        "error": f"Dify API returned status code: {response.status_code}",
                        "details": error_detail
                    }
                )
                
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=408,
            content={
                "success": False,
                "error": "Request timeout. Please try again."
            }
        )
    except httpx.RequestError as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": f"Request failed: {str(e)}"
            }
        )
    except Exception as e:
        import traceback
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": f"Internal error: {str(e)}",
                "traceback": traceback.format_exc()
            }
        )

@app.get("/api/conversation/{session_id}")
async def get_conversation(session_id: str):
    """Get conversation history for a session"""
    if session_id in CONVERSATIONS:
        return {
            "success": True,
            "session_id": session_id,
            "conversation_id": CONVERSATIONS[session_id]["conversation_id"],
            "messages": CONVERSATIONS[session_id]["messages"],
            "message_count": len(CONVERSATIONS[session_id]["messages"])
        }
    else:
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "error": "No conversation found for this session"
            }
        )

@app.post("/api/conversation/{session_id}/reset")
async def reset_conversation(session_id: str):
    """Reset conversation for a session"""
    if session_id in CONVERSATIONS:
        CONVERSATIONS[session_id] = {
            "conversation_id": "",
            "messages": []
        }
        return {
            "success": True,
            "message": f"Conversation reset for session {session_id}"
        }
    else:
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "error": "No conversation found for this session"
            }
        )

@app.get("/api/conversations")
async def list_conversations():
    """List all active conversations"""
    return {
        "success": True,
        "conversations": [
            {
                "session_id": sid,
                "conversation_id": data["conversation_id"],
                "message_count": len(data["messages"]),
                "last_updated": data["messages"][-1]["timestamp"] if data["messages"] else None
            }
            for sid, data in CONVERSATIONS.items() if data["messages"]
        ]
    }

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "config_configured": bool(DIFY_CONFIG["api_url"] and DIFY_CONFIG["api_key"]),
        "active_conversations": len(CONVERSATIONS),
        "total_messages": sum(len(conv["messages"]) for conv in CONVERSATIONS.values())
    }

@app.get("/favicon.ico")
async def favicon():
    """Return empty favicon to avoid 404 errors"""
    return ""

if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*50)
    print("Dify LLM Chat Interface with Conversation Management")
    print("="*50)
    print(f"Server running at: http://localhost:8026")
    print("Features:")
    print("  • Session-based conversation tracking")
    print("  • Conversation ID persistence")
    print("  • Conversation history per session")
    print("  • Multiple conversation support")
    print("Press CTRL+C to quit")
    print("="*50 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8026, log_level="info")
