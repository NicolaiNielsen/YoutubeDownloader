import tkinter as tk
from tkinter import ttk
import threading
import yt_dlp
import os
import requests
import re
import json5
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image, ImageTk
from ttkthemes import ThemedTk

os.environ["PATH"] += os.pathsep + r"C:\ffmpeg\bin"

def convert_webp_to_jpg(webp_path):
    jpg_path = os.path.splitext(webp_path)[0] + ".jpg"
    try:
        im = Image.open(webp_path).convert("RGB")
        im.save(jpg_path, "JPEG")
        return jpg_path
    except Exception as e:
        print("Failed to convert webp to jpg:", e)
        return None

def prettify_tags(tags):
    return (
        f"\n  Title   : {tags.get('title','')}\n"
        f"  Artist  : {tags.get('artist','')}\n"
        f"  Album   : {tags.get('album','')}\n"
        f"  Genre   : {tags.get('genre','')}\n"
        f"  Year    : {tags.get('year','')}\n"
        f"  Remix   : {'Yes (' + tags['remixer'] + ')' if tags.get('remix') and tags.get('remixer') else 'No'}\n"
    )

def ollama_parse_song_meta(filename, playlist, yt_info, model='llama3'):
    video_title = yt_info.get("title", "")
    channel = yt_info.get("channel", "") or yt_info.get("uploader", "")
    description = yt_info.get("description", "")
    playlist_name = playlist
    prompt = (
        "Given the following YouTube video metadata, return ONLY valid, strict JSON for correct music tagging.\n"
        "Extract true song title and artist, with NO hallucinations. If not obvious, leave blank. "
        "Do NOT invent artists/titles. If remix/remixer is present in title/desc, set 'remix': true and fill 'remixer'.\n"
        "Strip YouTube fluff like 'official video', 'lyrics', 'audio', 'visualizer', channel branding, etc. from title/artist.\n"
        "JSON keys: title, artist, remix (bool), remixer, year, genre, album\n"
        "Never add feat. or remix unless present in the actual title.\n"
        f"Video Title: \"{video_title}\"\n"
        f"Channel: \"{channel}\"\n"
        f"Playlist: \"{playlist_name}\"\n"
        f"Description: \"{description[:300]}\"\n"
        "Respond ONLY with JSON. No extra text.\n"
        "Example: {\"title\":\"Song Title\",\"artist\":\"Artist\",\"remix\":false,\"remixer\":\"\",\"year\":\"\",\"genre\":\"\",\"album\":\"Playlist\"}\n"
    )
    data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False
    }
    try:
        resp = requests.post("http://localhost:11434/api/chat", json=data, timeout=60)
        resp.raise_for_status()
        text = resp.json()["message"]["content"]
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            raise ValueError("No JSON found in Ollama output!")
        json_part = match.group(0)
        tags = json5.loads(json_part)
        return tags
    except Exception as e:
        print("Ollama failed, using fallback:", e)
        return {
            "title": os.path.splitext(os.path.basename(filename))[0],
            "artist": "",
            "remix": False,
            "remixer": "",
            "year": "",
            "genre": "",
            "album": playlist
        }

def set_metadata(filename, tags):
    audio = MP4(filename)
    if tags.get("title"): audio["\xa9nam"] = tags["title"]
    if tags.get("artist"): audio["\xa9ART"] = tags["artist"]
    if tags.get("album"): audio["\xa9alb"] = tags["album"]
    if tags.get("genre"): audio["\xa9gen"] = tags["genre"]
    if tags.get("year"): audio["\xa9day"] = tags["year"]
    if tags.get("remix") and tags.get("remixer"):
        audio["----:com.apple.iTunes:COMMENT"] = tags["remixer"].encode("utf-8")
    # Album art
    base = os.path.splitext(filename)[0]
    for ext in ('.jpg', '.jpeg', '.png', '.webp'):
        imgpath = base + ext
        if os.path.exists(imgpath):
            # Convert webp to jpg for compatibility
            if ext == '.webp':
                jpg = convert_webp_to_jpg(imgpath)
                if jpg and os.path.exists(jpg):
                    with open(jpg, 'rb') as img2:
                        data = img2.read()
                        cov = MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)
                        audio["covr"] = [cov]
                    break
            else:
                with open(imgpath, 'rb') as img:
                    data = img.read()
                    if ext == '.png':
                        cov = MP4Cover(data, imageformat=MP4Cover.FORMAT_PNG)
                    else:
                        cov = MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)
                    audio["covr"] = [cov]
                break
    audio.save()

