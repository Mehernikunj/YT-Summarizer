import streamlit as st
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
from urllib.parse import urlparse, parse_qs
import yt_dlp
import os
import time
import json
import re
import matplotlib.pyplot as plt
import datetime
from wordcloud import WordCloud
from collections import Counter

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="YT Smart Summarizer", page_icon="🎙️", layout="wide")

st.markdown("""
<style>
    .stButton>button { width: 100%; background-color: #FF4B4B; color: white; }
    .reportview-container { background: #0E1117; }
    .speaker-card { background-color: #262730; padding: 15px; border-radius: 10px; border-left: 5px solid #FF4B4B; margin-bottom: 10px; }
    .metric-card { background-color: #1E1E1E; padding: 15px; border-radius: 8px; text-align: center; border: 1px solid #333; }
    .metric-value { font-size: 1.5em; font-weight: bold; color: #4DA6FF; }
    .metric-label { font-size: 0.9em; color: #aaa; }
    .question-box { background-color: #1E1E1E; padding: 10px; border-radius: 5px; margin-bottom: 5px; border-left: 3px solid #4DA6FF; }
    a { text-decoration: none; color: #4DA6FF !important; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# --- 2. SESSION STATE SETUP ---
if "transcript_text" not in st.session_state: st.session_state.transcript_text = None
if "content_type" not in st.session_state: st.session_state.content_type = None
if "podcast_data" not in st.session_state: st.session_state.podcast_data = None
if "summary_result" not in st.session_state: st.session_state.summary_result = None
if "video_meta" not in st.session_state: st.session_state.video_meta = {}
if "audio_path" not in st.session_state: st.session_state.audio_path = None

# --- 3. HELPER FUNCTIONS ---
def extract_video_id(url):
    try:
        query = urlparse(url)
        if query.hostname == 'youtu.be': return query.path[1:]
        if query.hostname in ('www.youtube.com', 'youtube.com'):
            if query.path == '/watch': return parse_qs(query.query)['v'][0]
            if query.path[:7] == '/embed/': return query.path.split('/')[2]
            if query.path[:3] == '/v/': return query.path.split('/')[2]
    except: return None
    return None

def get_video_metadata(url):
    ydl_opts = {'quiet': True, 'skip_download': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": info.get('title', 'Unknown Title'),
                "duration": info.get('duration', 0)
            }
    except:
        return {"title": "Unknown Video", "duration": 0}

def format_duration(seconds):
    if not seconds: return "Unknown"
    return str(datetime.timedelta(seconds=seconds))

def get_transcript_text(video_id):
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        return " ".join([entry['text'] for entry in transcript]), "text"
    except:
        return None, None

def download_audio(url):
    # Generates a unique filename to prevent conflicts
    filename = f"audio_{int(time.time())}.m4a"
    simple_opts = {
        'format': 'bestaudio[ext=m4a]',
        'outtmpl': filename,
        'quiet': True,
        'overwrites': True 
    }
    try:
        with yt_dlp.YoutubeDL(simple_opts) as ydl:
            ydl.download([url])
        return filename
    except Exception as e:
        return None

def clean_json_response(text):
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```', '', text)
    return text.strip()

def make_clickable_timestamps(text, video_id):
    def replace_match(match):
        timestamp = match.group(0)
        parts = timestamp.split(':')
        seconds = int(parts[0]) * 60 + int(parts[1]) if len(parts) == 2 else int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return f"[{timestamp}](https://youtu.be/{video_id}?t={seconds})"
    pattern = r'\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b'
    return re.sub(pattern, replace_match, text)

# --- 4. MODEL PROCESSOR ---
def process_with_gemini(content, input_type, prompt, api_key):
    genai.configure(api_key=api_key)
    model_candidates = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-1.5-flash"]
    
    for model_name in model_candidates:
        try:
            model = genai.GenerativeModel(model_name)
            if "JSON" in prompt:
                model = genai.GenerativeModel(model_name, generation_config={"response_mime_type": "application/json"})
            
            if input_type == "text":
                response = model.generate_content(f"Analyze this transcript: {content}\n\n{prompt}")
                return response.text, model_name 
            elif input_type == "audio":
                audio_file = genai.upload_file(path=content)
                while audio_file.state.name == "PROCESSING":
                    time.sleep(2)
                    audio_file = genai.get_file(audio_file.name)
                response = model.generate_content([prompt, audio_file])
                return response.text, model_name
        except:
            continue
    return None, ["All models failed"]

# --- 5. UI LAYOUT ---
st.title("📺 Smart YouTube Summarizer")

with st.sidebar:
    st.header("⚙️ Configuration")
    user_api_key = st.text_input("Enter Your API Key (Optional)", type="password")
    
    st.divider()
    language = st.selectbox("Output Language:", ["English", "Hindi", "Spanish", "French", "German"])
    
    if user_api_key:
        active_api_key = user_api_key
        st.success("✅ Using: **Your Personal Key**")
    else:
        if "GEMINI_API_KEY" in st.secrets:
            active_api_key = st.secrets["GEMINI_API_KEY"]
            st.info("ℹ️ Using: **Shared Key**")
        else:
            active_api_key = None
            st.warning("⚠️ No Key found.")
    st.link_button("🔑 Get Free Key", "https://aistudio.google.com/app/apikey")

# Main Interface
video_url = st.text_input("🔗 Paste YouTube Link:", placeholder="https://www.youtube.com/watch?v=...")

if video_url:
    video_id = extract_video_id(video_url)
    if video_id:
        st.image(f"http://img.youtube.com/vi/{video_id}/0.jpg", use_container_width=True)
        
        mode = st.selectbox("Select Mode:", [
            "🎙️ Podcast Analysis",
            "📄 General Summary", 
            "📝 Bullet Summary", 
            "🎬 Timestamp Summary", 
            "🎯 Key Insights"
        ])
        
        if st.button("Generate Analysis"):
            if not active_api_key:
                st.error("❌ No API Key available.")
            else:
                with st.spinner("Fetching data & Analyzing content..."):
                    # Cleanup old audio if a new video is loaded
                    if st.session_state.audio_path and os.path.exists(st.session_state.audio_path):
                        os.remove(st.session_state.audio_path)
                        st.session_state.audio_path = None

                    # Get Meta Data
                    st.session_state.video_meta = get_video_metadata(video_url)
                    
                    # Get Content
                    content, c_type = get_transcript_text(video_id)
                    if not content:
                        st.warning("⚠️ Switching to Audio Mode...")
                        content = download_audio(video_url)
                        c_type = "audio"
                        st.session_state.audio_path = content # Save path for later use
                    
                    st.session_state.transcript_text = content 
                    st.session_state.content_type = c_type
                    st.session_state.video_id = video_id
                    st.session_state.podcast_data = None
                    st.session_state.summary_result = None 
                    
                    lang_instruction = f"Respond in {language}."

                    # --- PODCAST MODE LOGIC ---
                    if mode == "🎙️ Podcast Analysis":
                        podcast_prompt = f"""
                        Analyze this podcast. Return STRICT JSON:
                        {{
                            "guest_info": {{ "name": "Name", "bio": "Role/Bio" }},
                            "questions": ["List 5-8 key questions"],
                            "talking_ratio": {{ "host_percentage": 40, "guest_percentage": 60 }},
                            "controversy": ["List 1-3 controversies or 'None'"],
                            "summary": "Comprehensive summary (200 words)."
                        }}
                        {lang_instruction}
                        """
                        result, used_model = process_with_gemini(content, c_type, podcast_prompt, active_api_key)
                        if result:
                            try:
                                clean_json = clean_json_response(result)
                                st.session_state.podcast_data = json.loads(clean_json)
                                st.session_state.summary_result = "Podcast Mode Active"
                                st.session_state.model_used = used_model
                            except:
                                st.error("Failed to parse podcast data.")
                    else:
                        prompts = {
                            "📄 General Summary": f"Provide a general summary. {lang_instruction}",
                            "📝 Bullet Summary": f"Summarize into bullet points. {lang_instruction}",
                            "🎬 Timestamp Summary": f"Create a timeline summary. {lang_instruction}",
                            "🎯 Key Insights": f"Extract top 5 insights. {lang_instruction}"
                        }
                        result, used_model = process_with_gemini(content, c_type, prompts[mode], active_api_key)
                        if result:
                            st.session_state.summary_result = make_clickable_timestamps(result, video_id)
                            st.session_state.model_used = used_model

        # --- DISPLAY RESULTS ---
        if st.session_state.summary_result:
            st.divider()
            
            # --- VIEW 1: PODCAST DASHBOARD ---
            if st.session_state.podcast_data:
                data = st.session_state.podcast_data
                meta = st.session_state.video_meta
                
                # Metrics Calculation
                duration_str = format_duration(meta.get('duration', 0))
                
                # Check Text Availability
                is_text_available = st.session_state.content_type == "text" or (st.session_state.content_type == "audio" and len(str(st.session_state.transcript_text)) > 500)
                
                if is_text_available and st.session_state.content_type == "text":
                    word_count = len(st.session_state.transcript_text.split())
                elif is_text_available and st.session_state.content_type == "audio":
                    word_count = "Generated from Audio"
                else:
                    word_count = "N/A (Audio Mode)"

                # Tab Layout
                tab1, tab2, tab3 = st.tabs(["📊 Metrics & Talk Ratio", "👥 Guest & Questions", "📝 Full Summary & Transcript"])
                
                with tab1:
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        st.markdown(f"<div class='metric-card'><div class='metric-value'>{duration_str}</div><div class='metric-label'>⏱️ Total Duration</div></div>", unsafe_allow_html=True)
                    with c2:
                        st.markdown(f"<div class='metric-card'><div class='metric-value'>{word_count}</div><div class='metric-label'>📝 Word Count</div></div>", unsafe_allow_html=True)
                    with c3:
                         title_short = meta.get('title', 'Unknown')[:20] + "..."
                         st.markdown(f"<div class='metric-card'><div class='metric-value'>{title_short}</div><div class='metric-label'>📺 Video Title</div></div>", unsafe_allow_html=True)
                    
                    st.divider()
                    
                    col1, col2 = st.columns([1, 1])
                    with col1:
                        st.subheader("🗣️ Talking Ratio")
                        ratio = data.get('talking_ratio', {'host_percentage': 50, 'guest_percentage': 50})
                        labels = ['Host', 'Guest']
                        sizes = [ratio.get('host_percentage', 50), ratio.get('guest_percentage', 50)]
                        colors = ['#FF4B4B', '#4DA6FF']
                        
                        fig, ax = plt.subplots(figsize=(4, 4))
                        ax.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', startangle=90, textprops={'color':"white"})
                        fig.patch.set_facecolor('#0E1117') 
                        st.pyplot(fig)

                with tab2:
                    st.subheader("👤 Guest Profile")
                    st.markdown(f"<div class='speaker-card'><h3>{data.get('guest_info', {}).get('name', 'Unknown')}</h3><p>{data.get('guest_info', {}).get('bio', '')}</p></div>", unsafe_allow_html=True)
                    st.subheader("❓ Key Questions")
                    for q in data.get('questions', []):
                        st.markdown(f"<div class='question-box'>🔹 {q}</div>", unsafe_allow_html=True)

                with tab3:
                    st.subheader("📝 Total Podcast Summary")
                    st.markdown(data.get('summary', ''))
                    st.download_button("📥 Download Summary", data.get('summary', ''), "podcast_summary.txt")
                    
                    st.divider()
                    st.subheader("📜 Full Transcript")
                    
                    # LOGIC: If we have text, show it. If Audio, offer to generate it.
                    if st.session_state.content_type == "text":
                         st.text_area("Transcript", st.session_state.transcript_text, height=300)
                    else:
                        # Audio Mode Logic
                        if "generated_transcript" in st.session_state:
                             st.success("✅ Transcript Generated!")
                             st.text_area("Generated Transcript", st.session_state.generated_transcript, height=300)
                        else:
                             st.warning("⚠️ Full text is unavailable in Audio Mode.")
                             if st.button("✨ Generate Transcript from Audio (Takes ~30s)"):
                                 if st.session_state.audio_path and os.path.exists(st.session_state.audio_path):
                                     with st.spinner("🎧 Listening to audio and transcribing..."):
                                         # Request specific transcription
                                         transcription, _ = process_with_gemini(st.session_state.audio_path, "audio", "Generate a verbatim transcript of this audio. Do not summarize, just transcribe.", active_api_key)
                                         if transcription:
                                             st.session_state.generated_transcript = transcription
                                             st.rerun() # Refresh to show the text area
                                 else:
                                     st.error("Audio file lost. Please re-generate the analysis.")

            # --- VIEW 2: STANDARD SUMMARY ---
            else:
                st.subheader("💡 Analysis Result")
                st.markdown(st.session_state.summary_result)
                st.download_button("📥 Download Summary", st.session_state.summary_result, "summary.txt")