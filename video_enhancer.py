import os
import json
import google.generativeai as genai
from moviepy.editor import *
import moviepy.video.fx.all as vfx
import time
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# --- Instructions ---
# 1. Set your Gemini API key: export GEMINI_API_KEY="YOUR_API_KEY"
# 2. Enable the YouTube Data API v3 in your Google Cloud project.
# 3. Create OAuth 2.0 credentials for a "Desktop app" and download the
#    JSON file. Rename it to "client_secrets.json" and place it in the same
#    directory as this script.
# 4. Create "videos_to_process" and "music" directories.
# 5. Run the script: python video_enhancer.py

# --- Local Video Processing ---
def get_video_files(directory_path):
    video_files = []
    supported_extensions = ['.mp4', '.mov', '.avi', '.mkv']
    for filename in os.listdir(directory_path):
        if any(filename.lower().endswith(ext) for ext in supported_extensions):
            video_files.append(os.path.join(directory_path, filename))
    return video_files

# --- Gemini Integration ---
def get_gemini_suggestions(video_path):
    print(f"Analyzing {os.path.basename(video_path)} with Gemini...")
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key: raise ValueError("GEMINI_API_KEY not set.")
        genai.configure(api_key=api_key)

        video_file = genai.upload_file(path=video_path, display_name=os.path.basename(video_path))
        while video_file.state.name == "PROCESSING":
            time.sleep(10)
            video_file = genai.get_file(video_file.name)
        if video_file.state.name == "FAILED": raise ValueError(f"Video processing failed.")

        prompt = """
        You are a viral video expert. Analyze the video and provide suggestions in JSON format:
        {"background_music": "...", "color_edit": "...", "tilt": "...", "title": "A catchy title for a YouTube Short", "description": "A short, engaging description with relevant #hashtags."}
        """
        model = genai.GenerativeModel(model_name="gemini-1.5-pro-latest")
        response = model.generate_content([prompt, video_file])
        genai.delete_file(video_file.name)
        json_response = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(json_response)
    except Exception as e:
        print(f"Error during Gemini analysis: {e}")
        return None

# --- Video Editing ---
def apply_edits(video_path, suggestions):
    try:
        clip = VideoFileClip(video_path)
        color_suggestion = suggestions.get("color_edit", "").lower()
        if "contrast" in color_suggestion: clip = vfx.lum_contrast(clip, lum=0, contrast=0.2)
        if "vibrant" in color_suggestion: clip = vfx.colorx(clip, 1.2)
        if "vintage" in color_suggestion: clip = vfx.colorx(clip, 1.1).fx(vfx.lum_contrast, lum=-5, contrast=-0.1)
        if "desaturate" in color_suggestion: clip = vfx.colorx(clip, 0.5)

        tilt_suggestion = suggestions.get("tilt", "").lower()
        if "9:16" in tilt_suggestion:
            (w, h) = clip.size
            target_w = int(h * 9 / 16)
            clip = vfx.crop(clip, width=target_w, height=h, x_center=w/2, y_center=h/2)

        music_path = input("Enter path to background music (or press Enter to skip): ")
        if music_path and os.path.exists(music_path):
            music = AudioFileClip(music_path)
            music = music.subclip(0, clip.duration) if music.duration > clip.duration else music.fx(vfx.loop, duration=clip.duration)
            if clip.audio:
                original_audio = clip.audio.volumex(0.3)
                clip.audio = CompositeAudioClip([original_audio, music.volumex(0.7)])
            else:
                clip.audio = music

        output_dir = "edited_videos"
        if not os.path.exists(output_dir): os.makedirs(output_dir)
        output_path = os.path.join(output_dir, f"edited_{os.path.basename(video_path)}")
        clip.write_videofile(output_path, codec="libx264", audio_codec="aac")
        return output_path
    except Exception as e:
        print(f"Error during video editing: {e}")
        return None

# --- YouTube Integration ---
def get_youtube_service():
    CLIENT_SECRETS_FILE = "client_secrets.json"
    YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
    API_SERVICE_NAME = "youtube"
    API_VERSION = "v3"
    TOKEN_FILE = "youtube_token.json"

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, [YOUTUBE_UPLOAD_SCOPE])
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, [YOUTUBE_UPLOAD_SCOPE])
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
    return build(API_SERVICE_NAME, API_VERSION, credentials=creds)

def upload_to_youtube(video_path, suggestions):
    try:
        youtube = get_youtube_service()
        body = {
            "snippet": {
                "title": suggestions.get("title", "AI-Enhanced Video"),
                "description": suggestions.get("description", "This video was edited with AI assistance."),
                "tags": ["YouTubeShorts", "AI", "Gemini"],
                "categoryId": "22" # People & Blogs
            },
            "status": {
                "privacyStatus": "private" # Can be "public", "private", or "unlisted"
            }
        }

        print(f"Uploading {os.path.basename(video_path)} to YouTube...")
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=media)

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"Uploaded {int(status.progress() * 100)}%")

        print(f"Upload successful! Video ID: {response.get('id')}")
        print(f"Watch on YouTube: https://www.youtube.com/watch?v={response.get('id')}")

    except HttpError as e:
        print(f"An HTTP error {e.resp.status} occurred:\n{e.content}")
    except Exception as e:
        print(f"An error occurred during YouTube upload: {e}")

def main():
    video_directory = input("Enter video directory (default: ./videos_to_process): ") or "videos_to_process"
    if not os.path.exists(video_directory):
        os.makedirs(video_directory)
        print(f"Created directory. Add videos and rerun.")
        return

    videos = get_video_files(video_directory)
    if not videos:
        print(f"No videos found in {video_directory}.")
        return

    for video_path in videos:
        suggestions = get_gemini_suggestions(video_path)
        if suggestions:
            print(f"\n--- Suggestions for {os.path.basename(video_path)} ---")
            print(f"Title: {suggestions.get('title')}\nDescription: {suggestions.get('description')}")
            print("------------------------------------\n")

            approval = input("Approve these edits and upload? (yes/no): ").lower()
            if approval == 'yes':
                print("Applying edits...")
                edited_video_path = apply_edits(video_path, suggestions)
                if edited_video_path:
                    upload_to_youtube(edited_video_path, suggestions)
            else:
                print("Skipping video.")
        else:
            print(f"Could not get suggestions for {os.path.basename(video_path)}. Skipping.")

if __name__ == '__main__':
    main()
