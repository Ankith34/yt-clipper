#!/usr/bin/env python3
import sys
import os
import re

print("=" * 50)
print("Cobalt-Style YouTube Clipper Starting...")
print("=" * 50)

# Test imports
try:
    from flask import Flask, request, jsonify, send_file, render_template, redirect, url_for
    print("✓ Flask imported successfully")
except ImportError as e:
    print(f"✗ Flask import failed: {e}")
    sys.exit(1)

try:
    from flask_cors import CORS
    print("✓ Flask-CORS imported successfully")
except ImportError as e:
    print(f"✗ Flask-CORS import failed: {e}")
    sys.exit(1)

try:
    import yt_dlp
    print("✓ yt-dlp imported successfully")
except ImportError as e:
    print(f"✗ yt-dlp import failed: {e}")
    sys.exit(1)

import subprocess
import uuid
import threading
import time
from datetime import datetime
import json

# Test FFmpeg
try:
    result = subprocess.run(['ffmpeg', '-version'], 
                          capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        print("✓ FFmpeg is available")
    else:
        print("✗ FFmpeg not working properly")
except Exception as e:
    print(f"✗ FFmpeg test failed: {e}")

print("=" * 50)

app = Flask(__name__)
CORS(app)

# Store job status and video info
jobs = {}
video_info_cache = {}

def get_video_id(url):
    """Extract video ID from YouTube URL"""
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([^&\n?#]+)',
        r'youtube\.com/watch\?.*v=([^&\n?#]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def get_video_info(url):
    """Get video information including available qualities"""
    try:
        video_id = get_video_id(url)
        if video_id in video_info_cache:
            print(f"Using cached info for video: {video_id}")
            return video_info_cache[video_id]
        
        print(f"Fetching video info for: {url}")
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Extract available qualities
            formats = info.get('formats', [])
            qualities = set()
            
            for fmt in formats:
                height = fmt.get('height')
                if height and fmt.get('vcodec') != 'none':  # Has video
                    if height >= 2160:
                        qualities.add('4K (2160p)')
                    elif height >= 1440:
                        qualities.add('1440p')
                    elif height >= 1080:
                        qualities.add('1080p')
                    elif height >= 720:
                        qualities.add('720p')
                    elif height >= 480:
                        qualities.add('480p')
                    elif height >= 360:
                        qualities.add('360p')
            
            # Sort qualities by resolution (highest first)
            quality_order = {'4K (2160p)': 2160, '1440p': 1440, '1080p': 1080, 
                           '720p': 720, '480p': 480, '360p': 360}
            sorted_qualities = sorted(list(qualities), 
                                    key=lambda x: quality_order.get(x, 0), reverse=True)
            
            video_data = {
                'title': info.get('title', 'Unknown Video'),
                'duration': info.get('duration', 0),
                'thumbnail': info.get('thumbnail', ''),
                'video_id': video_id,
                'qualities': sorted_qualities,
                'uploader': info.get('uploader', 'Unknown'),
                'view_count': info.get('view_count', 0),
                'description': info.get('description', '')[:200] + '...' if info.get('description') else ''
            }
            
            video_info_cache[video_id] = video_data
            print(f"✓ Video info cached: {video_data['title'][:50]}...")
            return video_data
            
    except Exception as e:
        print(f"✗ Error getting video info: {e}")
        return None

def time_to_seconds(time_str):
    """Convert time string to seconds"""
    try:
        parts = time_str.split(':')
        if len(parts) == 3:  # HH:MM:SS
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:  # MM:SS
            return int(parts[0]) * 60 + int(parts[1])
        else:  # SS
            return int(parts[0])
    except ValueError:
        raise Exception(f"Invalid time format: {time_str}")

def validate_time_range(start_time, end_time, duration):
    """Validate time range against video duration"""
    start_seconds = time_to_seconds(start_time)
    end_seconds = time_to_seconds(end_time)
    
    if start_seconds < 0:
        raise Exception("Start time cannot be negative")
    
    if end_seconds <= start_seconds:
        raise Exception("End time must be after start time")
    
    if start_seconds >= duration:
        raise Exception("Start time exceeds video duration")
    
    if end_seconds > duration:
        raise Exception("End time exceeds video duration")
    
    clip_duration = end_seconds - start_seconds
    if clip_duration > 600:  # 10 minutes max
        raise Exception("Clip duration cannot exceed 10 minutes")
    
    return start_seconds, end_seconds, clip_duration

def get_quality_format(quality):
    """Convert quality string to yt-dlp format"""
    quality_map = {
        '4K (2160p)': 'bestvideo[height=2160]+bestaudio/best',
        '1440p': 'bestvideo[height=1440]+bestaudio/best',
        '1080p': 'bestvideo[height=1080]+bestaudio/best',
        '720p': 'bestvideo[height=720]+bestaudio/best',
        '480p': 'bestvideo[height=480]+bestaudio/best',
        '360p': 'bestvideo[height=360]+bestaudio/best'
    }
    return quality_map.get(quality, 'bestvideo+bestaudio/best')

def clean_filename(filename):
    """Clean filename for safe storage"""
    # Remove/replace problematic characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = re.sub(r'[^\w\s-]', '', filename)
    filename = re.sub(r'[-\s]+', '-', filename)
    return filename.strip('-')[:50]  # Limit length

def download_and_clip(job_id, url, start_time, end_time, quality):
    """Download video and create clip with enhanced error handling"""
    try:
        print(f"\n🚀 Starting job {job_id}")
        print(f"   URL: {url}")
        print(f"   Time: {start_time} → {end_time}")
        print(f"   Quality: {quality}")
        
        jobs[job_id]['status'] = 'downloading'
        jobs[job_id]['message'] = f'downloading video in {quality}...'
        
        # Create downloads directory
        os.makedirs('downloads', exist_ok=True)
        
        # Validate time range
        video_info = jobs[job_id]['video_info']
        start_seconds, end_seconds, duration = validate_time_range(
            start_time, end_time, video_info['duration']
        )
        
        print(f"   Validated time range: {start_seconds}s → {end_seconds}s ({duration}s duration)")
        
        temp_video = f'downloads/temp_{job_id}.%(ext)s'
        
        # yt-dlp options with quality selection
        format_selector = get_quality_format(quality)
        ydl_opts = {
            'format': format_selector,
            'outtmpl': temp_video,
            'quiet': False,
            'no_warnings': False,
        }

        print(f"   🔄 Downloading with format: {format_selector}")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=True)
                video_title = info.get('title', 'video')
                print(f"   ✓ Downloaded: {video_title[:50]}...")
            except Exception as e:
                raise Exception(f"Download failed: {str(e)}")
        
        # Find downloaded file
        downloaded_file = None
        for file in os.listdir('downloads'):
            if file.startswith(f'temp_{job_id}'):
                downloaded_file = f'downloads/{file}'
                break
        
        if not downloaded_file or not os.path.exists(downloaded_file):
            raise Exception("Downloaded file not found")
        
        print(f"   ✓ Found downloaded file: {downloaded_file}")
        
        jobs[job_id]['status'] = 'clipping'
        jobs[job_id]['message'] = 'creating clip...'
        
        print(f"   ✂️  Creating clip: {start_seconds}s → {end_seconds}s")
        
        # Generate safe output filename
        safe_title = clean_filename(video_title)
        quality_tag = quality.replace(' ', '_').replace('(', '').replace(')', '')
        output_file = f'downloads/clip_{job_id}_{safe_title}_{quality_tag}.mp4'
        
        # Create clip using ffmpeg with optimized settings
        cmd = [
            'ffmpeg',
            '-i', downloaded_file,
            '-ss', str(start_seconds),
            '-t', str(duration),
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-preset', 'fast',
            '-crf', '23',
            '-movflags', '+faststart',  # Enable fast streaming
            '-avoid_negative_ts', 'make_zero',
            output_file,
            '-y'  # Overwrite output file
        ]
        
        print(f"   🎬 Running FFmpeg...")
        print(f"   Command: {' '.join(cmd[:8])}...")  # Show partial command for security
        
        result = subprocess.run(cmd, 
                              capture_output=True, 
                              text=True, 
                              timeout=300)  # 5 minute timeout
        
        if result.returncode != 0:
            print(f"   ✗ FFmpeg error: {result.stderr}")
            raise Exception(f"Video processing failed: {result.stderr}")
        
        # Verify output file exists and has reasonable size
        if not os.path.exists(output_file):
            raise Exception("Output file was not created")
        
        file_size = os.path.getsize(output_file)
        if file_size < 1024:  # Less than 1KB is suspicious
            raise Exception("Output file is too small - processing may have failed")
        
        print(f"   ✓ Clip created successfully: {output_file}")
        print(f"   ✓ File size: {file_size / (1024*1024):.2f} MB")
        
        # Clean up temporary file
        try:
            os.remove(downloaded_file)
            print(f"   🗑️  Cleaned up temp file: {downloaded_file}")
        except Exception as e:
            print(f"   ⚠️  Failed to remove temp file: {e}")
        
        jobs[job_id]['status'] = 'completed'
        jobs[job_id]['message'] = 'clip ready for download!'
        jobs[job_id]['download_url'] = f'/download/{job_id}'
        jobs[job_id]['filename'] = os.path.basename(output_file)
        jobs[job_id]['file_size'] = file_size
        
        print(f"   🎉 Job {job_id} completed successfully!")
        
    except Exception as e:
        print(f"   ❌ Job {job_id} failed: {str(e)}")
        jobs[job_id]['status'] = 'error'
        jobs[job_id]['message'] = str(e)
        
        # Clean up any temporary files
        try:
            for file in os.listdir('downloads'):
                if file.startswith(f'temp_{job_id}') or file.startswith(f'clip_{job_id}'):
                    os.remove(f'downloads/{file}')
                    print(f"   🗑️  Cleaned up: {file}")
        except Exception as cleanup_error:
            print(f"   ⚠️  Cleanup error: {cleanup_error}")

@app.route('/')
def index():
    """Main page"""
    return render_template('index.html')

@app.route('/preview/<job_id>')
def preview_page(job_id):
    """Preview/status page"""
    if job_id not in jobs:
        return redirect(url_for('index'))
    
    job = jobs[job_id]
    return render_template('preview.html', job=job, job_id=job_id)

@app.route('/api/video-info', methods=['POST'])
def get_video_info_api():
    """Get video information API endpoint"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'no json data provided'}), 400
        
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({'error': 'url is required'}), 400
        
        # Validate YouTube URL
        if not any(domain in url.lower() for domain in ['youtube.com', 'youtu.be']):
            return jsonify({'error': 'please provide a valid youtube url'}), 400
        
        print(f"📺 Fetching video info for: {url}")
        
        video_info = get_video_info(url)
        
        if not video_info:
            return jsonify({'error': 'could not fetch video information'}), 400
        
        print(f"✓ Video info retrieved: {video_info['title'][:50]}...")
        return jsonify(video_info)
        
    except Exception as e:
        print(f"❌ Video info error: {str(e)}")
        return jsonify({'error': f'server error: {str(e)}'}), 500

@app.route('/api/clip', methods=['POST'])
def create_clip():
    """Create clip API endpoint"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'no json data provided'}), 400
        
        url = data.get('url', '').strip()
        start_time = data.get('start_time', '').strip()
        end_time = data.get('end_time', '').strip()
        quality = data.get('quality', '').strip()
        
        print(f"📝 Clip request received:")
        print(f"   URL: {url}")
        print(f"   Start: {start_time}")
        print(f"   End: {end_time}")
        print(f"   Quality: {quality}")
        
        # Validate required fields
        if not all([url, start_time, end_time, quality]):
            return jsonify({'error': 'missing required fields'}), 400
        
        # Validate YouTube URL
        if not any(domain in url.lower() for domain in ['youtube.com', 'youtu.be']):
            return jsonify({'error': 'please provide a valid youtube url'}), 400
        
        # Get video info for the job
        video_info = get_video_info(url)
        if not video_info:
            return jsonify({'error': 'could not fetch video information'}), 400
        
        # Validate quality is available
        if quality not in video_info['qualities']:
            return jsonify({'error': f'quality {quality} not available for this video'}), 400
        
        try:
            # Validate time format and range
            validate_time_range(start_time, end_time, video_info['duration'])
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        
        # Generate job ID
        job_id = str(uuid.uuid4())
        
        # Initialize job with video info
        jobs[job_id] = {
            'status': 'queued',
            'message': 'job queued for processing...',
            'created_at': datetime.now(),
            'url': url,
            'start_time': start_time,
            'end_time': end_time,
            'quality': quality,
            'video_info': video_info
        }
        
        print(f"✓ Created job {job_id}")
        
        # Start processing in background
        thread = threading.Thread(
            target=download_and_clip,
            args=(job_id, url, start_time, end_time, quality),
            daemon=True
        )
        thread.start()
        
        return jsonify({
            'job_id': job_id, 
            'preview_url': f'/preview/{job_id}',
            'status': 'queued'
        })
        
    except Exception as e:
        print(f"❌ API error: {str(e)}")
        return jsonify({'error': f'server error: {str(e)}'}), 500

@app.route('/api/status/<job_id>')
def get_status(job_id):
    """Get job status API endpoint"""
    if job_id not in jobs:
        return jsonify({'error': 'job not found'}), 404
    
    job_data = jobs[job_id].copy()
    
    # Add additional info for completed jobs
    if job_data['status'] == 'completed':
        job_data['download_ready'] = True
    
    return jsonify(job_data)

@app.route('/download/<job_id>')
def download_clip(job_id):
    """Download clip endpoint"""
    if job_id not in jobs:
        return jsonify({'error': 'job not found'}), 404
    
    job = jobs[job_id]
    
    if job['status'] != 'completed':
        return jsonify({'error': 'file not ready yet'}), 404
    
    try:
        # Find the clip file
        clip_file = None
        for filename in os.listdir('downloads'):
            if filename.startswith(f'clip_{job_id}'):
                clip_file = f'downloads/{filename}'
                break
        
        if not clip_file or not os.path.exists(clip_file):
            return jsonify({'error': 'file not found'}), 404
        
        print(f"📥 Serving download: {clip_file}")
        
        # Get original filename from job
        download_name = job.get('filename', f'clip_{job_id}.mp4')
        
        return send_file(
            clip_file,
            as_attachment=True,
            download_name=download_name,
            mimetype='video/mp4'
        )
        
    except Exception as e:
        print(f"❌ Download error: {e}")
        return jsonify({'error': f'download error: {str(e)}'}), 500

@app.route('/api/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'active_jobs': len([j for j in jobs.values() if j['status'] in ['queued', 'downloading', 'clipping']]),
        'total_jobs': len(jobs)
    })

@app.errorhandler(404)
def not_found(error):
    """404 handler"""
    return redirect(url_for('index'))

@app.errorhandler(500)
def internal_error(error):
    """500 handler"""
    print(f"❌ Internal server error: {error}")
    return jsonify({'error': 'internal server error'}), 500

# Cleanup old jobs periodically
def cleanup_old_jobs():
    """Clean up old jobs and files"""
    try:
        now = datetime.now()
        old_jobs = []
        
        for job_id, job in jobs.items():
            # Remove jobs older than 1 hour
            if (now - job['created_at']).total_seconds() > 3600:
                old_jobs.append(job_id)
        
        for job_id in old_jobs:
            # Clean up files
            try:
                for filename in os.listdir('downloads'):
                    if job_id in filename:
                        os.remove(f'downloads/{filename}')
                        print(f"🗑️  Cleaned up old file: {filename}")
            except Exception as e:
                print(f"⚠️  Cleanup error for {job_id}: {e}")
            
            # Remove from jobs dict
            del jobs[job_id]
            print(f"🗑️  Removed old job: {job_id}")
    
    except Exception as e:
        print(f"⚠️  Cleanup process error: {e}")

# Schedule cleanup every 30 minutes
def start_cleanup_scheduler():
    """Start background cleanup scheduler"""
    def cleanup_loop():
        while True:
            time.sleep(1800)  # 30 minutes
            cleanup_old_jobs()
    
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("🚀 Starting Cobalt-Style YouTube Clipper...")
    print("📂 Make sure templates/index.html and templates/preview.html exist!")
    print("🌐 Server will run at: http://localhost:5000")
    print("💡 Use Ctrl+C to stop the server")
    print("🔧 Features: Video analysis, quality selection, precise clipping")
    print("=" * 60 + "\n")
    
    # Start cleanup scheduler
    start_cleanup_scheduler()
    
    try:
        app.run(
            debug=True, 
            port=5000, 
            host='127.0.0.1',
            threaded=True
        )
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
    except Exception as e:
        print(f"❌ Failed to start server: {e}")
        input("Press Enter to exit...")