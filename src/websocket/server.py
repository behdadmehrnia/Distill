# src/websocket/server.py
import asyncio
import websockets
import json
import base64
import numpy as np
from aiohttp import web
import logging
from typing import Dict, Any, Set
from src.providers.llms.dify_provider import DifyLLMProvider

from src.core.vad_manager import VADManager
#from src.core.vad_manager_2 import VADManager

import collections
import time

logger = logging.getLogger(__name__)

class AudioAgentServer:
    def __init__(self, pipeline, host: str = "0.0.0.0", http_port: int = 8080, ws_port: int = 8765):
        self.pipeline = pipeline
        self.host = host
        self.http_port = http_port
        self.ws_port = ws_port
        self.ws_connections: Set[websockets.WebSocketServerProtocol] = set()
        # HTTP app
        self.app = web.Application()
        self.setup_routes()
        
    def setup_routes(self):
        """Setup HTTP routes"""
        self.app.router.add_get('/', self.serve_index)
        self.app.router.add_get('/app.js', self.serve_js)
        self.app.router.add_static('/static/', path='./static/')
        self.app.router.add_get('/health', self.health_check)
        
        # WebSocket route
        self.app.router.add_get('/ws', self.websocket_handler)

    async def serve_index(self, request):

        """Serve main HTML page"""
        html_content = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Audio Agent - Real-time Voice AI</title>
            <script src="/static/tailwind.js"></script>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        </head>
        <body class="bg-gray-900 text-white">
            <div class="container mx-auto px-4 py-8">
                <!-- Header -->
                <div class="text-center mb-8">
                    <h1 class="text-4xl font-bold text-blue-400 mb-2">Audio Agent</h1>
                    <p class="text-gray-400">Real-time voice conversation with AI</p>
                </div>

                <!-- Main Content -->
                <div class="max-w-4xl mx-auto">
                    <!-- Status Panel -->
                    <div class="bg-gray-800 rounded-lg p-6 mb-6">
                        <div class="flex flex-wrap items-center justify-between gap-4">
                            <div class="flex items-center space-x-4">
                                <div id="connectionStatus" class="w-3 h-3 rounded-full bg-red-500 mr-2"></div>
                                <span id="statusText" class="text-sm">Disconnected</span>
                            </div>
                            <div id="audioLevel" class="flex items-center space-x-2 hidden">
                                <div class="w-2 h-4 bg-green-400 rounded"></div>
                                <div class="w-2 h-6 bg-green-400 rounded"></div>
                                <div class="w-2 h-3 bg-green-400 rounded"></div>
                                <span class="text-xs text-gray-400">Listening...</span>
                            </div>
                        </div>
                        
                        <div class="flex space-x-4 mt-4">
                            <button id="startBtn" class="bg-green-500 hover:bg-green-600 px-6 py-2 rounded-lg font-semibold transition-colors">
                                <i class="fas fa-microphone mr-2"></i>Start Conversation
                            </button>
                            <button id="stopBtn" class="bg-red-500 hover:bg-red-600 px-6 py-2 rounded-lg font-semibold transition-colors hidden">
                                <i class="fas fa-stop mr-2"></i>Stop
                            </button>
                        </div>
                    </div>

                    <!-- Conversation Area -->
                    <div class="bg-gray-800 rounded-lg p-6 mb-6">
                        <h2 class="text-xl font-semibold mb-4 text-blue-400">Conversation</h2>
                        <div id="conversation" class="space-y-4 max-h-96 overflow-y-auto p-4 bg-gray-900 rounded-lg">
                            <div class="text-center text-gray-500 py-8">
                                <i class="fas fa-comments text-4xl mb-2"></i>
                                <p>Start speaking to begin conversation</p>
                            </div>
                        </div>
                    </div>

                    <!-- Settings Panel -->
                    <div class="bg-gray-800 rounded-lg p-6">
                        <h2 class="text-xl font-semibold mb-4 text-blue-400">Settings</h2>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div>
                                <label class="block text-sm font-medium mb-2">Audio Input</label>
                                <select id="audioInput" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white">
                                    <option value="">Default Microphone</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-sm font-medium mb-2">Voice Settings</label>
                                <div class="flex space-x-4">
                                    <button id="testAudio" class="bg-blue-500 hover:bg-blue-600 px-4 py-2 rounded-lg text-sm">
                                        Test Audio
                                    </button>
                                    <button id="clearChat" class="bg-gray-600 hover:bg-gray-700 px-4 py-2 rounded-lg text-sm">
                                        Clear Chat
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Audio Elements -->
            <audio id="audioPlayer" class="hidden"></audio>

            <script src="/app.js"></script>
        </body>
        </html>
        """
        return web.Response(text=html_content, content_type='text/html')

    async def serve_js(self, request):
        """Serve JavaScript file"""
        js_content = """
