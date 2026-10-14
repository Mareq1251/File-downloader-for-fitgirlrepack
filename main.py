import os
import re
import time
import queue
import shutil
import threading
import subprocess
from urllib.parse import urlparse, unquote

import requests
import customtkinter as ctk
from tkinter import filedialog, messagebox
from playwright.sync_api import sync_playwright

# Konfiguracja wyglądu CustomTkinter
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

MAX_RETRIES = 3
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
DL_TOKEN_RE = re.compile(r'fuckingfast\.co(?:\\?/)dl(?:\\?/)([A-Za-z0-9_-]{8,})')


def format_eta(seconds):
    seconds = int(max(seconds, 0))
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


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
        self.worker_thread = None
        self.opt_extract = True
        self.opt_delete = False
        self.opt_headless = True
        self.browser_headless = False
        self.token_failure = False
        self.token_wait = 75
        self.download_folder = os.path.join(os.getcwd(), "downloads")
        os.makedirs(self.download_folder, exist_ok=True)

        # Kolejka zdarzeń UI – wątek roboczy NIE może dotykać widgetów bezpośrednio
        self.ui_queue = queue.Queue()

        self.seven_zip_path = self.find_7zip()

        self.setup_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(50, self.process_ui_queue)

    # ------------------------------------------------------------------ #
    #  Narzędzia pomocnicze
    # ------------------------------------------------------------------ #
    def find_7zip(self):
        """Szuka pliku wykonywalnego 7-Zip w standardowych lokalizacjach i w PATH."""
        for path in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
            if os.path.isfile(path):
                return path
        for name in ("7z", "7za", "7zz"):
            found = shutil.which(name)
            if found:
                return found
        return None

    @staticmethod
    def sanitize_filename(name):
        name = INVALID_FILENAME_CHARS.sub("_", name).strip().strip(".")
        return name or None

    def ui(self, func, *args, **kwargs):
        """Zleca wykonanie funkcji w głównym wątku (bezpieczne z wątków roboczych)."""
        self.ui_queue.put((func, args, kwargs))

    def process_ui_queue(self):
        try:
            for _ in range(200):  # limit na jeden "tik", żeby nie zamrozić okna
                func, args, kwargs = self.ui_queue.get_nowait()
                try:
                    func(*args, **kwargs)
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.after(50, self.process_ui_queue)

    def log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.ui(self._append_log, f"[{timestamp}] {message}\n")

    def _append_log(self, text):
        self.log_textbox.configure(state="normal")
        self.log_textbox.insert("end", text)
        self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")

    def set_overall(self, done, total, suffix=""):
        self.ui(self.overall_label.configure, text=f"Postęp kolejki: {done} / {total} plików{suffix}")
        self.ui(self.overall_progress.set, (done / total) if total else 0)

    # ------------------------------------------------------------------ #
    #  Interfejs
    # ------------------------------------------------------------------ #
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

        ctk.CTkLabel(links_frame, text="Wklej linki (jeden w linii, np. https://fuckingfast.co/...#nazwa.rar):", font=("Arial", 13, "bold")).grid(row=0, column=0, padx=10, pady=(8, 2), sticky="w")
        self.links_textbox = ctk.CTkTextbox(links_frame, height=130)
        self.links_textbox.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")

        # 3. Opcje dodatkowe
        options_frame = ctk.CTkFrame(self)
        options_frame.grid(row=2, column=0, padx=15, pady=5, sticky="ew")

        self.auto_extract_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(options_frame, text="Wypakuj automatycznie (7-Zip)", variable=self.auto_extract_var).pack(side="left", padx=15, pady=10)

        self.delete_archives_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(options_frame, text="Usuń archiwa po wypakowaniu", variable=self.delete_archives_var).pack(side="left", padx=15, pady=10)

        self.headless_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(options_frame, text="Przeglądarka w tle", variable=self.headless_var).pack(side="left", padx=15, pady=10)

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

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.download_folder)
        if folder:
            self.download_folder = folder
            self.folder_entry.delete(0, "end")
            self.folder_entry.insert(0, folder)

    def on_close(self):
        if self.is_downloading:
            if not messagebox.askyesno("Zamknij", "Trwa pobieranie. Czy na pewno chcesz zamknąć program?"):
                return
            self.stop_requested = True
            self.is_paused = False
            if self.worker_thread and self.worker_thread.is_alive():
                self.worker_thread.join(timeout=5)  # daj czas na zamknięcie przeglądarki
        self.destroy()

    # ------------------------------------------------------------------ #
    #  Pobieranie linku /dl/ przez Playwright
    # ------------------------------------------------------------------ #
    @staticmethod
    def needs_token(url):
        return "fuckingfast.co" in url and "/dl/" not in url

    def try_click_download(self, page):
        """Klika przycisk DOWNLOAD na stronie (pierwsze kliknięcie otwiera reklamy, drugie startuje pobieranie)."""
        candidates = [
            page.get_by_role("button", name=re.compile(r"^\s*download\s*$", re.I)),
            page.get_by_role("link", name=re.compile(r"^\s*download\s*$", re.I)),
            page.get_by_text(re.compile(r"^\s*download\s*$", re.I)),
            page.get_by_role("button", name=re.compile("download", re.I)),
            page.get_by_role("link", name=re.compile("download", re.I)),
        ]
        for loc in candidates:
            try:
                if loc.count() > 0:
                    loc.first.click(timeout=3000)
                    self.log("[AUTOMAT] Kliknięto przycisk DOWNLOAD.")
                    return True
            except Exception:
                continue
        return False

    def close_extra_pages(self, page):
        """Zamyka karty z reklamami otwarte przez stronę i wraca do głównej karty."""
        closed = False
        for pg in list(page.context.pages):
            if pg is not page:
                try:
                    pg.close()
                    closed = True
                except Exception:
                    pass
        if closed:
            try:
                page.bring_to_front()
            except Exception:
                pass

    def dump_debug_page(self, page):
        try:
            path = os.path.join(os.getcwd(), "debug_page.html")
            html = page.content()
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"<!-- URL: {page.url} -->\n{html}")
            self.log(f"[DEBUG] Tytuł strony: {page.title()!r} | długość HTML: {len(html)}")
            self.log(f"[DEBUG] Zapisano źródło strony do: {path}")
        except Exception as e:
            self.log(f"[DEBUG] Nie udało się zapisać źródła strony: {e}")

    def extract_dl_link_from_page(self, page, clean_url):
        """
        Otwiera stronę w Playwright i zdobywa prawdziwy link /dl/<token>:
          1) z kodu źródłowego strony (jeśli jest tam od razu),
          2) przez przechwycenie żądania /dl/ wywołanego kliknięciem DOWNLOAD
             (skrypt klika sam, ale możesz też kliknąć ręcznie w oknie przeglądarki),
          3) przez zdarzenie 'download' przeglądarki.
        Żądanie jest przerywane, żeby przeglądarka nie zużyła tokenu / nie pobierała pliku drugi raz.
        """
        self.log(f"[AUTOMAT] Pobieram token autoryzacyjny z: {clean_url}")
        context = page.context
        captured = []
        downloads = []
        route_pattern = re.compile(r"https?://[^/]*fuckingfast\.co/dl/[A-Za-z0-9_-]{8,}")

        def handle_route(route):
            url = route.request.url
            if url not in captured:
                captured.append(url)
            try:
                route.abort()
            except Exception:
                pass

        def handle_download(download):
            downloads.append(download)
            if download.url not in captured:
                captured.append(download.url)

        def watch_new_page(pg):
            pg.on("download", handle_download)

        context.route(route_pattern, handle_route)
        page.on("download", handle_download)
        context.on("page", watch_new_page)

        direct_url = None
        try:
            try:
                page.goto(clean_url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                self.log(f"[OSTRZEŻENIE] Problem z załadowaniem strony ({e}) – próbuję dalej...")

            click_times = {4, 9, 14, 20, 27, 35, 45}
            warned = False

            for i in range(self.token_wait):
                if self.stop_requested:
                    return None, None, None

                if captured:
                    direct_url = captured[0]
                    break

                try:
                    html = page.content()
                    match = DL_TOKEN_RE.search(html)
                    if match:
                        direct_url = f"https://fuckingfast.co/dl/{match.group(1)}"
                        break
                except Exception:
                    pass  # strona akurat się przeładowuje

                if i in click_times:
                    self.close_extra_pages(page)
                    self.try_click_download(page)
                elif i > 4:
                    self.close_extra_pages(page)

                if i == 12 and not warned:
                    warned = True
                    try:
                        title = page.title()
                    except Exception:
                        title = "?"
                    extra = "" if self.browser_headless else " Jeśli widzisz Cloudflare lub przycisk DOWNLOAD – kliknij go ręcznie w oknie przeglądarki."
                    self.log(f"[INFO] Wciąż czekam na link (tytuł strony: {title!r}).{extra}")

                # wait_for_timeout (a nie time.sleep) – dzięki temu Playwright obsługuje zdarzenia/żądania
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    time.sleep(1)

            if not direct_url:
                self.dump_debug_page(page)
                return None, None, None

            self.close_extra_pages(page)
            user_agent = page.evaluate("navigator.userAgent")
            cookies = {c["name"]: c["value"] for c in context.cookies()}
            return direct_url, cookies, user_agent

        finally:
            for d in downloads:  # anuluj pobieranie rozpoczęte przez przeglądarkę
                try:
                    d.cancel()
                except Exception:
                    pass
            try:
                context.unroute(route_pattern, handle_route)
            except Exception:
                pass
            try:
                page.remove_listener("download", handle_download)
                context.remove_listener("page", watch_new_page)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Nazwy plików
    # ------------------------------------------------------------------ #
    def filename_from_response(self, url, response):
        """Zwraca nazwę pliku z nagłówków/URL-a albo None (bez nazw zależnych od czasu)."""
        cd = response.headers.get("Content-Disposition")
        if cd:
            found = re.findall(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';]+)["\']?', cd, flags=re.IGNORECASE)
            if found:
                name = self.sanitize_filename(unquote(found[0]))
                if name:
                    return name

        name = unquote(os.path.basename(urlparse(url).path))
        if name and "." in name:
            return self.sanitize_filename(name)
        return None

    def probe_filename(self, url, headers, cookies):
        """Ustala nazwę pliku zanim sprawdzimy, czy da się wznowić pobieranie."""
        try:
            r = requests.head(url, headers=headers, cookies=cookies, allow_redirects=True, timeout=20)
            return self.filename_from_response(r.url, r)
        except requests.RequestException:
            return None

    # ------------------------------------------------------------------ #
    #  Sterowanie
    # ------------------------------------------------------------------ #
    def start_download(self):
        raw_links = self.links_textbox.get("1.0", "end").strip().splitlines()
        links = [link.strip() for link in raw_links if link.strip().lower().startswith("http")]
        links = list(dict.fromkeys(links))  # usuń duplikaty, zachowaj kolejność

        if not links:
            messagebox.showwarning("Brak linków", "Wklej przynajmniej jeden prawidłowy link zaczynający się od http/https!")
            return

        folder = self.folder_entry.get().strip()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Błąd folderu", f"Nie można utworzyć folderu docelowego:\n{e}")
            return
        self.download_folder = folder

        # Odczyt zmiennych Tk TYLKO w głównym wątku
        self.opt_extract = self.auto_extract_var.get()
        self.opt_delete = self.delete_archives_var.get()
        self.opt_headless = self.headless_var.get()

        self.is_downloading = True
        self.is_paused = False
        self.stop_requested = False

        self.start_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="⏸ Pauza")
        self.stop_btn.configure(state="normal")
        self.links_textbox.configure(state="disabled")
        self.file_progress.set(0)
        self.overall_progress.set(0)

        self.worker_thread = threading.Thread(target=self.download_worker, args=(links,), daemon=True)
        self.worker_thread.start()

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

    # ------------------------------------------------------------------ #
    #  Wątek roboczy
    # ------------------------------------------------------------------ #
    def launch_browser(self, p, headless):
        args = ["--disable-blink-features=AutomationControlled", "--window-size=1000,700"]
        if not headless:
            return p.chromium.launch(headless=False, args=args)
        try:
            # nowy tryb headless (pełny Chromium) – trudniejszy do wykrycia niż stary headless shell
            return p.chromium.launch(headless=True, channel="chromium", args=args)
        except Exception:
            return p.chromium.launch(headless=True, args=args)

    def new_context(self, browser, headless):
        if headless:
            ua = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  f"(KHTML, like Gecko) Chrome/{browser.version} Safari/537.36")
            return browser.new_context(user_agent=ua, viewport={"width": 1280, "height": 800})
        return browser.new_context()

    def download_worker(self, links):
        total_files = len(links)
        downloaded_files = []

        try:
            self.log(f"[START] Rozpoczynam przetwarzanie {total_files} linków do: {self.download_folder}")

            if any(self.needs_token(l.split("#")[0]) for l in links):
                headless = self.opt_headless
                start_index = 0
                while True:
                    self.browser_headless = headless
                    self.token_wait = 25 if headless else 75
                    if headless:
                        self.log("[INFO] Uruchamiam przeglądarkę w tle (bez okna).")
                    with sync_playwright() as p:
                        browser = self.launch_browser(p, headless)
                        try:
                            context = self.new_context(browser, headless)
                            page = context.new_page()
                            restart_from = self.process_links(page, links, downloaded_files, start_index, headless)
                        finally:
                            browser.close()

                    if restart_from is not None and headless and not self.stop_requested:
                        self.log("[INFO] Tryb w tle nie przeszedł zabezpieczeń strony – przełączam na widoczną przeglądarkę.")
                        headless = False
                        start_index = restart_from
                        continue
                    break
            else:
                self.process_links(None, links, downloaded_files, 0, False)

            if not self.stop_requested:
                failed = total_files - len(downloaded_files)
                suffix = " (Zakończono)" if failed == 0 else f" (Zakończono, błędy: {failed})"
                self.set_overall(len(downloaded_files), total_files, suffix)

                if self.opt_extract and downloaded_files:
                    if failed == 0:
                        self.extract_archives(downloaded_files)
                    else:
                        self.log("[OSTRZEŻENIE] Nie wszystkie pliki zostały pobrane! Pomijam wypakowywanie.")

        except Exception as e:
            msg = str(e)
            self.log(f"[BŁĄD KRYTYCZNY] {msg}")
            if "Executable doesn't exist" in msg or "playwright install" in msg:
                self.log("[PODPOWIEDŹ] Uruchom w terminalu: playwright install chromium")
        finally:
            self.ui(self.reset_ui)

    def process_links(self, page, links, downloaded_files, start_index=0, headless=False):
        """Zwraca indeks linku, od którego trzeba wznowić w trybie widocznym, albo None."""
        total_files = len(links)
        for idx in range(start_index + 1, total_files + 1):
            url = links[idx - 1]
            if self.stop_requested:
                break

            self.set_overall(idx - 1, total_files)

            success, filepath = self.download_single_file(page, url, idx, total_files)
            if success and filepath:
                downloaded_files.append(filepath)
                self.set_overall(len(downloaded_files), total_files)
            elif not self.stop_requested:
                if headless and self.token_failure:
                    return idx - 1
                self.log(f"[BŁĄD] Pominięto plik z powodu błędu: {url}")
        return None

    def download_single_file(self, page, original_url, index, total):
        parsed = urlparse(original_url)
        hint_filename = self.sanitize_filename(unquote(parsed.fragment)) if parsed.fragment else None
        clean_url = original_url.split("#")[0]
        needs_token = self.needs_token(clean_url)
        self.token_failure = False

        for attempt in range(1, MAX_RETRIES + 1):
            if self.stop_requested:
                return False, None

            try:
                if needs_token:
                    direct_url, cookies, user_agent = self.extract_dl_link_from_page(page, clean_url)
                    if not direct_url:
                        if self.stop_requested:
                            return False, None
                        self.log(f"[BŁĄD] Próba {attempt}/{MAX_RETRIES}: nie znaleziono linku /dl/ na stronie: {clean_url}")
                        self.token_failure = True
                        if self.browser_headless:
                            return False, None  # w trybie w tle nie ponawiamy – przełączymy na widoczną przeglądarkę
                        time.sleep(2)
                        continue
                else:
                    direct_url, cookies, user_agent = clean_url, {}, DEFAULT_UA

                return self.stream_to_file(direct_url, clean_url, cookies, user_agent, hint_filename, index, total)

            except (requests.RequestException, OSError) as e:
                self.log(f"[BŁĄD SIECI] Próba {attempt}/{MAX_RETRIES}: {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(3)
            except Exception as e:
                self.log(f"[BŁĄD KRYTYCZNY] {original_url} -> {e}")
                return False, None

        return False, None

    def stream_to_file(self, direct_url, referer, cookies, user_agent, hint_filename, index, total):
        headers = {"User-Agent": user_agent, "Referer": referer}

        # Nazwa pliku musi być znana PRZED sprawdzeniem częściowego pliku (wznawianie)
        filename = hint_filename or self.probe_filename(direct_url, headers, cookies) or f"part_{index}.rar"
        filepath = os.path.join(self.download_folder, filename)

        downloaded_bytes = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        req_headers = dict(headers)
        if downloaded_bytes > 0:
            req_headers["Range"] = f"bytes={downloaded_bytes}-"

        with requests.get(direct_url, headers=req_headers, cookies=cookies, stream=True, timeout=(15, 60)) as r:
            if r.status_code == 416:
                self.log(f"[INFO] Plik {filename} jest już w pełni pobrany.")
                return True, filepath

            r.raise_for_status()

            if "text/html" in r.headers.get("Content-Type", ""):
                self.log(f"[BŁĄD] Serwer zwrócił stronę HTML zamiast pliku dla: {filename}")
                return False, None

            is_resuming = (r.status_code == 206)
            mode = "ab" if is_resuming else "wb"
            if not is_resuming:
                downloaded_bytes = 0  # serwer zignorował Range – zaczynamy od zera

            # Całkowity rozmiar: z Content-Range (przy 206) lub Content-Length
            total_size = 0
            content_range = r.headers.get("Content-Range", "")
            if is_resuming and "/" in content_range:
                tail = content_range.rsplit("/", 1)[1].strip()
                if tail.isdigit():
                    total_size = int(tail)
            if not total_size:
                cl = r.headers.get("Content-Length", "")
                if cl.isdigit():
                    total_size = int(cl) + downloaded_bytes

            self.ui(self.current_file_label.configure, text=f"[{index}/{total}] Pobieranie: {filename}")
            self.log(f"[POBIERANIE] {filename} ({total_size / (1024 * 1024):.1f} MB)" + (" – wznawianie" if is_resuming else ""))

            start_time = time.time()
            session_bytes = 0
            last_ui_update = 0.0

            with open(filepath, mode) as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if self.stop_requested:
                        return False, None

                    if self.is_paused:
                        while self.is_paused and not self.stop_requested:
                            time.sleep(0.2)
                        if self.stop_requested:
                            return False, None
                        start_time = time.time()
                        session_bytes = 0

                    if not chunk:
                        continue

                    f.write(chunk)
                    downloaded_bytes += len(chunk)
                    session_bytes += len(chunk)

                    now = time.time()
                    if now - last_ui_update < 0.25:
                        continue  # ogranicz odświeżanie UI
                    last_ui_update = now

                    elapsed = max(now - start_time, 1e-6)
                    speed = session_bytes / elapsed

                    if total_size > 0:
                        self.ui(self.file_progress.set, min(downloaded_bytes / total_size, 1.0))
                        eta_str = format_eta((total_size - downloaded_bytes) / speed) if speed > 0 else "--:--"
                    else:
                        self.ui(self.file_progress.set, 0)
                        eta_str = "--:--"

                    self.ui(
                        self.stats_label.configure,
                        text=(f"Prędkość: {speed / (1024 * 1024):.2f} MB/s  |  "
                              f"Pobrano: {downloaded_bytes / (1024 * 1024):.1f} / {total_size / (1024 * 1024):.1f} MB  |  "
                              f"ETA: {eta_str}"),
                    )

        # Połączenie mogło zostać zerwane po cichu – wtedy ponawiamy (z wznowieniem)
        if total_size and downloaded_bytes < total_size:
            raise IOError(f"Pobrano tylko {downloaded_bytes} z {total_size} bajtów – połączenie przerwane")

        self.ui(self.file_progress.set, 1.0)
        self.log(f"[SUKCES] Ukończono pobieranie: {filename}")
        return True, filepath

    # ------------------------------------------------------------------ #
    #  Wypakowywanie
    # ------------------------------------------------------------------ #
    def extract_archives(self, filepaths):
        if not self.seven_zip_path:
            self.log("[BŁĄD] Brak programu 7-Zip!")
            return

        first_parts = []
        for path in filepaths:
            name = os.path.basename(path).lower()
            if re.search(r"\.part0*1\.rar$", name) or name.endswith(".001"):
                first_parts.append(path)
            elif name.endswith((".rar", ".7z", ".zip")) and ".part" not in name:
                first_parts.append(path)

        if not first_parts:
            self.log("[INFO] Nie znaleziono głównego pliku archiwum do wypakowania.")
            return

        popen_kwargs = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        all_ok = True
        for archive in first_parts:
            if self.stop_requested:
                return

            archive_name = os.path.basename(archive)
            self.ui(self.current_file_label.configure, text=f"Wypakowywanie: {archive_name}...")
            self.log(f"[ROZPAKOWYWANIE] Rozpoczynam wypakowywanie {archive_name}...")

            cmd = [self.seven_zip_path, "x", archive, f"-o{self.download_folder}", "-y"]
            process = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **popen_kwargs)

            if process.returncode == 0:
                self.log(f"[SUKCES] Pomyślnie wypakowano archiwum: {archive_name}")
            else:
                all_ok = False
                details = (process.stderr or process.stdout or "").strip()[-600:]
                self.log(f"[BŁĄD WYPAKOWYWANIA] Kod błędu 7z ({process.returncode}): {details}")

        # Sprzątanie dopiero PO wypakowaniu wszystkich archiwów i tylko gdy wszystko się udało
        if self.opt_delete:
            if all_ok:
                self.cleanup_archives(filepaths)
            else:
                self.log("[INFO] Pomijam usuwanie archiwów, bo wypakowywanie zakończyło się błędem.")

    def cleanup_archives(self, filepaths):
        self.log("[CZYSZCZENIE] Usuwanie pobranych archiwów...")
        for path in filepaths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                self.log(f"[BŁĄD] Nie udało się usunąć {os.path.basename(path)}: {e}")
        self.log("[CZYSZCZENIE] Zakończono.")

    def reset_ui(self):
        self.is_downloading = False
        self.is_paused = False
        self.start_btn.configure(state="normal")
        self.pause_btn.configure(state="disabled", text="⏸ Pauza")
        self.stop_btn.configure(state="disabled")
        self.links_textbox.configure(state="normal")
        self.current_file_label.configure(text="Gotowy do pracy.")


if __name__ == "__main__":
    app = MultiPartDownloaderApp()
    app.mainloop()