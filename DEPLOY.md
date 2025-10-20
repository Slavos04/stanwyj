Deployment — szybki przewodnik
===============================

Cel: uruchomić projekt na serwerze o adresie 192.168.1.100, tak aby działał jako usługa i był dostępny na porcie 8090.

Założenia
- masz dostęp SSH do serwera 192.168.1.100
- na serwerze jest Python 3.10+ (lub 3.8+), pip, oraz można instalować pakiety systemowe (np. ffmpeg)
- masz uprawnienia do tworzenia usług systemd lub uruchamiania procesów w tle

Kroki
1) Sklonuj repozytorium

ssh user@192.168.1.100
cd ~
git clone <repo-url> stanwyj
cd stanwyj

2) Utwórz i aktywuj wirtualne środowisko

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

3) Zainstaluj ffmpeg (na Debian/Ubuntu)

sudo apt update
sudo apt install -y ffmpeg

4) Skonfiguruj `config.json`
- Skopiuj lokalny plik `config.json` lub edytuj go na serwerze.
- Ustaw `telegram.bot_token` i `telegram.chat_id` jeśli chcesz powiadomienia.

5) Uruchom jako usługa systemd (rekomendowane)
Utwórz plik `/etc/systemd/system/stanwyj.service` z zawartością:

[Unit]
Description=Stanwyj downloader
After=network.target

[Service]
User=youruser
WorkingDirectory=/home/youruser/stanwyj
Environment="PATH=/home/youruser/stanwyj/venv/bin"
ExecStart=/home/youruser/stanwyj/venv/bin/python /home/youruser/stanwyj/server.py
Restart=always

[Install]
WantedBy=multi-user.target

Następnie:

sudo systemctl daemon-reload
sudo systemctl enable stanwyj
sudo systemctl start stanwyj
sudo journalctl -u stanwyj -f

6) (Opcjonalnie) Reverse proxy z Nginx
- Jeśli chcesz serwować aplikację pod nazwą domeny lub zabezpieczyć HTTPS, ustaw Nginx jako reverse proxy.
Przykład bloku serwera:

server {
    listen 80;
    server_name 192.168.1.100; # lub domena

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

7) Firewall
- Otwórz port 8090, jeśli będziesz udostępniać z zewnątrz w sieci:

sudo ufw allow 8090/tcp
sudo ufw enable

8) Testy
- Po uruchomieniu sprawdź endpointy lokalnie na serwerze:

curl http://127.0.0.1:8090/api/search?query=ava%20max

- Sprawdź logi serwisu: `sudo journalctl -u stanwyj -f`

Uwagi
- Jeśli chcesz korzystać z HTTPS publicznie, skonfiguruj Nginx i certbot (Let's Encrypt).
- Dla większej niezawodności możesz uruchamiać aplikację za pomocą gunicorn lub uvicorn za reverse-proxy Nginx, ale dla prostoty uruchomienie bezpośrednie może być wystarczające.

Jeśli chcesz, mogę:
- wygenerować przykładowy plik systemd z twoimi ścieżkami,
- dodać plik `nginx` configuration snippet,
- zrobić dodatkowe zabezpieczenia (uwierzytelnianie, ograniczenie dostępu po IP).