// app.js
class AudioAgentClient {
    constructor() {
        this.ws = null;
        this.audioContext = null;
        this.mediaRecorder = null;
        this.audioStream = null;
        this.isRecording = false;
        this.isConnected = false;
        
        // DOM Elements
        this.startBtn = document.getElementById('startBtn');
        this.stopBtn = document.getElementById('stopBtn');
        this.conversation = document.getElementById('conversation');
        this.connectionStatus = document.getElementById('connectionStatus');
        this.statusText = document.getElementById('statusText');
        this.audioLevel = document.getElementById('audioLevel');
        this.audioInput = document.getElementById('audioInput');
        this.testAudio = document.getElementById('testAudio');
        this.clearChat = document.getElementById('clearChat');
        this.audioPlayer = document.getElementById('audioPlayer');
        
        this.initializeEventListeners();
        this.loadAudioDevices();
    }

    initializeEventListeners() {
        this.startBtn.addEventListener('click', () => this.startConversation());
        this.stopBtn.addEventListener('click', () => this.stopConversation());
        this.testAudio.addEventListener('click', () => this.testAudioOutput());
        this.clearChat.addEventListener('click', () => this.clearConversation());
    }

    async loadAudioDevices() {
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            const audioInputs = devices.filter(device => device.kind === 'audioinput');
            
            this.audioInput.innerHTML = '<option value="">Default Microphone</option>';
            audioInputs.forEach(device => {
                const option = document.createElement('option');
                option.value = device.deviceId;
                option.textContent = device.label || `Microphone ${this.audioInput.children.length + 1}`;
                this.audioInput.appendChild(option);
            });
        } catch (error) {
            console.error('Error loading audio devices:', error);
        }
    }

    async startConversation() {
        try {
            await this.connectWebSocket();
            await this.startAudioRecording();
            
            this.updateUI('recording');
            this.addMessage('system', 'Conversation started. Speak now...');
            
        } catch (error) {
            console.error('Error starting conversation:', error);
            this.addMessage('error', `Failed to start: ${error.message}`);
        }
    }

    async connectWebSocket() {
        return new Promise((resolve, reject) => {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws`;

            console.log("wsUrl", wsUrl);
            
            this.ws = new WebSocket(wsUrl);
            
            this.ws.onopen = () => {
                console.log('WebSocket connected');
                this.isConnected = true;
                this.updateConnectionStatus('connected');
                resolve();
            };
            
            this.ws.onmessage = (event) => {
                this.handleWebSocketMessage(event);
            };
            
            this.ws.onclose = () => {
                console.log('WebSocket disconnected');
                this.isConnected = false;
                this.updateConnectionStatus('disconnected');
                if (this.isRecording) {
                    this.stopConversation();
                }
            };
            
            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                reject(error);
            };
        });
    }

    async startAudioRecording() {
        try {
            const constraints = {
                audio: {
                    deviceId: this.audioInput.value ? { exact: this.audioInput.value } : undefined,
                    sampleRate: 16000,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true
                }
            };
            
            this.audioStream = await navigator.mediaDevices.getUserMedia(constraints);
            this.audioContext = new AudioContext({ sampleRate: 16000 });
            
            const source = this.audioContext.createMediaStreamSource(this.audioStream);
            const processor = this.audioContext.createScriptProcessor(1024, 1, 1);
            
            processor.onaudioprocess = (event) => {
                if (this.isRecording && this.isConnected) {
                    const audioData = event.inputBuffer.getChannelData(0);
                    this.sendAudioData(audioData);
                    this.updateAudioLevel(audioData);
                }
            };
            
            source.connect(processor);
            processor.connect(this.audioContext.destination);
            
            this.isRecording = true;
            
        } catch (error) {
            throw new Error(`Microphone access denied: ${error.message}`);
        }
    }

    sendAudioData(audioData) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            const int16Data = this.floatTo16BitPCM(audioData);
            const message = {
                type: 'audio',
                data: Array.from(int16Data)
            };
            this.ws.send(JSON.stringify(message));
        }
    }

    floatTo16BitPCM(input) {
        const output = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
            const s = Math.max(-1, Math.min(1, input[i]));
            output[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return output;
    }

    handleWebSocketMessage(event) {
        try {
            const message = JSON.parse(event.data);
            
            switch (message.type) {
                case 'audio':
                    this.playAudioOutput(message.data);
                    break;
                    
                case 'transcript':
                    this.addMessage('user', message.text);
                    break;
                    
                case 'response':
                    this.addMessage('assistant', message.text);
                    break;
                    
                case 'error':
                    this.addMessage('error', message.message);
                    break;
                    
                case 'vad_status':
                    this.updateAudioLevelDisplay(message.is_speech);
                    break;

                case 'status':
                    console.log('Status:', message.message);
                    break;
            }
        } catch (error) {
            console.error('Error handling WebSocket message:', error);
        }
    }

    playAudioOutput(audioData) {
        try {
            // Convert base64 audio data to ArrayBuffer
            const binaryString = atob(audioData);
            const bytes = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) {
                bytes[i] = binaryString.charCodeAt(i);
            }
            
            // Create audio blob and play
            const blob = new Blob([bytes], { type: 'audio/wav' });
            const url = URL.createObjectURL(blob);
            
            this.audioPlayer.src = url;
            this.audioPlayer.play().catch(e => console.error('Audio play failed:', e));
            
        } catch (error) {
            console.error('Error playing audio:', error);
        }
    }

    stopAudio() {
        try {
            if (this.audioPlayer) {
                this.audioPlayer.pause();
                this.audioPlayer.currentTime = 0;
                
                // Clean up the object URL to prevent memory leaks
                if (this.audioPlayer.src) {
                    URL.revokeObjectURL(this.audioPlayer.src);
                }
            }
        } catch (error) {
            console.error('Error stopping audio:', error);
        }
    }

    async testAudioOutput() {

        // Test with a simple message
        const testMessage = {
            type: 'test_audio',
            text: 'This is a test of the audio system.'
        };
        
        console.log("this.ws", this.ws);
        console.log("this.isConnected", this.isConnected);

        if (this.ws && this.isConnected) {
            this.ws.send(JSON.stringify(testMessage));
        } else {
            this.addMessage('error', 'Not connected to server');
        }
    }

    addMessage(role, content) {
        // Clear initial message if it exists
        if (this.conversation.querySelector('.text-center')) {
            this.conversation.innerHTML = '';
        }

        const messageDiv = document.createElement('div');
        messageDiv.className = `flex ${role === 'user' ? 'justify-end' : 'justify-start'} mb-4`;
        
        const bubble = document.createElement('div');
        bubble.className = `max-w-xs lg:max-w-md px-4 py-2 rounded-lg ${
            role === 'user' 
                ? 'bg-blue-500 text-white rounded-br-none' 
                : role === 'assistant'
                ? 'bg-gray-700 text-white rounded-bl-none'
                : role === 'system'
                ? 'bg-green-500 text-white'
                : 'bg-red-500 text-white'
        }`;
        
        const icon = role === 'user' ? '👤' : role === 'assistant' ? '🤖' : role === 'system' ? '⚡' : '⚠️';
        bubble.innerHTML = `
            <div class="text-xs opacity-75 mb-1">${icon} ${role}</div>
            <div class="text-sm">${this.escapeHtml(content)}</div>
        `;
        
        messageDiv.appendChild(bubble);
        this.conversation.appendChild(messageDiv);
        this.conversation.scrollTop = this.conversation.scrollHeight;
    }

    clearConversation() {
        this.conversation.innerHTML = `
            <div class="text-center text-gray-500 py-8">
                <i class="fas fa-comments text-4xl mb-2"></i>
                <p>Start speaking to begin conversation</p>
            </div>
        `;
    }

    updateUI(state) {
        switch (state) {
            case 'recording':
                this.startBtn.classList.add('hidden');
                this.stopBtn.classList.remove('hidden');
                this.audioLevel.classList.remove('hidden');
                break;
                
            case 'stopped':
                this.startBtn.classList.remove('hidden');
                this.stopBtn.classList.add('hidden');
                this.audioLevel.classList.add('hidden');
                break;
        }
    }

    updateConnectionStatus(status) {
        switch (status) {
            case 'connected':
                this.connectionStatus.className = 'w-3 h-3 rounded-full bg-green-500 mr-2';
                this.statusText.textContent = 'Connected';
                break;
            case 'connecting':
                this.connectionStatus.className = 'w-3 h-3 rounded-full bg-yellow-500 mr-2';
                this.statusText.textContent = 'Connecting...';
                break;
            default:
                this.connectionStatus.className = 'w-3 h-3 rounded-full bg-red-500 mr-2';
                this.statusText.textContent = 'Disconnected';
        }
    }

    updateAudioLevel(audioData) {
        // Calculate RMS for audio level visualization
        let sum = 0;
        for (let i = 0; i < audioData.length; i++) {
            sum += audioData[i] * audioData[i];
        }
        const rms = Math.sqrt(sum / audioData.length);
        
        // Update visualizer bars
        const bars = this.audioLevel.querySelectorAll('div');
        const level = Math.min(3, Math.floor(rms * 10));
        
        bars.forEach((bar, index) => {
            const height = index < level ? 6 : 2;
            bar.className = `w-2 h-${height} bg-green-400 rounded`;
        });
    }

    updateAudioLevelDisplay(isSpeech) {
        const bars = this.audioLevel.querySelectorAll('div');
        const color = isSpeech ? 'bg-green-400' : 'bg-gray-400';
        
        bars.forEach(bar => {
            bar.className = bar.className.replace(/bg-(green|gray)-400/, color);
        });

        if (isSpeech) {
            this.stopAudio();
        }
    }

    stopConversation() {
        this.isRecording = false;
        
        if (this.audioStream) {
            this.audioStream.getTracks().forEach(track => track.stop());
            this.audioStream = null;
        }
        
        if (this.audioContext) {
            this.audioContext.close();
            this.audioContext = null;
        }
        
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        
        this.updateUI('stopped');
        this.addMessage('system', 'Conversation ended');
    }

    escapeHtml(unsafe) {
        return unsafe
    }
}

// Initialize the application when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.audioAgent = new AudioAgentClient();
});
        """
        return web.Response(text=js_content, content_type='application/javascript')




    async def wait_audio(self):
        if self.pipeline.waiting_audio is not None:
            return self.pipeline.waiting_audio, self.pipeline.waiting_audio_duration
        
        audio_output = await self.pipeline.tts.synthesize("اجازه بدید بررسی کنم")
                                            
        # Estimate audio duration
        # Assuming 16kHz sample rate, 16-bit mono audio
        audio_duration = len(audio_output) / (2 * 16000)  # bytes / (2 bytes per sample * 16000 samples/sec)
        tts_duration = audio_duration
        
        audio_base64 = base64.b64encode(audio_output).decode("ascii")

        self.pipeline.set_waiting_audio(audio_base64, tts_duration)
        return audio_base64, tts_duration



    async def websocket_handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        self.vad = VADManager()
        self.llm = DifyLLMProvider()
        
        self.ws_connections.add(ws)
        logger.info(f"WebSocket connected. Total: {len(self.ws_connections)}")

        # Buffer to accumulate speech audio
        stt_buffer = []
        
        # Lock to prevent parallel processing
        processing_lock = asyncio.Lock()
        is_processing = False
        is_tts_playing = False
        tts_start_time = None
        tts_duration = 0

        try:
            await ws.send_json({
                'type': 'status',
                'message': 'Connected to audio agent'
            })

            async for msg in ws:
                # If TTS is playing, check if it has finished
                if is_tts_playing and tts_start_time:
                    elapsed = time.time() - tts_start_time
                    if elapsed >= tts_duration:
                        logger.info(f"TTS audio finished playing after {elapsed:.2f}s")
                        is_tts_playing = False
                        tts_start_time = None
                
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)

                        if data["type"] == "audio":
                            # Skip if processing OR TTS is playing
                            if is_processing or is_tts_playing:
                                continue
                                
                            # Convert audio to float32 for Silero VAD
                            audio_chunk = np.array(data["data"], dtype=np.int16)
                            float_audio = audio_chunk.astype(np.float32) / 32768.0

                            # Feed into VAD stream
                            vad_result = await self.vad.is_speech(float_audio)
                            
                            # vad_result can be: "speech", "silence", "end_of_speech"
                            #if vad_result:   # speech detected
                            stt_buffer.append(float_audio)
                            
                            if vad_result:
                                await ws.send_json({
                                    "type": "vad_status",
                                    "is_speech": True,
                                })
                            else:
                                await ws.send_json({
                                    "type": "vad_status",
                                    "is_speech": False,
                                })
                            

                            if await self.vad.speech_ended() and len(stt_buffer) > 2: # end of speech detected

                                audio_base64, tts_duration = await self.wait_audio()
                                await ws.send_json({
                                                "type": "audio",
                                                "data": audio_base64,
                                                "duration": tts_duration
                                            })

                                logger.info("Speech segment ended — running STT...")
                                
                                # Acquire lock to prevent parallel processing
                                async with processing_lock:
                                    is_processing = True
                                    
                                    try:
                                        # Send processing status to frontend
                                        await ws.send_json({
                                            "type": "status", 
                                            "message": "processing_speech"
                                        })

                                        # Combine all chunks
                                        self.vad.reset()
                                        full_audio = np.concatenate(stt_buffer)

                                        # Run whisper
                                        text = await self.pipeline.stt.transcribe(full_audio)
                                        logger.info(f"STT: {text}")

                                        await ws.send_json({
                                            "type": "transcript",
                                            "text": "دارم فکر میکنم ..."
                                        })

                                        if self.pipeline.stt.is_persian_valid(text) and text != "" and text != "null" and text != None:
                                            # Run LLM
                                            response = await self.llm.generate(text)
                                            logger.info(f"LLM: {response}")

                                            # Send transcript to frontend
                                            await ws.send_json({
                                                "type": "transcript",
                                                "text": response
                                            })

                                            # Run TTS
                                            audio_output = await self.pipeline.tts.synthesize(response)
                                            
                                            # Estimate audio duration
                                            # Assuming 16kHz sample rate, 16-bit mono audio
                                            audio_duration = len(audio_output) / (2 * 16000)  # bytes / (2 bytes per sample * 16000 samples/sec)
                                            tts_duration = audio_duration * 2.5
                                            is_tts_playing = True
                                            tts_start_time = time.time()
                                            
                                            logger.info(f"TTS audio duration estimated: {tts_duration:.2f} seconds")
                                            
                                            audio_base64 = base64.b64encode(audio_output).decode("ascii")

                                            await ws.send_json({
                                                "type": "audio",
                                                "data": audio_base64,
                                                "duration": tts_duration  # Optional: send duration to frontend
                                            })

                                            # Clear buffer after successful processing
                                            stt_buffer = []
                                            self.vad.reset()
                                        
                                    except Exception as e:
                                        logger.error(f"Processing error: {e}")
                                        await ws.send_json({
                                            'type': 'error', 
                                            'message': f'Processing failed: {str(e)}'
                                        })
                                        # Reset TTS state on error
                                        is_tts_playing = False
                                        tts_start_time = None
                                        
                                    finally:
                                        # Release lock
                                        is_processing = False

                            # else VAD returned "silence" — do nothing

                        elif data["type"] == "test_audio":
                            # Skip test audio if processing OR TTS is playing
                            if is_processing or is_tts_playing:
                                continue
                                
                            async with processing_lock:
                                is_processing = True
                                try:
                                    test_response = "Audio system test OK!"
                                    audio_output = await self.pipeline.tts.synthesize(test_response)
                                    
                                    # Estimate duration for test audio too
                                    audio_duration = len(audio_output) / (2 * 16000)
                                    tts_duration = audio_duration
                                    is_tts_playing = True
                                    tts_start_time = time.time()
                                    
                                    audio_array = np.frombuffer(audio_output, dtype=np.int16)
                                    audio_base64 = base64.b64encode(audio_array.tobytes()).decode("ascii")

                                    await ws.send_json({
                                        'type': 'audio',
                                        'data': audio_base64,
                                        'duration': tts_duration
                                    })
                                finally:
                                    is_processing = False

                    except Exception as e:
                        logger.error(f"Error: {e}")
                        await ws.send_json({'type': 'error', 'message': str(e)})

                elif msg.type == web.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")

        except Exception as e:
            logger.error(f"WebSocket connection error: {e}")

        finally:
            self.ws_connections.remove(ws)
            logger.info(f"WebSocket disconnected. Total: {len(self.ws_connections)}")

        return ws




    async def health_check(self, request):
        """Health check endpoint"""
        return web.json_response({
            'status': 'healthy', 
            'service': 'audio-agent',
            'connections': len(self.ws_connections)
        })

    async def start(self):
        """Start the combined HTTP + WebSocket server"""
        runner = web.AppRunner(self.app)
        await runner.setup()
        
        site = web.TCPSite(runner, self.host, self.http_port)
        await site.start()
        
        logger.info(f"Audio Agent server running on http://{self.host}:{self.http_port}")
        logger.info(f"WebSocket available at ws://{self.host}:{self.http_port}/ws")
        
        # Keep the server running
        await asyncio.Future()
