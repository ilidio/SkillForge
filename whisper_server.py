import os
import math
from flask import Flask, request, jsonify
from faster_whisper import WhisperModel

app = Flask(__name__)

# Configuration
# 'tiny', 'base', 'small', 'medium', 'large-v2', 'large-v3'
# 'int8' is faster on CPU. Use 'float16' if on GPU (Mac M1/M2/M3 works well with CPU/int8 or specific libs)
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "base")
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE", "int8")

print(f"Loading Whisper Model: {MODEL_SIZE} on {DEVICE} ({COMPUTE_TYPE})...")
model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
print("Model loaded!")

def format_timestamp(seconds):
    hours = math.floor(seconds / 3600)
    minutes = math.floor((seconds % 3600) / 60)
    secs = math.floor(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"

@app.route('/v1/audio/transcriptions', methods=['POST'])
def transcribe():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
        
    file = request.files['file']
    filename = file.filename
    
    # Save temp
    temp_path = os.path.join("temp", filename)
    if not os.path.exists("temp"):
        os.makedirs("temp")
        
    file.save(temp_path)
    
    try:
        print(f"Transcribing {filename}...")
        segments, info = model.transcribe(temp_path, beam_size=5)
        
        # Check requested format
        response_format = request.form.get('response_format', 'json')
        
        if response_format == 'vtt':
            output = ["WEBVTT\n"]
            for segment in segments:
                start = format_timestamp(segment.start)
                end = format_timestamp(segment.end)
                output.append(f"{start} --> {end}")
                output.append(f"{segment.text}\n")
            result = "\n".join(output)
            return result, 200, {'Content-Type': 'text/vtt'}
            
        elif response_format == 'srt':
            output = []
            for i, segment in enumerate(segments, start=1):
                start = format_timestamp(segment.start).replace('.', ',')
                end = format_timestamp(segment.end).replace('.', ',')
                output.append(f"{i}")
                output.append(f"{start} --> {end}")
                output.append(f"{segment.text}\n")
            result = "\n".join(output)
            return result, 200, {'Content-Type': 'text/plain'}
            
        else:
            # JSON (Default)
            text_all = ""
            segs_json = []
            for segment in segments:
                text_all += segment.text
                segs_json.append({
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text
                })
            return jsonify({"text": text_all, "segments": segs_json})
            
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

if __name__ == '__main__':
    print("Starting Local Whisper Server on port 9000...")
    app.run(host='0.0.0.0', port=9000)
