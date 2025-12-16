import numpy as np
import wave
import matplotlib.pyplot as plt
import soundfile as sf
import librosa
from pathlib import Path
import asyncio
import json
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

class VADTester:
    def __init__(self, vad_manager_class, sample_rate=16000):
        """
        Test VAD manager with recorded audio files
        """
        self.sample_rate = sample_rate
        self.VADManagerClass = vad_manager_class
        self.results = []
        
    def load_audio_file(self, audio_path: str):
        """
        Load audio file and return numpy array
        Supports: wav, mp3, flac, etc.
        """
        try:
            # Try librosa first (handles most formats)
            audio, sr = librosa.load(audio_path, sr=self.sample_rate, mono=True)
            print(f"✓ Loaded {audio_path}")
            print(f"  Duration: {len(audio)/sr:.2f}s, Sample rate: {sr}Hz")
            print(f"  Audio shape: {audio.shape}, Min: {audio.min():.3f}, Max: {audio.max():.3f}")
            return audio
        except Exception as e:
            print(f"Error loading {audio_path}: {e}")
            return None
    
    def create_test_audio(self, duration=5.0):
        """
        Create synthetic test audio with speech and silence patterns
        Useful if you don't have recorded audio yet
        """
        total_samples = int(duration * self.sample_rate)
        audio = np.zeros(total_samples)
        
        # Create speech-like segments (sinusoids with noise)
        speech_segments = [
            (0.5, 1.5),    # Speech 1
            (2.0, 3.0),    # Speech 2  
            (3.5, 4.0),    # Short speech
            (4.5, 4.8)     # Very short speech
        ]
        
        for start, end in speech_segments:
            start_sample = int(start * self.sample_rate)
            end_sample = int(end * self.sample_rate)
            duration_samples = end_sample - start_sample
            
            # Create speech-like signal
            freq = 220  # Hz (typical speech frequency)
            t = np.linspace(0, (end-start), duration_samples)
            speech = 0.3 * np.sin(2 * np.pi * freq * t)  # Base tone
            speech += 0.1 * np.random.randn(duration_samples)  # Add noise
            speech *= np.hanning(duration_samples)  # Smooth edges
            
            audio[start_sample:end_sample] = speech
        
        # Add some background noise throughout
        audio += 0.01 * np.random.randn(total_samples)
        
        return audio
    
    async def test_vad_on_audio(self, audio: np.ndarray, chunk_duration_ms=30, 
                                visualize=True, save_results=True):
        """
        Test VAD manager on audio and detect turning points
        """
        print(f"\n{'='*60}")
        print("VAD TEST STARTED")
        print(f"{'='*60}")
        
        # Initialize VAD manager
        vad = self.VADManagerClass(sample_rate=self.sample_rate)
        
        # Calculate chunk size
        chunk_size = int(self.sample_rate * chunk_duration_ms / 1000)
        total_chunks = len(audio) // chunk_size
        
        # Arrays to store results
        timestamps = []
        is_active_list = []
        energy_list = []
        turning_points = []
        
        # Process audio chunk by chunk
        print(f"Processing {total_chunks} chunks ({chunk_duration_ms}ms each)...")
        
        for i in range(total_chunks):
            start_sample = i * chunk_size
            end_sample = start_sample + chunk_size
            chunk = audio[start_sample:end_sample]
            
            # Convert to float32 if needed
            if chunk.dtype != np.float32:
                chunk = chunk.astype(np.float32)
            
            # Process chunk through VAD
            is_active = await vad.process_chunk(chunk)
            
            # Calculate timestamp in seconds
            timestamp = start_sample / self.sample_rate
            
            # Store results
            timestamps.append(timestamp)
            is_active_list.append(is_active)
            
            # Calculate energy for visualization
            energy = np.sqrt(np.mean(chunk ** 2))
            energy_list.append(energy)
            
            # Detect turning points (state changes)
            if i > 0 and is_active_list[-1] != is_active_list[-2]:
                turning_point = {
                    'time': timestamp,
                    'type': 'START' if is_active else 'END',
                    'chunk_index': i
                }
                turning_points.append(turning_point)
                print(f"  ↳ Turning point at {timestamp:.2f}s: {'SPEECH START' if is_active else 'SPEECH END'}")
        
        # Calculate statistics
        active_chunks = sum(is_active_list)
        activity_percentage = (active_chunks / total_chunks) * 100
        
        print(f"\n{'='*60}")
        print("TEST RESULTS")
        print(f"{'='*60}")
        print(f"Total chunks processed: {total_chunks}")
        print(f"Active chunks: {active_chunks} ({activity_percentage:.1f}%)")
        print(f"Silence chunks: {total_chunks - active_chunks}")
        print(f"Turning points detected: {len(turning_points)}")
        
        # Print turning points in readable format
        if turning_points:
            print("\nTurning Points:")
            for i, tp in enumerate(turning_points):
                print(f"  {i+1:2d}. {tp['time']:6.2f}s - {tp['type']}")
        
        # Store results
        test_result = {
            'timestamp': datetime.now().isoformat(),
            'total_chunks': total_chunks,
            'active_chunks': active_chunks,
            'activity_percentage': activity_percentage,
            'turning_points': turning_points,
            'chunk_duration_ms': chunk_duration_ms
        }
        self.results.append(test_result)
        
        # Visualize results
        if visualize:
            self._visualize_results(audio, timestamps, is_active_list, energy_list, turning_points)
        
        # Save results
        if save_results:
            self._save_results(audio, test_result)
        
        return test_result
    
    def _visualize_results(self, audio, timestamps, is_active_list, energy_list, turning_points):
        """
        Create visualization of VAD results
        """
        fig, axes = plt.subplots(3, 1, figsize=(15, 10))
        
        # Plot 1: Raw audio waveform
        time_axis = np.linspace(0, len(audio)/self.sample_rate, len(audio))
        axes[0].plot(time_axis, audio, alpha=0.7, color='blue', linewidth=0.5)
        axes[0].set_title('Original Audio Waveform')
        axes[0].set_xlabel('Time (s)')
        axes[0].set_ylabel('Amplitude')
        axes[0].grid(True, alpha=0.3)
        
        # Highlight active regions
        for i, is_active in enumerate(is_active_list):
            if is_active:
                start_time = timestamps[i]
                end_time = start_time + (timestamps[1] - timestamps[0]) if i < len(timestamps)-1 else start_time + 0.03
                axes[0].axvspan(start_time, end_time, alpha=0.2, color='green', label='Active' if i==0 else "")
        
        # Plot 2: VAD activity
        axes[1].step(timestamps, is_active_list, where='post', color='red', linewidth=2)
        axes[1].set_title('VAD Activity Detection (1=Active, 0=Silent)')
        axes[1].set_xlabel('Time (s)')
        axes[1].set_ylabel('Activity State')
        axes[1].set_ylim(-0.1, 1.1)
        axes[1].grid(True, alpha=0.3)
        
        # Mark turning points
        for tp in turning_points:
            color = 'green' if tp['type'] == 'START' else 'red'
            axes[1].axvline(x=tp['time'], color=color, linestyle='--', alpha=0.7)
            axes[1].text(tp['time'], 0.5, tp['type'][0], 
                        ha='center', va='center', 
                        bbox=dict(boxstyle="round,pad=0.3", facecolor=color, alpha=0.5))
        
        # Plot 3: Energy over time
        axes[2].plot(timestamps, energy_list, color='purple', linewidth=1, alpha=0.7)
        axes[2].fill_between(timestamps, 0, energy_list, alpha=0.3, color='purple')
        axes[2].set_title('Audio Energy (RMS) per Chunk')
        axes[2].set_xlabel('Time (s)')
        axes[2].set_ylabel('Energy')
        axes[2].grid(True, alpha=0.3)
        
        # Add threshold line if available
        try:
            vad = self.VADManagerClass()
            if hasattr(vad, 'energy_threshold'):
                axes[2].axhline(y=vad.energy_threshold, color='red', linestyle='--', 
                              label=f'Threshold: {vad.energy_threshold:.4f}')
                axes[2].legend()
        except:
            pass
        
        plt.tight_layout()
        plt.savefig('vad_test_results.png', dpi=150, bbox_inches='tight')
        print("\n✓ Visualization saved as 'vad_test_results.png'")
        plt.show()
    
    def _save_results(self, audio, test_result):
        """
        Save test results and audio for later analysis
        """
        # Save results as JSON
        results_file = 'vad_test_results.json'
        with open(results_file, 'w') as f:
            json.dump(test_result, f, indent=2)
        
        # Save detected speech segments as separate WAV files
        if test_result['turning_points']:
            print("\nSaving detected speech segments...")
            
            # Group START and END points
            speech_segments = []
            current_start = None
            
            for tp in test_result['turning_points']:
                if tp['type'] == 'START' and current_start is None:
                    current_start = tp['time']
                elif tp['type'] == 'END' and current_start is not None:
                    speech_segments.append((current_start, tp['time']))
                    current_start = None
            
            # Save each speech segment
            for i, (start_time, end_time) in enumerate(speech_segments):
                start_sample = int(start_time * self.sample_rate)
                end_sample = int(end_time * self.sample_rate)
                segment = audio[start_sample:end_sample]
                
                if len(segment) > 0:
                    filename = f"speech_segment_{i+1:03d}_{start_time:.1f}s_to_{end_time:.1f}s.wav"
                    sf.write(filename, segment, self.sample_rate)
                    print(f"  ✓ Segment {i+1}: {filename} ({len(segment)/self.sample_rate:.2f}s)")
        
        print(f"\n✓ Results saved to {results_file}")
    
    def print_detailed_report(self, test_result):
        """
        Print detailed report of VAD performance
        """
        print(f"\n{'='*60}")
        print("DETAILED VAD PERFORMANCE REPORT")
        print(f"{'='*60}")
        
        print(f"\n1. Basic Statistics:")
        print(f"   - Total processing time: {test_result['total_chunks'] * 0.03:.1f}s")
        print(f"   - Activity percentage: {test_result['activity_percentage']:.1f}%")
        
        if test_result['turning_points']:
            print(f"\n2. Turning Points Analysis:")
            
            # Calculate speech segment durations
            speech_segments = []
            current_start = None
            
            for tp in test_result['turning_points']:
                if tp['type'] == 'START' and current_start is None:
                    current_start = tp['time']
                elif tp['type'] == 'END' and current_start is not None:
                    duration = tp['time'] - current_start
                    speech_segments.append({
                        'start': current_start,
                        'end': tp['time'],
                        'duration': duration
                    })
                    current_start = None
            
            if speech_segments:
                print(f"   - Number of speech segments: {len(speech_segments)}")
                print(f"   - Segment durations:")
                for i, seg in enumerate(speech_segments):
                    print(f"     {i+1:2d}. {seg['start']:5.2f}s - {seg['end']:5.2f}s "
                          f"({seg['duration']:.2f}s)")
                
                avg_duration = np.mean([s['duration'] for s in speech_segments])
                print(f"   - Average segment duration: {avg_duration:.2f}s")
    
    async def run_comprehensive_test(self, audio_path=None, synthetic_duration=5.0):
        """
        Run comprehensive test on either recorded or synthetic audio
        """
        # Load or create audio
        if audio_path and Path(audio_path).exists():
            print(f"Testing with recorded audio: {audio_path}")
            audio = self.load_audio_file(audio_path)
            if audio is None:
                print("Falling back to synthetic audio...")
                audio = self.create_test_audio(synthetic_duration)
        else:
            print("No recorded audio provided. Creating synthetic test audio...")
            audio = self.create_test_audio(synthetic_duration)
        
        if audio is None:
            print("Error: Could not load or create audio for testing")
            return None
        
        # Run VAD test
        print(f"\nAudio loaded: {len(audio)/self.sample_rate:.2f} seconds")
        
        # Test with different chunk durations
        chunk_durations = [10, 30, 50, 100]  # ms
        
        all_results = []
        for chunk_ms in chunk_durations:
            print(f"\n{'='*60}")
            print(f"Testing with {chunk_ms}ms chunks")
            print(f"{'='*60}")
            
            result = await self.test_vad_on_audio(
                audio, 
                chunk_duration_ms=chunk_ms,
                visualize=(chunk_ms == 30),  # Only visualize for 30ms chunks
                save_results=(chunk_ms == 30)
            )
            all_results.append(result)
            
            # Print report for this configuration
            self.print_detailed_report(result)
        
        return all_results




