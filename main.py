import os
import re
import time
import threading
import subprocess
from urllib.parse import urlparse, unquote
import requests
import cloudscraper
import customtkinter as ctk
from tkinter import filedialog, messagebox

# Konfiguracja wyglądu CustomTkinter
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class MultiPartDownloaderApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Multi-Part Downloader & Auto-Extractor")
        self.geometry("850x650")
        self.minsize(750, 600)

        # Zmienne stanu
        self.is_downloading = False
        self.is_paused = False
        self.stop_requested = False
        self.download_folder = os.path.join(os.getcwd(), "downloads")
        os.makedirs(self.download_folder, exist_ok=True)

        # Inicjalizacja Cloudscraper (omija błąd 403 Forbidden / Cloudflare)
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )

        # Domyślne ścieżki do 7-Zip
        self.seven_zip_path = self.find_7zip()

        self.setup_ui()

    def find_7zip(self):
        """Szuka pliku wykonywalnego 7-Zip w standardowych lokalizacjach."""
        paths = [
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
            "7z"
        ]
        for path in paths:
            try:
                subprocess.run([path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return path
            except FileNotFoundError:
                continue
        return None

    def setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(5, weight=1)

        # 1. Sekcja wyboru folderu
        folder_frame = ctk.CTkFrame(self)
        folder_frame.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="ew")
        folder_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(folder_frame, text="Folder docelowy:", font=("Arial", 13, "bold")).grid(row=0, column=0, padx=10, pady=10)
        self.folder_entry = ctk.CTkEntry(folder_frame)
        self.folder_entry.grid(row=0, column=1, padx=5, pady=10, sticky="ew")
        self.folder_entry.insert(0, self.download_folder)

        ctk.CTkButton(folder_frame, text="Przeglądaj...", width=110, command=self.choose_folder).grid(row=0, column=2, padx=10, pady=10)

        # 2. Sekcja wklejania linków
        links_frame = ctk.CTkFrame(self)
        links_frame.grid(row=1, column=0, padx=15, pady=5, sticky="nsew")
        links_frame.grid_columnconfigure(0, weight=1)
        links_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(links_frame, text="Wklej linki (obsługuje fuckingfast.co oraz linki bezpośrednie):", font=("Arial", 13, "bold")).grid(row=0, column=0, padx=10, pady=(8, 2), sticky="w")
        self.links_textbox = ctk.CTkTextbox(links_frame, height=130)
        self.links_textbox.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")

        # 3. Opcje dodatkowe
        options_frame = ctk.CTkFrame(self)
        options_frame.grid(row=2, column=0, padx=15, pady=5, sticky="ew")

        self.auto_extract_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(options_frame, text="Automatycznie wypakuj po pobraniu (7-Zip)", variable=self.auto_extract_var).pack(side="left", padx=15, pady=10)

        self.delete_archives_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(options_frame, text="Usuń archiwa .rar/.001 po udanym wypakowaniu", variable=self.delete_archives_var).pack(side="left", padx=15, pady=10)

        # 4. Paski postępu i statystyki
        progress_frame = ctk.CTkFrame(self)
        progress_frame.grid(row=3, column=0, padx=15, pady=5, sticky="ew")
        progress_frame.grid_columnconfigure(0, weight=1)

        self.current_file_label = ctk.CTkLabel(progress_frame, text="Oczekiwanie na rozpoczęcie...", anchor="w", font=("Arial", 12, "bold"))
        self.current_file_label.grid(row=0, column=0, padx=12, pady=(8, 2), sticky="ew")

        self.file_progress = ctk.CTkProgressBar(progress_frame)
        self.file_progress.grid(row=1, column=0, padx=12, pady=4, sticky="ew")
        self.file_progress.set(0)

        self.stats_label = ctk.CTkLabel(progress_frame, text="Prędkość: 0.00 MB/s  |  Pobrano: 0 / 0 MB  |  ETA: --:--", anchor="w")
        self.stats_label.grid(row=2, column=0, padx=12, pady=2, sticky="ew")

        self.overall_label = ctk.CTkLabel(progress_frame, text="Postęp kolejki: 0 / 0 plików", anchor="w")
        self.overall_label.grid(row=3, column=0, padx=12, pady=(6, 2), sticky="ew")

        self.overall_progress = ctk.CTkProgressBar(progress_frame, progress_color="#2ecc71")
        self.overall_progress.grid(row=4, column=0, padx=12, pady=(2, 10), sticky="ew")
        self.overall_progress.set(0)

        # 5. Przyciski sterujące
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=4, column=0, padx=15, pady=5, sticky="ew")
        btn_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.start_btn = ctk.CTkButton(btn_frame, text="▶ Rozpocznij Pobieranie", fg_color="#27ae60", hover_color="#2ecc71", font=("Arial", 13, "bold"), height=36, command=self.start_download)
        self.start_btn.grid(row=0, column=0, padx=5, sticky="ew")

        self.pause_btn = ctk.CTkButton(btn_frame, text="⏸ Pauza", fg_color="#f39c12", hover_color="#e67e22", font=("Arial", 13, "bold"), height=36, state="disabled", command=self.toggle_pause)
        self.pause_btn.grid(row=0, column=1, padx=5, sticky="ew")

        self.stop_btn = ctk.CTkButton(btn_frame, text="⏹ Anuluj", fg_color="#c0392b", hover_color="#e74c3c", font=("Arial", 13, "bold"), height=36, state="disabled", command=self.stop_download)
        self.stop_btn.grid(row=0, column=2, padx=5, sticky="ew")

        # 6. Konsola logów i błędów
        log_frame = ctk.CTkFrame(self)
        log_frame.grid(row=5, column=0, padx=15, pady=(5, 15), sticky="nsew")
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(log_frame, text="Dziennik zdarzeń i błędów:", font=("Arial", 12, "bold")).grid(row=0, column=0, padx=10, pady=(6, 2), sticky="w")
        self.log_textbox = ctk.CTkTextbox(log_frame, height=110, state="disabled", font=("Consolas", 12))
        self.log_textbox.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")

        if not self.seven_zip_path:
            self.log("[OSTRZEŻENIE] Nie wykryto programu 7-Zip. Automatyczne wypakowywanie nie będzie działać!")
        else:
            self.log(f"[INFO] Wykryto 7-Zip: {self.seven_zip_path}")

    def log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_textbox.configure(state="normal")
        self.log_textbox.insert("end", f"[{timestamp}] {message}\n")
        self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.download_folder)
        if folder:
            self.download_folder = folder
            self.folder_entry.delete(0, "end")
            self.folder_entry.insert(0, folder)

    def resolve_hoster_url(self, url):
        """
        Rozwiązuje linki z serwisów hostingowych (np. fuckingfast.co)
        i zwraca: (bezpośredni_link_do_pobrania, sugerowana_nazwa_pliku)
        """
        parsed = urlparse(url)
        # W linkach FitGirl nazwa pliku często jest po znaku '#' (fragment)
        hint_filename = unquote(parsed.fragment) if parsed.fragment else None
        clean_url = url.split('#')[0]

        if "fuckingfast.co" in parsed.netloc:
            self.log(f"[PARSOWANIE] Wyciąganie bezpośredniego linku z: {clean_url}")
            resp = self.scraper.get(clean_url, timeout=20)
            resp.raise_for_status()

            # Szukanie ukrytego linku w kodzie strony: window.open("https://fuckingfast.co/dl/...")
            match = re.search(r'window\.open\(["\'](https://fuckingfast\.co/dl/[^"\']+)["\']\)', resp.text)
            if not match:
                match = re.search(r'(https://fuckingfast\.co/dl/[a-zA-Z0-9_-]+)', resp.text)

            if match:
                direct_url = match.group(1)
                # Jeśli w URL nie było '#', spróbuj wyciągnąć nazwę z tytułu strony HTML
                if not hint_filename:
                    title_match = re.search(r'<title>([^<]+)</title>', resp.text)
                    if title_match:
                        hint_filename = title_match.group(1).replace(" - FuckingFast", "").strip()
                return direct_url, hint_filename
            else:
                raise ValueError("Nie znaleziono bezpośredniego linku '/dl/' w kodzie strony FuckingFast.")

        return clean_url, hint_filename

    def get_filename_from_url(self, url, response, hint_filename=None):
        """Ustala nazwę pliku na podstawie: 1. #fragmentu, 2. Content-Disposition, 3. URL."""
        if hint_filename:
            # Oczyszczenie nazwy z niedozwolonych znaków Windows
            return re.sub(r'[<>:"/\\|?*]', '_', hint_filename)

        if "Content-Disposition" in response.headers:
            cd = response.headers["Content-Disposition"]
            fname = re.findall(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';]+)["\']?', cd)
            if fname:
                return unquote(fname[0])

        parsed = urlparse(url)
        name = unquote(os.path.basename(parsed.path))
        return name if name else f"plik_{int(time.time())}.rar"

    def start_download(self):
        raw_links = self.links_textbox.get("1.0", "end").strip().splitlines()
        links = [link.strip() for link in raw_links if link.strip().startswith("http")]

        if not links:
            messagebox.showwarning("Brak linków", "Wklej przynajmniej jeden prawidłowy link zaczynający się od http/https!")
            return

        self.download_folder = self.folder_entry.get().strip()
        os.makedirs(self.download_folder, exist_ok=True)

        self.is_downloading = True
        self.is_paused = False
        self.stop_requested = False

        self.start_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="⏸ Pauza")
        self.stop_btn.configure(state="normal")
        self.links_textbox.configure(state="disabled")

        threading.Thread(target=self.download_worker, args=(links,), daemon=True).start()

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.pause_btn.configure(text="▶ Wznów")
            self.log("[PAUZA] Wstrzymano pobieranie.")
        else:
            self.pause_btn.configure(text="⏸ Pauza")
            self.log("[WZNOWIONO] Kontynuowanie pobierania...")

    def stop_download(self):
        self.stop_requested = True
        self.is_paused = False
        self.log("[ANULOWANIE] Przerywanie operacji...")

    def download_worker(self, links):
        total_files = len(links)
        downloaded_files = []

        self.log(f"[START] Rozpoczynam pobieranie {total_files} plików do: {self.download_folder}")

        for idx, url in enumerate(links, start=1):
            if self.stop_requested:
                break

            self.overall_label.configure(text=f"Postęp kolejki: {idx - 1} / {total_files} plików")
            self.overall_progress.set((idx - 1) / total_files)

            success, filepath = self.download_single_file(url, idx, total_files)
            if success and filepath:
                downloaded_files.append(filepath)
            elif not self.stop_requested:
                self.log(f"[BŁĄD] Pominięto plik z powodu błędu: {url}")

        if not self.stop_requested:
            self.overall_progress.set(1.0)
            self.overall_label.configure(text=f"Postęp kolejki: {len(downloaded_files)} / {total_files} plików (Zakończono)")

            if self.auto_extract_var.get() and downloaded_files:
                if len(downloaded_files) == total_files:
                    self.extract_archives(downloaded_files)
                else:
                    self.log("[OSTRZEŻENIE] Nie wszystkie pliki zostały pobrane! Pomijam wypakowywanie, aby uniknąć błędów.")

        self.reset_ui()

    def download_single_file(self, original_url, index, total):
        try:
            # 1. Wyciągnięcie bezpośredniego linku i nazwy pliku z hostingu
            direct_url, hint_filename = self.resolve_hoster_url(original_url)

            headers = {
                "Referer": original_url.split('#')[0]
            }

            # 2. Ustalenie nazwy pliku
            if not hint_filename:
                with self.scraper.get(direct_url, headers=headers, stream=True, timeout=20) as r:
                    r.raise_for_status()
                    filename = self.get_filename_from_url(direct_url, r)
            else:
                filename = self.get_filename_from_url(direct_url, None, hint_filename)

            filepath = os.path.join(self.download_folder, filename)
            downloaded_bytes = 0

            # 3. Sprawdzenie czy plik częściowo istnieje (Resume)
            if os.path.exists(filepath):
                downloaded_bytes = os.path.getsize(filepath)
                headers["Range"] = f"bytes={downloaded_bytes}-"

            # 4. Właściwe pobieranie strumieniowe
            with self.scraper.get(direct_url, headers=headers, stream=True, timeout=30) as r:
                if r.status_code == 416:
                    self.log(f"[INFO] Plik {filename} jest już w pełni pobrany.")
                    return True, filepath

                r.raise_for_status()

                content_type = r.headers.get("Content-Type", "")
                if "text/html" in content_type and not filename.endswith(".html"):
                    self.log(f"[BŁĄD] Serwer zwrócił stronę HTML zamiast archiwum dla: {filename}")
                    return False, None

                is_resuming = (r.status_code == 206)
                mode = "ab" if is_resuming else "wb"
                if not is_resuming:
                    downloaded_bytes = 0

                total_size = int(r.headers.get("content-length", 0)) + downloaded_bytes

                self.current_file_label.configure(text=f"[{index}/{total}] Pobieranie: {filename}")
                self.log(f"[POBIERANIE] {filename} ({total_size / (1024*1024):.1f} MB)")

                start_time = time.time()
                session_bytes = 0

                with open(filepath, mode) as f:
                    for chunk in r.iter_content(chunk_size=1024 * 512):  # 512 KB chunk
                        if self.stop_requested:
                            self.log(f"[PRZERWANO] Zatrzymano pobieranie pliku: {filename}")
                            return False, None

                        while self.is_paused:
                            time.sleep(0.2)
                            if self.stop_requested:
                                return False, None
                            start_time = time.time()
                            session_bytes = 0

                        if chunk:
                            f.write(chunk)
                            downloaded_bytes += len(chunk)
                            session_bytes += len(chunk)

                            elapsed = time.time() - start_time
                            speed = (session_bytes / elapsed) if elapsed > 0 else 0
                            speed_mb = speed / (1024 * 1024)

                            if total_size > 0:
                                progress = downloaded_bytes / total_size
                                self.file_progress.set(progress)
                                remaining_bytes = total_size - downloaded_bytes
                                eta_seconds = int(remaining_bytes / speed) if speed > 0 else 0
                                eta_str = time.strftime("%M:%S", time.gmtime(eta_seconds))
                            else:
                                self.file_progress.set(0)
                                eta_str = "--:--"

                            dl_mb = downloaded_bytes / (1024 * 1024)
                            tot_mb = total_size / (1024 * 1024)

                            self.stats_label.configure(
                                text=f"Prędkość: {speed_mb:.2f} MB/s  |  Pobrano: {dl_mb:.1f} / {tot_mb:.1f} MB  |  ETA: {eta_str}"
                            )

            self.log(f"[SUKCES] Ukończono pobieranie: {filename}")
            return True, filepath

        except Exception as e:
            self.log(f"[BŁĄD KRYTYCZNY] {original_url} -> {str(e)}")
            return False, None

    def extract_archives(self, filepaths):
        """Wyszukuje główne archiwum i wypakowuje całość za pomocą 7-Zip."""
        if not self.seven_zip_path:
            self.log("[BŁĄD] Brak programu 7-Zip! Nie można wypakować plików.")
            return

        first_parts = []
        for path in filepaths:
            name = os.path.basename(path).lower()
            if re.search(r'\.part0*1\.rar$', name) or name.endswith('.001'):
                first_parts.append(path)
            elif name.endswith(('.rar', '.7z', '.zip')) and '.part' not in name:
                first_parts.append(path)

        if not first_parts:
            self.log("[INFO] Nie znaleziono głównego pliku archiwum (np. part1.rar) do wypakowania.")
            return

        for archive in first_parts:
            archive_name = os.path.basename(archive)
            self.current_file_label.configure(text=f"Wypakowywanie: {archive_name}...")
            self.log(f"[ROZPAKOWYWANIE] Rozpoczynam wypakowywanie {archive_name}...")

            cmd = [self.seven_zip_path, "x", archive, f"-o{self.download_folder}", "-y"]
            process = subprocess.run(cmd, capture_output=True, text=True)

            if process.returncode == 0:
                self.log(f"[SUKCES] Pomyślnie wypakowano archiwum: {archive_name}")
                if self.delete_archives_var.get():
                    self.cleanup_archives(filepaths)
            else:
                self.log(f"[BŁĄD WYPAKOWYWANIA] Kod błędu 7z ({process.returncode}): {process.stderr}")

    def cleanup_archives(self, filepaths):
        self.log("[CZYSZCZENIE] Usuwanie pobranych archiwów...")
        for path in filepaths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                self.log(f"[BŁĄD] Nie udało się usunąć {os.path.basename(path)}: {e}")
        self.log("[CZYSZCZENIE] Zakończono usuwanie archiwów.")

    def reset_ui(self):
        self.is_downloading = False
        self.start_btn.configure(state="normal")
        self.pause_btn.configure(state="disabled", text="⏸ Pauza")
        self.stop_btn.configure(state="disabled")
        self.links_textbox.configure(state="normal")
        self.current_file_label.configure(text="Gotowy do pracy.")


if __name__ == "__main__":
    app = MultiPartDownloaderApp()
    app.mainloop()