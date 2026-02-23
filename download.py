import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import subprocess
import threading
import os

class YTDLPGui:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube Audio Downloader")
        self.root.geometry("700x600")
        self.root.resizable(True, True)
        
        # Default paths - customize these for your system
        self.ytdlp_path = r"C:\ytdlp\yt-dlp.exe"
        self.ffmpeg_path = r"C:\ffmpeg\bin"
        self.default_output = os.path.expanduser("~")  # User's home directory
        
        self.create_widgets()
        
    def create_widgets(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        
        # URL input
        ttk.Label(main_frame, text="YouTube URL:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.url_entry = ttk.Entry(main_frame, width=50)
        self.url_entry.grid(row=0, column=1, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        # Output folder
        ttk.Label(main_frame, text="Output Folder:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.output_entry = ttk.Entry(main_frame, width=50)
        self.output_entry.insert(0, self.default_output)
        self.output_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5)
        
        browse_btn = ttk.Button(main_frame, text="Browse", command=self.browse_folder)
        browse_btn.grid(row=1, column=2, padx=5, pady=5)
        
        # Options frame
        options_frame = ttk.LabelFrame(main_frame, text="Options", padding="10")
        options_frame.grid(row=2, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10)
        options_frame.columnconfigure(0, weight=1)
        
        # Playlist option
        self.playlist_var = tk.BooleanVar(value=False)
        playlist_check = ttk.Checkbutton(options_frame, text="Download entire playlist", 
                                        variable=self.playlist_var, command=self.toggle_playlist)
        playlist_check.grid(row=0, column=0, sticky=tk.W)
        
        # Playlist organization option (hidden by default)
        self.organize_var = tk.BooleanVar(value=True)
        self.organize_check = ttk.Checkbutton(options_frame, 
                                             text="Organize playlist in folder with numbered files",
                                             variable=self.organize_var)
        self.organize_check.grid(row=1, column=0, sticky=tk.W, padx=20)
        self.organize_check.grid_remove()  # Hide initially
        
        # Audio quality
        quality_frame = ttk.Frame(options_frame)
        quality_frame.grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Label(quality_frame, text="Audio Quality:").pack(side=tk.LEFT)
        self.quality_var = tk.StringVar(value="0")
        quality_combo = ttk.Combobox(quality_frame, textvariable=self.quality_var, 
                                    values=["0 (Best)", "5 (Medium)", "9 (Low)"],
                                    width=15, state="readonly")
        quality_combo.pack(side=tk.LEFT, padx=5)
        quality_combo.current(0)
        
        # Paths configuration frame
        paths_frame = ttk.LabelFrame(main_frame, text="Configuration", padding="10")
        paths_frame.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10)
        paths_frame.columnconfigure(1, weight=1)
        
        ttk.Label(paths_frame, text="yt-dlp path:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.ytdlp_entry = ttk.Entry(paths_frame)
        self.ytdlp_entry.insert(0, self.ytdlp_path)
        self.ytdlp_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), pady=2, padx=5)
        
        ttk.Label(paths_frame, text="FFmpeg path:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.ffmpeg_entry = ttk.Entry(paths_frame)
        self.ffmpeg_entry.insert(0, self.ffmpeg_path)
        self.ffmpeg_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=2, padx=5)
        
        # Download button
        self.download_btn = ttk.Button(main_frame, text="Download MP3", 
                                      command=self.start_download)
        self.download_btn.grid(row=4, column=0, columnspan=3, pady=10)
        
        # Progress/Status area
        ttk.Label(main_frame, text="Status:").grid(row=5, column=0, sticky=tk.W)
        self.status_text = scrolledtext.ScrolledText(main_frame, height=15, width=70, 
                                                     wrap=tk.WORD, state='disabled')
        self.status_text.grid(row=6, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        
        # Configure row weight for status text to expand
        main_frame.rowconfigure(6, weight=1)
        
    def toggle_playlist(self):
        if self.playlist_var.get():
            self.organize_check.grid()
        else:
            self.organize_check.grid_remove()
    
    def browse_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_entry.get())
        if folder:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, folder)
    
    def log_status(self, message):
        self.status_text.config(state='normal')
        self.status_text.insert(tk.END, message + "\n")
        self.status_text.see(tk.END)
        self.status_text.config(state='disabled')
        self.root.update_idletasks()
    
    def start_download(self):
        url = self.url_entry.get().strip()
        if not url:
            self.log_status("ERROR: Please enter a YouTube URL")
            return
        
        # Disable button during download
        self.download_btn.config(state='disabled')
        
        # Start download in separate thread to prevent GUI freezing
        thread = threading.Thread(target=self.download, args=(url,))
        thread.daemon = True
        thread.start()
    
    def download(self, url):
        try:
            ytdlp_path = self.ytdlp_entry.get()
            ffmpeg_path = self.ffmpeg_entry.get()
            output_folder = self.output_entry.get()
            quality = self.quality_var.get().split()[0]  # Get just the number
            
            # Build command
            cmd = [
                ytdlp_path,
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", quality,
                "--no-check-certificate",
                "--ffmpeg-location", ffmpeg_path
            ]
            
            # Add output template
            if self.playlist_var.get() and self.organize_var.get():
                output_template = os.path.join(output_folder, "%(playlist)s", "%(playlist_index)02d - %(title)s.%(ext)s")
            else:
                output_template = os.path.join(output_folder, "%(title)s.%(ext)s")
            
            cmd.extend(["-o", output_template])
            cmd.append(url)
            
            self.log_status("Starting download...")
            self.log_status(f"Command: {' '.join(cmd)}")
            self.log_status("-" * 70)
            
            # Run yt-dlp
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            # Stream output
            for line in process.stdout:
                self.log_status(line.strip())
            
            process.wait()
            
            if process.returncode == 0:
                self.log_status("-" * 70)
                self.log_status("✓ Download completed successfully!")
            else:
                self.log_status("-" * 70)
                self.log_status("✗ Download failed. Check the errors above.")
                
        except Exception as e:
            self.log_status(f"ERROR: {str(e)}")
        
        finally:
            # Re-enable button
            self.download_btn.config(state='normal')

if __name__ == "__main__":
    root = tk.Tk()
    app = YTDLPGui(root)
    root.mainloop()