# Example usage
async def main():
    """Example usage of the VAD tester"""
    
    # First, make sure you have your VADManager class imported
    from vad_manager_2 import VADManager
    
    # Initialize tester with your VAD manager class
    tester = VADTester(vad_manager_class=VADManager, sample_rate=16000)
    
    # Option 1: Test with recorded audio file
    audio_path = "/home/ubuntu/realtime-model.alshifa.me/my_own_realtime_model/src_26856754_dst_10100_channel_SIP_from_dstchannel_SIP_101_4_09.mp3"
    results = await tester.run_comprehensive_test(audio_path)
    
    # Option 2: Test with synthetic audio (no file needed)
    # results = await tester.run_comprehensive_test(synthetic_duration=10.0)
    
    print(f"\n{'='*60}")
    print("TEST COMPLETED SUCCESSFULLY!")
    print(f"{'='*60}")




if __name__ == "__main__":
    # For testing, replace ActivityVADManager with your actual class
    # For now, we'll create a mock for demonstration
    class MockActivityVADManager:
        def __init__(self, sample_rate=16000):
            self.sample_rate = sample_rate
            self.is_active = False
            self.consecutive_silence = 0
            self.consecutive_activity = 0
            self.energy_threshold = 0.001
        
        async def process_chunk(self, audio_chunk):
            # Simple mock detection based on energy
            energy = np.sqrt(np.mean(audio_chunk ** 2))
            
            if energy > self.energy_threshold:
                self.consecutive_activity += 1
                self.consecutive_silence = 0
                if self.consecutive_activity >= 2:
                    self.is_active = True
            else:
                self.consecutive_silence += 1
                self.consecutive_activity = 0
                if self.consecutive_silence >= 5:
                    self.is_active = False
            
            return self.is_active
    
    # Replace with your actual VAD manager
    ActivityVADManager = MockActivityVADManager
    
    # Run the test
    asyncio.run(main())