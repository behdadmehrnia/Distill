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
                option.textContent = device.label || `Microphone ${this.audioInput.children.length}`;
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
                    const audioData = event.inputBuffer.getChannel(0);
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

                case 'vad':
                    this.updateAudioLevelDisplay(message.is_speech);
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

    async testAudioOutput() {
        // Test with a simple message
        const testMessage = {
            type: 'test_audio',
            text: 'This is a test of the audio system.'
        };

        if (this.ws && this.isConnected) {
            this.ws.send(JSON.stringify(testMessage));
        } else {
            this.addMessage('error', 'Not connected to server');
        }
    }

    addMessage(role, content) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `flex ${role === 'user' ? 'justify-end' : 'justify-start'}`;

        const bubble = document.createElement('div');
        bubble.className = `max-w-xs lg:max-w-md px-4 py-2 rounded-lg ${role === 'user'
            ? 'bg-blue-500 text-white rounded-br-none'
            : role === 'assistant'
                ? 'bg-gray-700 text-white rounded-bl-none'
                : 'bg-red-500 text-white'
            }`;

        const icon = role === 'user' ? '👤' : role === 'assistant' ? '🤖' : '⚠️';
        bubble.innerHTML = `
            <div class="text-xs opacity-75 mb-1">${icon} ${role}</div>
            <div>${this.escapeHtml(content)}</div>
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
            bar.className = `w-2 h-${index < level ? '4' : '2'} bg-green-400 rounded`;
        });
    }

    updateAudioLevelDisplay(isSpeech) {
        const bars = this.audioLevel.querySelectorAll('div');
        const color = isSpeech ? 'bg-green-400' : 'bg-gray-400';

        bars.forEach(bar => {
            bar.className = bar.className.replace(/bg-(green|gray)-400/, color);
        });
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
        return unsafe;
    }
}

// Initialize the application when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.audioAgent = new AudioAgentClient();
});