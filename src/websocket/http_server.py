# src/websocket/http_server.py
from aiohttp import web
import aiohttp
import os
import json
import logging
import sys
import asyncio

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)


class HTTPServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.setup_routes()
    
    def setup_routes(self):
        """Setup HTTP routes"""
        self.app.router.add_get('/', self.serve_index)
        self.app.router.add_get('/app.js', self.serve_js)
        # self.app.router.add_static('/static/', path='./static/')
        
        # API routes
        self.app.router.add_post('/api/conversation/start', self.start_conversation)
        self.app.router.add_post('/api/conversation/stop', self.stop_conversation)
        self.app.router.add_get('/api/health', self.health_check)
    
    async def serve_index(self, request):
        """Serve main HTML page"""
        html_content = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Audio Agent - Real-time Voice AI</title>
            <script src="https://cdn.tailwindcss.com"></script>
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
        
        js_content = open("front/app.js", "r").read()
        
        return web.Response(text=js_content, content_type='application/javascript')
    
    async def start_conversation(self, request):
        """API endpoint to start conversation"""
        return web.json_response({'status': 'success', 'message': 'Conversation started'})
    
    async def stop_conversation(self, request):
        """API endpoint to stop conversation"""
        return web.json_response({'status': 'success', 'message': 'Conversation stopped'})
    
    async def health_check(self, request):
        """Health check endpoint"""
        return web.json_response({'status': 'healthy', 'service': 'audio-agent'})
    
    async def start(self):
        """Start HTTP server"""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        print(f"HTTP server running on http://{self.host}:{self.port}")






# Main application
async def main():
    # Initialize your pipeline components
    from src.core.pipeline import AudioAgentPipeline
    from src.core.vad_manager import VADManager
    from src.providers.stt.base import WhisperSTT
    from src.providers.tts.base import TTS
    from src.providers.llm.dify_provider import DifyLLMProvider
    
    stt = WhisperSTT()
    tts = TTS()
    llm = DifyLLMProvider(
        api_key="your-dify-api-key",
        base_url="https://api.dify.ai/v1"
    )
    vad = VADManager()
    
    pipeline = AudioAgentPipeline(stt, tts)
    
    # Start both HTTP and WebSocket servers
    http_server = HTTPServer()
    ws_server = EnhancedAudioWebSocketServer(pipeline)
    
    await asyncio.gather(
        http_server.start(),
        ws_server.start()
    )

if __name__ == "__main__":
    asyncio.run(main())