def rename_file_to_title(filename, title):
    ext = os.path.splitext(filename)[1]
    folder = os.path.dirname(filename)
    clean_title = "".join([c for c in title if c not in '\\/:*?"<>|'])
    new_filename = os.path.join(folder, f"{clean_title}{ext}")
    if filename != new_filename and not os.path.exists(new_filename):
        os.rename(filename, new_filename)
    return new_filename

def fetch_playlists_yt_dlp(channel_url):
    ydl_opts = {
        'extract_flat': True,
        'skip_download': True,
        'dump_single_json': True,
    }
    playlists = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
        for entry in info.get('entries', []):
            if (
                entry.get('_type') == 'url'
                and 'playlist?list=' in str(entry.get('url'))
            ):
                title = entry.get('title', 'Untitled Playlist')
                pl_url = entry.get('url')
                playlists.append((title, pl_url))
        if not playlists:
            playlists.append(("All Uploads (as playlist)", channel_url))
    return playlists

def find_downloaded_m4a_files(download_dir):
    m4a_files = []
    for root, dirs, files in os.walk(download_dir):
        for file in files:
            if file.endswith('.m4a'):
                m4a_files.append(os.path.join(root, file))
    return m4a_files

def get_playlist_info_dict(playlist_url):
    ydl = yt_dlp.YoutubeDL({'extract_flat': False, 'skip_download': True, 'quiet': True})
    info = ydl.extract_info(playlist_url, download=False)
    # If playlist, entries is a list; else it's a single dict
    entry_map = {}
    if isinstance(info, dict) and 'entries' in info:
        for entry in info['entries']:
            if entry and 'id' in entry:
                entry_map[entry['id']] = entry
    elif isinstance(info, dict) and 'id' in info:
        entry_map[info['id']] = info
    return info.get("title",""), entry_map

def get_video_id_from_filename(filename):
    # Try to parse video ID from file name, fallback: None
    # Sometimes yt-dlp includes [ID] at the end, or you can match YouTube 11-char IDs
    match = re.search(r"\[([a-zA-Z0-9_-]{11})\]", filename)
    if match:
        return match.group(1)
    return None

def download_audio_from_playlist(playlist_url, status_callback, progress_callback, done_callback, albumart_callback):
    # Get playlist info dict BEFORE downloading, so we can match videos to .m4a files later!
    playlist_name, entry_map = get_playlist_info_dict(playlist_url)
    outtmpl = os.path.join(os.getcwd(), '%(playlist_title)s/%(title)s.%(ext)s')

    def on_progress(d):
        status_callback(d)
        progress_callback(d)

    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'outtmpl': outtmpl,
        'noplaylist': False,
        'quiet': True,
        'writethumbnail': True,
        'ffmpeg_location': r'C:\ffmpeg\bin',
        'progress_hooks': [on_progress],
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
            }
        ],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([playlist_url])

    # --- Postprocessing, tagging, renaming ---
    m4a_files = find_downloaded_m4a_files(os.getcwd())
    for m4a_file in m4a_files:
        original_name = os.path.basename(m4a_file)
        video_id = get_video_id_from_filename(original_name)
        yt_info = {}
        if video_id and video_id in entry_map:
            yt_info = entry_map[video_id]
        else:
            # fallback: just use title only
            yt_info = {"title": os.path.splitext(original_name)[0]}

        tags = ollama_parse_song_meta(m4a_file, playlist_name, yt_info)
        set_metadata(m4a_file, tags)
        newname = rename_file_to_title(m4a_file, tags['title'])
        newname_short = os.path.basename(newname)

        pretty_tags = prettify_tags(tags)
        log_msg = (
            f"🔄 Postprocessing complete!\n"
            f"Original filename: {original_name}\n"
            f"Renamed to      : {newname_short}\n"
            f"Metadata set to : {pretty_tags}\n"
        )
        status_callback({'status': 'tagged', 'log': log_msg})

        # Album art update
        base = os.path.splitext(newname)[0]
        for ext in ('.jpg', '.jpeg', '.png', '.webp'):
            imgpath = base + ext
            if os.path.exists(imgpath):
                albumart_callback(imgpath)
                break

    done_callback()

