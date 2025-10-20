# YouTube to MP3 downloader
# stanwyj — YouTube → MP3 downloader (server + scheduler)

Projekt pozwala wyszukiwać i pobierać audio z YouTube w formacie MP3, harmonogramować pobrania i wysyłać
powiadomienia przez Telegram. Pliki można zapisać lokalnie (konfigurowalny katalog) oraz wysłać do Nextcloud
przez WebDAV.

Najważniejsze pliki
- `server.py` — Flask API + scheduler (APScheduler).
- `download_mp3.py` — logika wyszukiwania i pobierania (yt-dlp + ffmpeg).
- `static/index.html` — prosty frontend do obsługi wyszukiwania, harmonogramów i logów.
- `config.json` — konfiguracja (Telegram, Nextcloud, katalog lokalny).

Wymagania
- Python 3.8+ (zalecane 3.10+)
- ffmpeg (w systemie)

Instalacja i uruchomienie (Ubuntu)

1. Utwórz katalog docelowy, przykładowo `/programy/stanwyj` i sklonuj tam repozytorium.
2. Stwórz wirtualne środowisko i zainstaluj zależności:

```bash
cd /programy/stanwyj
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. Utwórz lub edytuj `config.json`. Przykład minimalnej konfiguracji:

```json
{
	"telegram": { "bot_token": "<token>", "chat_id": "<chat_id>" },
	"nextcloud": { "webdav_url": "https://nextcloud.example/remote.php/dav/files/username", "user": "username", "password": "app-password", "folder": "stanwyj", "enabled": false },
	"local": { "download_dir": "/programy/stanwyj/downloads" }
}
```

4. Uruchom serwer:

```bash
source venv/bin/activate
python3 server.py
```

Serwer nasłuchuje domyślnie na `0.0.0.0:8090`.

Systemd unit (przykład)

Zapisz poniższy plik jako `/etc/systemd/system/stanwyj.service` i dopasuj `User` oraz ścieżki:

```
[Unit]
Description=StanWyj MP3 downloader
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/programy/stanwyj
Environment="PATH=/programy/stanwyj/venv/bin"
ExecStart=/programy/stanwyj/venv/bin/python /programy/stanwyj/server.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Po dodaniu pliku:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stanwyj.service
sudo journalctl -u stanwyj -f
```

Interfejs webowy
- Strona główna: `/` — wyszukiwanie, harmonogramy, lista zadań.
- Panel `Pobrane pliki lokalnie` pozwala przeglądać i usuwać pobrane pliki.

Uwagi bezpieczeństwa
- Nie zapisuj poufnych haseł w repo. Przechowuj `config.json` poza systemem kontroli wersji lub stosuj zmienne środowiskowe.

Jeśli chcesz, przygotuję dodatkowo skrypt instalacyjny (bash) który utworzy katalogi, venv i plik systemd.

---# stanwyj
