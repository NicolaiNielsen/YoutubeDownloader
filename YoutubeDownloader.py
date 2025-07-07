import tkinter as tk
from tkinter import ttk
import threading
import yt_dlp

def fetch_playlists_yt_dlp(channel_url):
    """Returns a list of (playlist_title, playlist_url) from a YouTube channel using yt-dlp."""
    ydl_opts = {
        'extract_flat': True,
        'skip_download': True,
        'dump_single_json': True,
    }
    playlists = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
        # Debug: print raw output to understand what is found
        # print(info)
        for entry in info.get('entries', []):
            # Accept anything with _type=='playlist' or YoutubePlaylist, OR anything with a playlist url/id
            if entry.get('_type') == 'playlist' or entry.get('ie_key') == 'YoutubePlaylist':
                title = entry.get('title', 'Untitled Playlist')
                pl_url = entry.get('url')
                if pl_url and not pl_url.startswith('http'):
                    pl_url = f"https://www.youtube.com/playlist?list={pl_url}"
                playlists.append((title, pl_url))
        # If no playlists found but channel has videos, add an "All Uploads" pseudo-playlist
        if not playlists and 'entries' in info:
            playlists.append(("All Uploads (as playlist)", channel_url))
    return playlists

class PlaylistApp:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube Playlist Picker (yt-dlp)")

        # Channel input
        ttk.Label(root, text="YouTube Channel URL:").pack(pady=(10, 0))
        self.url_entry = ttk.Entry(root, width=60)
        self.url_entry.pack(pady=(0, 10))

        # Fetch button
        ttk.Button(root, text="Fetch Playlists", command=self.start_fetch_thread).pack()

        # Playlists frame (scrollable!)
        self.scroll_canvas = tk.Canvas(root, height=300)
        self.scroll_frame = ttk.Frame(self.scroll_canvas)
        self.scrollbar = ttk.Scrollbar(root, orient="vertical", command=self.scroll_canvas.yview)
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.scroll_canvas.pack(side="left", fill="both", expand=True)
        self.scroll_canvas.create_window((0,0), window=self.scroll_frame, anchor='nw')

        self.scroll_frame.bind("<Configure>", lambda e: self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all")))
        self.check_vars = []

        # Download button
        ttk.Button(root, text="Download Selected (stub)", command=self.download_selected).pack(pady=(10, 10))

        # Status
        self.status = tk.StringVar(value="")
        ttk.Label(root, textvariable=self.status).pack()

    def start_fetch_thread(self):
        threading.Thread(target=self.fetch_playlists, daemon=True).start()

    def fetch_playlists(self):
        url = self.url_entry.get().strip()
        self.status.set("Fetching playlists...")
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()
        self.check_vars = []
        try:
            playlists = fetch_playlists_yt_dlp(url)
            if not playlists:
                self.status.set("No playlists found.")
                return
            self.status.set(f"Found {len(playlists)} playlist(s)")
            for idx, (title, pl_url) in enumerate(playlists):
                var = tk.BooleanVar()
                # Playlist name and url (show name, url in tooltip)
                frame = ttk.Frame(self.scroll_frame)
                frame.pack(anchor="w", fill="x", padx=5, pady=2)
                chk = ttk.Checkbutton(frame, text=title, variable=var)
                chk.pack(side="left")
                # Add a label or button to copy url (optional)
                lbl = ttk.Label(frame, text=pl_url, foreground="blue", cursor="hand2")
                lbl.pack(side="left", padx=5)
                lbl.bind("<Button-1>", lambda e, url=pl_url: self.root.clipboard_append(url))
                self.check_vars.append((var, title, pl_url))
        except Exception as e:
            self.status.set("Error: " + str(e))

    def download_selected(self):
        selected = [(title, url) for var, title, url in self.check_vars if var.get()]
        if not selected:
            self.status.set("No playlist selected.")
        else:
            self.status.set(f"Selected: {', '.join([title for title, _ in selected])}")
            # Here you would trigger the download logic

if __name__ == "__main__":
    root = tk.Tk()
    app = PlaylistApp(root)
    root.mainloop()