class PlaylistApp:
    def __init__(self, root):
        self.root = root
        self.root.title("VibeTube: YouTube Playlist Picker + Smart AI Tagging 🎵")
        self.root.configure(bg="#161616")
        self.albumart_img = None
        self.font = ("Segoe UI", 12)
        ttk.Style().theme_use('clam')

        self.header = tk.Label(root, text="YouTube Playlist Downloader", fg="#fff", bg="#161616", font=("Segoe UI", 22, "bold"))
        self.header.pack(pady=(12, 4))
        url_frame = tk.Frame(root, bg="#161616")
        url_frame.pack(pady=(4,0))
        tk.Label(url_frame, text="Channel URL:", fg="#fff", bg="#161616", font=self.font).pack(side="left")
        self.url_entry = ttk.Entry(url_frame, width=70, font=self.font)
        self.url_entry.pack(side="left", padx=8)
        self.url_entry.insert(0, "https://www.youtube.com/@EmmaMusica703")
        ttk.Button(url_frame, text="Fetch Playlists", command=self.start_fetch_thread).pack(side="left")
        frame_container = tk.Frame(root, bg="#161616")
        frame_container.pack(fill="both", expand=True)
        self.scroll_canvas = tk.Canvas(frame_container, height=260, bg="#232323", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(frame_container, orient="vertical", command=self.scroll_canvas.yview)
        self.scroll_frame = tk.Frame(self.scroll_canvas, bg="#232323")
        self.scroll_frame.bind("<Configure>", lambda e: self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all")))
        self.scroll_canvas.create_window((0,0), window=self.scroll_frame, anchor='nw')
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scroll_canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.check_vars = []
        ttk.Button(root, text="Download Selected", command=self.download_selected).pack(pady=(10, 6))

        self.art_canvas = tk.Canvas(root, width=128, height=128, bg="#232323", bd=0, highlightthickness=0)
        self.art_canvas.pack(pady=(2, 8))
        self.show_albumart(None)

        self.progressbar = ttk.Progressbar(root, orient="horizontal", length=340, mode="determinate")
        self.progressbar.pack(pady=(4, 2))
        self.progressbar["value"] = 0
        self.progressbar["maximum"] = 100
        self.statuslog = tk.Text(root, height=13, width=75, bg="#1e1e1e", fg="#b6fcd5", font=("Consolas", 11), bd=0)
        self.statuslog.pack(padx=8, pady=(4,8))
        self.statuslog.insert("end", "✨ Ready. Paste a channel URL and fetch playlists.\n")
        self.statuslog.config(state="disabled")

    def log(self, msg, kind="info"):
        self.statuslog.config(state="normal")
        color = {"info":"#b6fcd5", "ok":"#f3bfff", "done":"#79ff89", "warn":"#ffc66a", "err":"#ff6161"}
        self.statuslog.insert("end", msg+"\n")
        self.statuslog.tag_add(kind, "end-%dc" % (len(msg)+1), "end-1c")
        self.statuslog.tag_config(kind, foreground=color.get(kind,"#b6fcd5"))
        self.statuslog.see("end")
        self.statuslog.config(state="disabled")

    def show_albumart(self, imgpath):
        self.art_canvas.delete("all")
        if imgpath and os.path.exists(imgpath):
            try:
                img = Image.open(imgpath)
                img = img.resize((128,128), Image.LANCZOS)
                self.albumart_img = ImageTk.PhotoImage(img)
                self.art_canvas.create_image(64,64, image=self.albumart_img)
            except Exception as e:
                self.log(f"Album art error: {e}", "warn")
        else:
            self.art_canvas.create_rectangle(0,0,128,128, fill="#232323", outline="#333")

    def start_fetch_thread(self):
        threading.Thread(target=self.fetch_playlists, daemon=True).start()

    def fetch_playlists(self):
        url = self.url_entry.get().strip()
        if "/playlists" not in url:
            if url.startswith("https://www.youtube.com/@"):
                url = url.rstrip('/') + "/playlists"
        self.log("Fetching playlists from: " + url)
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()
        self.check_vars = []
        try:
            playlists = fetch_playlists_yt_dlp(url)
            if not playlists:
                self.log("No playlists found.", "warn")
                return
            self.log(f"Found {len(playlists)} playlist(s)", "ok")
            for idx, (title, pl_url) in enumerate(playlists):
                var = tk.BooleanVar()
                frame = tk.Frame(self.scroll_frame, bg="#232323")
                frame.pack(anchor="w", fill="x", padx=10, pady=2)
                chk = ttk.Checkbutton(frame, text=title, variable=var)
                chk.pack(side="left")
                lbl = tk.Label(frame, text=pl_url, fg="#86d6ff", bg="#232323", cursor="hand2", font=("Segoe UI", 9, "underline"))
                lbl.pack(side="left", padx=10)
                lbl.bind("<Button-1>", lambda e, url=pl_url: self.copy_to_clipboard(url))
                self.check_vars.append((var, title, pl_url))
        except Exception as e:
            self.log("Error: " + str(e), "err")

    def copy_to_clipboard(self, url):
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.log("Copied URL to clipboard.", "ok")

    def download_selected(self):
        selected = [(title, url) for var, title, url in self.check_vars if var.get()]
        if not selected:
            self.log("No playlist selected.", "warn")
            return
        self.progressbar["value"] = 0
        self.progressbar["maximum"] = 100
        for title, url in selected:
            self.log(f"Downloading '{title}' as m4a...", "info")
            threading.Thread(
                target=download_audio_from_playlist,
                args=(
                    url,
                    self.update_status_from_thread,
                    self.update_progressbar_from_thread,
                    self.download_done_from_thread,
                    self.show_albumart
                ),
                daemon=True
            ).start()

    def update_status_from_thread(self, d):
        if isinstance(d, dict):
            if d.get('status') == 'downloading':
                filename = d.get('filename', '')
                percent = d.get('_percent_str', '').strip()
                msg = f"⬇️ Downloading: {os.path.basename(filename)} | {percent}"
                self.log(msg, "info")
            elif d.get('status') == 'finished':
                filename = d.get('filename', '')
                msg = f"✅ Downloaded: {os.path.basename(filename)}"
                self.log(msg, "ok")
            elif d.get('status') == 'tagged':
                msg = d.get('log', '')
                self.log(msg, "done")
            else:
                msg = d.get('status', '')
        else:
            msg = str(d)

    def update_progressbar_from_thread(self, d):
        if isinstance(d, dict) and d.get('status') == 'downloading':
            percent_str = d.get('_percent_str', '').strip()
            try:
                percent = float(percent_str.strip('%'))
            except:
                percent = 0
            self.root.after(0, self.progressbar.config, {'value': percent})
        elif isinstance(d, dict) and d.get('status') == 'finished':
            self.root.after(0, self.progressbar.config, {'value': 100})

    def download_done_from_thread(self):
        self.log("✨ Done!", "done")

if __name__ == "__main__":
    root = ThemedTk(theme="equilux")
    app = PlaylistApp(root)
    root.mainloop()
