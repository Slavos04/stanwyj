#!/usr/bin/env python3
"""
Prosty, stabilny downloader MP3 z YouTube.

Funkcje:
- wyszukiwanie 5 propozycji (najnowsze pierwsze)
- interaktywny wybór z timeoutem 30s (domyślnie 1)
- pobranie audio i konwersja do MP3 @96k z osadzeniem miniatury i metadanych
- automatyczna instalacja brakującego pakietu yt-dlp (pip)
- sprawdzenie obecności ffmpeg

Konfiguracja opcjonalna przez `config.json` w katalogu skryptu.
"""

from __future__ import annotations

import sys
import os
import shutil
import subprocess
import signal
import time
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

ROOT = os.path.dirname(__file__)
REQ_FILE = os.path.join(ROOT, 'requirements.txt')
CONFIG_FILE = os.path.join(ROOT, 'config.json')


def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def input_with_timeout(prompt: str, timeout: int, default: str = '1') -> str:
    """Read input with a SIGALRM timeout (works on Unix)."""
    if timeout is None or timeout <= 0:
        return input(prompt)

    def _handler(signum, frame):
        raise TimeoutError()

    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(timeout)
    try:
        val = input(prompt)
        signal.alarm(0)
        return val.strip() or default
    except TimeoutError:
        print(f"\nTimeout ({timeout}s) — wybieram domyślnie: {default}")
        return default
    finally:
        signal.signal(signal.SIGALRM, old)


def ensure_python_deps() -> bool:
    try:
        import yt_dlp  # type: ignore
        return True
    except Exception:
        print('Brak yt-dlp; próbuję zainstalować przez pip...')
        if os.path.exists(REQ_FILE):
            cmd = [sys.executable, '-m', 'pip', 'install', '-r', REQ_FILE]
        else:
            cmd = [sys.executable, '-m', 'pip', 'install', 'yt-dlp']
        try:
            subprocess.check_call(cmd)
            return True
        except Exception:
            print('Nie udało się zainstalować pakietów. Zainstaluj ręcznie (pip install yt-dlp).')
            return False


def ensure_ffmpeg() -> bool:
    if shutil.which('ffmpeg'):
        return True
    print('ffmpeg nie został znaleziony w PATH. Zainstaluj go np. `sudo apt install ffmpeg`.')
    return False


def secs_to_hhmmss_dots(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}.{m:02d}.{s:02d}"


def format_upload_date(udate: Optional[str]) -> str:
    if not udate:
        return ''
    try:
        return datetime.strptime(udate, '%Y%m%d').strftime('%d.%m.%Y')
    except Exception:
        return udate


def search_youtube(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Search YouTube using yt-dlp and return detailed entries.

    Returns list of dicts (possibly partial) sorted newest-first.
    """
    try:
        import yt_dlp  # type: ignore
    except Exception:
        print('Brak biblioteki yt_dlp - zainstaluj dependencies.')
        return []

    cfg = load_config()

    class _SilentLogger:
        def debug(self, msg):
            pass

        def info(self, msg):
            pass

        def warning(self, msg):
            pass

        def error(self, msg):
            pass

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': True,
        'allow_unplayable_formats': True,
        'retries': cfg.get('yt_dlp', {}).get('retries', 3),
        'logger': _SilentLogger(),
    }
    ua = cfg.get('yt_dlp', {}).get('user_agent')
    if ua:
        ydl_opts['http_headers'] = {'User-Agent': ua}
    cookies = cfg.get('yt_dlp', {}).get('cookies_file')
    if cookies:
        ydl_opts['cookiefile'] = cookies

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            entries = data.get('entries') or []
    except Exception as e:
        print('Błąd podczas wyszukiwania:', e)
        return []

    results: List[Dict[str, Any]] = []
    # fetch full info for each entry (best-effort)
    with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'allow_unplayable_formats': True, 'logger': _SilentLogger()}) as ydl:
        for e in entries:
            try:
                vid = e.get('url') or e.get('id')
                item = ydl.extract_info(vid, download=False)
            except Exception:
                item = e
            results.append(item)

    # sort by upload_date if present (newest first)
    results.sort(key=lambda x: x.get('upload_date') or '', reverse=True)
    return results[:limit]


def download_audio(video_url: str, outtmpl: str = '%(title)s.%(ext)s', output_dir: Optional[str] = None) -> Tuple[bool, str]:
    try:
        import yt_dlp  # type: ignore
    except Exception:
        return False, 'yt_dlp_missing'

    cfg = load_config()
    base_opts: Dict[str, Any] = {
        'format': cfg.get('yt_dlp', {}).get('format', 'bestaudio[ext=m4a]/bestaudio/best'),
        'outtmpl': outtmpl,
        'noplaylist': True,
        'writethumbnail': True,
        'prefer_ffmpeg': True,
        'allow_unplayable_formats': True,
        'retries': cfg.get('yt_dlp', {}).get('retries', 3),
        'postprocessors': [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '96'},
            {'key': 'EmbedThumbnail'},
            {'key': 'FFmpegMetadata'},
        ],
    }

    ua = cfg.get('yt_dlp', {}).get('user_agent')
    if ua:
        base_opts['http_headers'] = {'User-Agent': ua}
    cookies = cfg.get('yt_dlp', {}).get('cookies_file')
    if cookies:
        base_opts['cookiefile'] = cookies

    attempts = cfg.get('yt_dlp', {}).get('attempts', 3) or 3
    for attempt in range(1, attempts + 1):
        opts = dict(base_opts)
        if attempt > 1:
            opts['format'] = 'bestaudio/best'
        try:
            # if output_dir provided, ensure it exists and set outtmpl to write there
            search_dir = os.getcwd()
            if output_dir:
                try:
                    os.makedirs(output_dir, exist_ok=True)
                except Exception:
                    pass
                # set outtmpl to include output_dir so files are written there
                opts['outtmpl'] = os.path.join(output_dir, outtmpl)
                search_dir = output_dir

            with yt_dlp.YoutubeDL(opts) as ydl:
                # ydl.download returns None; download will write file to outtmpl
                ydl.download([video_url])
            # try to find the most recently created mp3 file in search_dir
            mp3s = [os.path.join(search_dir, f) for f in os.listdir(search_dir) if f.lower().endswith('.mp3')]
            if mp3s:
                mp3s.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                return True, mp3s[0]
            return True, ''
        except Exception as e:
            err = str(e)
            print(f'Próba {attempt} nie powiodła się: {err}')
            if 'HTTP Error 403' in err or '403' in err:
                if not cookies and not ua:
                    print('Błąd 403 — rozważ ustawienie cookies lub user agent w config.json.')
            if attempt < attempts:
                time.sleep(2 * attempt)
                continue
            return False, err


def main() -> None:
    print('Prosty downloader mp3 z YouTube (yt-dlp + ffmpeg).')

    ok = ensure_python_deps()
    if not ok:
        print('Brakuje zależności Python. Zainstaluj yt-dlp i spróbuj ponownie.')
        return
    ensure_ffmpeg()

    try:
        query = input('Wpisz frazę do wyszukania na YouTube: ').strip()
    except EOFError:
        print('\nBrak wejścia. Kończę.')
        return

    if not query:
        print('Nie podano frazy.')
        return

    print('\nWyszukiwanie... (pobieram 5 najnowszych wyników)')
    results = search_youtube(query, limit=5)
    if not results:
        print('Brak wyników.')
        return

    print('\nZnaleziono (najnowsze pierwsze):')
    for i, r in enumerate(results, start=1):
        title = r.get('title') or r.get('fulltitle') or 'No title'
        duration = int(r.get('duration') or 0)
        udate = r.get('upload_date') or ''
        url = r.get('webpage_url') or r.get('url') or r.get('id')
        print(f"{i}. {title}\n   Czas: {secs_to_hhmmss_dots(duration)}  Data publikacji: {format_upload_date(udate)}\n   URL: {url}\n")

    print('\nWybierz numer (1-5). Wpisz q aby anulować. Program będzie czekał na Twój wybór.')
    idx = None
    while True:
        choice = input('Numer [1]: ').strip()
        if choice.lower() in ('q', 'quit', 'exit'):
            print('Anulowano przez użytkownika.')
            return
        if not choice:
            # default to 1 only if user explicitly presses Enter
            choice = '1'
        try:
            idx = int(choice)
            if 1 <= idx <= len(results):
                break
            else:
                print(f'Nieprawidłowy numer, podaj wartość od 1 do {len(results)} lub q aby anulować.')
        except ValueError:
            print('Nieprawidłowy wybór, wpisz numer lub q aby anulować.')

    sel = results[idx - 1]
    video_url = sel.get('webpage_url') or sel.get('url') or sel.get('id')
    print(f"\nPobieram: {sel.get('title')} (URL: {video_url})\n")

    ok, path_or_err = download_audio(video_url)
    if ok:
        if path_or_err:
            print('Pobieranie zakończone. Plik mp3:', path_or_err)
        else:
            print('Pobieranie zakończone. Plik mp3 znajduje się w katalogu roboczym.')
    else:
        print('Pobieranie nie powiodło się. Błąd:', path_or_err)


if __name__ == '__main__':
    main()
