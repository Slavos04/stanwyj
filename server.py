#!/usr/bin/env python3
"""
Prosty serwer Flask obsługujący wyszukiwanie, pobieranie i harmonogramowanie cotygodniowych pobrań.
Używa funkcji wyszukiwania/pobierania z `download_mp3.py`.
"""
import os
import json
import threading
import shutil
from datetime import datetime, timezone
from uuid import uuid4

from flask import Flask, request, jsonify, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

APP_DIR = os.path.dirname(__file__)
SCHEDULES_FILE = os.path.join(APP_DIR, 'schedules.json')
JOBS_FILE = os.path.join(APP_DIR, 'jobs.json')
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')

# import helper functions from download_mp3.py
from download_mp3 import search_youtube, download_audio, secs_to_hhmmss_dots, format_upload_date

app = Flask(__name__, static_folder='static', static_url_path='')
tz = pytz.timezone('Europe/Warsaw')

scheduler = BackgroundScheduler(timezone=tz)


def load_json_or_empty(path, default):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def append_job_record(record):
    jobs = load_json_or_empty(JOBS_FILE, [])
    jobs.append(record)
    save_json(JOBS_FILE, jobs)


def send_telegram_notification(text):
    cfg = load_json_or_empty(CONFIG_FILE, {})
    tg = cfg.get('telegram') or {}
    token = tg.get('bot_token')
    chat_id = tg.get('chat_id')
    if not token or not chat_id:
        return False
    try:
        import requests
        url = f'https://api.telegram.org/bot{token}/sendMessage'
        resp = requests.post(url, json={'chat_id': chat_id, 'text': text})
        return resp.status_code == 200
    except Exception as e:
        print('Błąd wysyłania Telegrama:', e)
        return False


def perform_download_task(query=None, url=None, scheduled_id=None):
    start = datetime.now(tz).isoformat()
    record = {
        'id': str(uuid4()),
        'start': start,
        'query': query,
        'url': url,
        'scheduled_id': scheduled_id,
        'status': 'started',
    }
    append_job_record(record)

    # If we have a query but no url, search and pick newest
    if query and not url:
        results = search_youtube(query, limit=1)
        if not results:
            record.update({'status': 'failed', 'message': 'no results'})
            append_job_record(record)
            return
        url = results[0].get('webpage_url') or results[0].get('url') or results[0].get('id')

    # try to extract metadata (duration, estimated filesize) using yt-dlp
    try:
        import yt_dlp
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            info = None
    except Exception:
        info = None

    # populate metadata into record (duration in seconds and hh:mm:ss, size in bytes/MB)
    duration_seconds = None
    size_bytes = None
    video_page = None
    if info:
        try:
            duration_seconds = int(info.get('duration') or 0)
        except Exception:
            duration_seconds = None
        formats = info.get('formats') or []
        for f in reversed(formats):
            if f.get('filesize'):
                size_bytes = f.get('filesize')
                break
            if f.get('filesize_approx'):
                size_bytes = f.get('filesize_approx')
                break
        video_page = info.get('webpage_url') or info.get('url') or url

    def secs_to_hhmmss(secs):
        try:
            secs = int(secs or 0)
        except Exception:
            secs = 0
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    if duration_seconds is not None:
        record['duration_seconds'] = duration_seconds
        record['duration_hms'] = secs_to_hhmmss(duration_seconds)
    if size_bytes:
        try:
            record['size_bytes'] = int(size_bytes)
            record['size_mb'] = round(int(size_bytes) / (1024.0*1024.0), 2)
        except Exception:
            pass
    if video_page:
        record['video_url'] = video_page

    # determine local output directory from config
    cfg = load_json_or_empty(CONFIG_FILE, {})
    local_cfg = cfg.get('local', {}) if cfg else {}
    output_dir = local_cfg.get('download_dir') or os.path.join(APP_DIR, 'downloads')
    # ensure dir exists
    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception:
        pass

    # run download (blocking) - yt-dlp will create file in output_dir
    ok, path_or_error = download_audio(url, output_dir=output_dir)

    # determine final local_path: prefer the returned path, otherwise search for newest mp3
    local_path = None
    try:
        if ok and path_or_error:
            local_path = os.path.abspath(path_or_error)
        # if local_path exists but not under output_dir, move it
        if local_path and not os.path.abspath(local_path).startswith(os.path.abspath(output_dir)):
            try:
                dest = os.path.join(output_dir, os.path.basename(local_path))
                # avoid overwrite
                if os.path.exists(dest):
                    base, ext = os.path.splitext(os.path.basename(local_path))
                    i = 1
                    while True:
                        candidate = os.path.join(output_dir, f"{base}-{i}{ext}")
                        if not os.path.exists(candidate):
                            dest = candidate
                            break
                        i += 1
                shutil.move(local_path, dest)
                local_path = dest
            except Exception:
                # moving failed, keep original
                pass

        # if still no local_path, try to find newest mp3 in output_dir then cwd
        if not local_path:
            for sd in (output_dir, os.getcwd()):
                try:
                    mp3s = [os.path.join(sd, f) for f in os.listdir(sd) if f.lower().endswith('.mp3')]
                except Exception:
                    mp3s = []
                if mp3s:
                    mp3s.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                    candidate = mp3s[0]
                    # if candidate not in output_dir, move it
                    if not os.path.abspath(candidate).startswith(os.path.abspath(output_dir)):
                        try:
                            dest = os.path.join(output_dir, os.path.basename(candidate))
                            if os.path.exists(dest):
                                base, ext = os.path.splitext(os.path.basename(candidate))
                                i = 1
                                while True:
                                    cand2 = os.path.join(output_dir, f"{base}-{i}{ext}")
                                    if not os.path.exists(cand2):
                                        dest = cand2
                                        break
                                    i += 1
                            shutil.move(candidate, dest)
                            local_path = dest
                            break
                        except Exception:
                            # fallback to candidate path
                            local_path = candidate
                            break
                    else:
                        local_path = candidate
                        break
    except Exception as e:
        print('Error when locating/moving downloaded file:', e)

    end = datetime.now(tz).isoformat()
    # if download produced a local file path, record it
    if local_path:
        record['local_path'] = local_path

    record.update({'end': end, 'status': 'ok' if ok else 'failed', 'error': path_or_error if not ok else ''})
    append_job_record(record)

    # optionally upload to Google Drive or Nextcloud when enabled in config
    gd_result = None
    if ok:
        cfg = load_json_or_empty(CONFIG_FILE, {})
        # Nextcloud preferred
        nc_cfg = cfg.get('nextcloud', {}) if cfg else {}
        if nc_cfg.get('enabled'):
            try:
                local_path = path_or_error if path_or_error else None
                success, link_or_msg = upload_file_to_nextcloud(local_path, nc_cfg)
                if not success:
                    gd_result = ('nc_failed', link_or_msg)
                else:
                    gd_result = ('nc_ok', link_or_msg)
            except Exception as e:
                gd_result = ('nc_error', str(e))
            # If Nextcloud not enabled, we do not perform other cloud uploads

    # send Telegram notification if configured
    text = f"Pobieranie {'powiodło się' if ok else 'nie powiodło się'}: {query or url}\nStatus: {record.get('status')}"
    err_msg = ''
    if not ok:
        err_msg = path_or_error or record.get('error') or ''
    if err_msg:
        text += f"\nBłąd: {err_msg}"
    if ok and record.get('local_path'):
        text += f"\nLokalnie: {record.get('local_path')}"
    if gd_result:
        text += f"\nUpload: {gd_result[0]} - {gd_result[1]}"
        # if Nextcloud upload succeeded, persist remote_url into the last job record
        try:
            if gd_result[0] == 'nc_ok':
                remote = gd_result[1]
                jobs = load_json_or_empty(JOBS_FILE, [])
                # find the last record with this id
                for j in reversed(jobs):
                    if j.get('id') == record.get('id'):
                        j['remote_url'] = remote
                        break
                save_json(JOBS_FILE, jobs)
        except Exception as e:
            print('Failed to persist remote_url:', e)
    send_telegram_notification(text)


@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/api/search')
def api_search():
    q = request.args.get('query', '')
    if not q:
        return jsonify({'error': 'no query'}), 400
    results = search_youtube(q, limit=5)
    if results is None:
        return jsonify({'error': 'search_timeout', 'message': 'Wyszukiwanie trwało dłużej niż dozwolony limit'}), 504
    out = []
    for r in results:
        duration = int(r.get('duration') or 0)
        # try to estimate filesize from formats
        size = None
        formats = r.get('formats') or []
        for f in reversed(formats):
            if f.get('filesize'):
                size = f.get('filesize')
                break
            if f.get('filesize_approx'):
                size = f.get('filesize_approx')
                break

        size_human = None
        if size:
            for unit in ['B','KiB','MiB','GiB']:
                if size < 1024.0:
                    size_human = f"{size:.1f}{unit}"
                    break
                size /= 1024.0
        out.append({
            'title': r.get('title'),
            'duration': secs_to_hhmmss_dots(duration),
            'upload_date': format_upload_date(r.get('upload_date') or ''),
            'url': r.get('webpage_url') or r.get('url') or r.get('id'),
            'size': size_human,
        })
    return jsonify(out)


@app.route('/api/download', methods=['POST'])
def api_download():
    data = request.get_json() or {}
    url = data.get('url')
    query = data.get('query')
    if not url and not query:
        return jsonify({'error': 'url or query required'}), 400

    t = threading.Thread(target=perform_download_task, kwargs={'query': query, 'url': url})
    t.start()
    return jsonify({'status': 'scheduled'})


@app.route('/api/schedule', methods=['GET','POST','DELETE'])
def api_schedule():
    if request.method == 'GET':
        schedules = load_json_or_empty(SCHEDULES_FILE, [])
        return jsonify(schedules)

    if request.method == 'POST':
        data = request.get_json() or {}
        # expected: {query, type: 'weekly'|'daily'|'biweekly'|'monthly', day_of_week(0-6), hour, minute}
        if not data.get('query') or not data.get('type'):
            return jsonify({'error':'query and type required'}), 400
        entry = data.copy()
        entry['id'] = str(uuid4())
        entry['enabled'] = True
        # record creation time
        entry['created'] = datetime.now(tz).isoformat()
        schedules = load_json_or_empty(SCHEDULES_FILE, [])
        schedules.append(entry)
        save_json(SCHEDULES_FILE, schedules)
        schedule_job(entry)
        return jsonify(entry)

    if request.method == 'DELETE':
        data = request.get_json() or {}
        sid = data.get('id')
        if not sid:
            return jsonify({'error':'id required'}), 400
        schedules = load_json_or_empty(SCHEDULES_FILE, [])
        schedules = [s for s in schedules if s.get('id') != sid]
        save_json(SCHEDULES_FILE, schedules)
        # remove job from scheduler
        try:
            scheduler.remove_job(sid)
        except Exception:
            pass
        return jsonify({'status':'removed'})


@app.route('/api/jobs')
def api_jobs():
    jobs = load_json_or_empty(JOBS_FILE, [])
    # return up to 10 most recent jobs, newest first
    if not jobs:
        return jsonify([])
    # jobs are appended chronologically; pick last 10 and reverse
    last = jobs[-10:]
    last.reverse()
    return jsonify(last)


@app.route('/api/config', methods=['GET','POST'])
def api_config():
    if request.method == 'GET':
        return jsonify(load_json_or_empty(CONFIG_FILE, {}))
    data = request.get_json() or {}
    save_json(CONFIG_FILE, data)
    return jsonify({'status':'saved'})


@app.route('/api/gdrive_test', methods=['POST'])
def api_gdrive_test():
    return jsonify({'ok': False, 'message': 'google_drive integration removed'}), 410


@app.route('/api/gdrive_upload_sa', methods=['POST'])
def api_gdrive_upload_sa():
    return jsonify({'ok': False, 'message': 'google_drive integration removed'}), 410


def upload_file_to_nextcloud(local_path, nc_cfg):
    """Upload file to Nextcloud via WebDAV. nc_cfg should have: url (base webdav url), user, password, folder (remote folder path).
    Returns (True, remote_url) or (False, message).
    """
    try:
        import requests
    except Exception:
        return False, 'requests_missing'

    if not local_path or not os.path.exists(local_path):
        # try to find most recent mp3 in cwd
        mp3s = [os.path.join(os.getcwd(), f) for f in os.listdir(os.getcwd()) if f.lower().endswith('.mp3')]
        if not mp3s:
            return False, 'no local file found to upload'
        mp3s.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        local_path = mp3s[0]

    base = nc_cfg.get('webdav_url') or nc_cfg.get('url')
    user = nc_cfg.get('user')
    pwd = nc_cfg.get('password')
    folder = nc_cfg.get('folder', '')
    verify = nc_cfg.get('verify_ssl', True)
    if not base or not user or not pwd:
        return False, 'nextcloud config missing (webdav_url/user/password)'

    # ensure base ends without slash
    base = base.rstrip('/')
    remote_path = f"{folder.rstrip('/')}/{os.path.basename(local_path)}" if folder else os.path.basename(local_path)
    # full url: base + '/' + remote_path
    upload_url = f"{base}/{remote_path}"
    try:
        with open(local_path, 'rb') as fh:
            resp = requests.put(upload_url, data=fh, auth=(user, pwd), verify=verify, timeout=30)
        if resp.status_code in (200,201,204):
            return True, upload_url
        else:
            return False, f'status {resp.status_code}: {resp.text[:200]}'
    except Exception as e:
        return False, str(e)


@app.route('/api/nextcloud_test', methods=['POST'])
def api_nextcloud_test():
    cfg = load_json_or_empty(CONFIG_FILE, {})
    nc_cfg = cfg.get('nextcloud', {})
    if not nc_cfg.get('enabled'):
        return jsonify({'ok': False, 'message': 'nextcloud not enabled in config'}), 400
    try:
        success, msg = upload_file_to_nextcloud(None, nc_cfg)
        return jsonify({'ok': success, 'message': msg})
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)}), 500


def schedule_job(entry):
    # entry contains type, query, day_of_week (0-6), hour, minute
    typ = entry.get('type')
    hour = int(entry.get('hour', 0))
    minute = int(entry.get('minute', 0))
    day = entry.get('day_of_week')

    if typ == 'daily':
        trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
    elif typ == 'weekly' or typ == 'biweekly':
        # day expected 0-6 (Mon=0) -> APScheduler uses mon,tue...
        dow = ['mon','tue','wed','thu','fri','sat','sun'][int(day or 0)]
        trigger = CronTrigger(day_of_week=dow, hour=hour, minute=minute, timezone=tz)
    elif typ == 'monthly':
        # run on same day-of-month as created
        day_of_month = datetime.now(tz).day
        trigger = CronTrigger(day=day_of_month, hour=hour, minute=minute, timezone=tz)
    else:
        return

    def job_wrapper(entry_id=entry.get('id')):
        schedules = load_json_or_empty(SCHEDULES_FILE, [])
        item = next((s for s in schedules if s.get('id')==entry_id), None)
        if not item or not item.get('enabled'):
            return
        # biweekly: check parity
        if item.get('type') == 'biweekly':
            start_week = item.get('start_week')
            if not start_week:
                # set initial start_week
                item['start_week'] = datetime.now(tz).isocalendar()[1]
                schedules2 = load_json_or_empty(SCHEDULES_FILE, [])
                for s in schedules2:
                    if s.get('id') == item.get('id'):
                        s['start_week'] = item['start_week']
                save_json(SCHEDULES_FILE, schedules2)
            else:
                current_week = datetime.now(tz).isocalendar()[1]
                if ((current_week - int(start_week)) % 2) != 0:
                    # skip this week
                    return
        # perform download of newest for query
        # notify that scheduled job started
        try:
            start_text = (
                f"Zadanie harmonogramu uruchomione: '{item.get('query')}'\n"
                f"ID: {entry_id}\n"
                f"Typ: {item.get('type')}  Godzina: {item.get('hour')}:{item.get('minute')}"
            )
            send_telegram_notification(start_text)
        except Exception:
            # don't let notification failure stop the job
            pass

        perform_download_task(query=item.get('query'), scheduled_id=item.get('id'))

    # add job
    try:
        scheduler.add_job(job_wrapper, trigger, id=entry.get('id'))
    except Exception as e:
        print('Nie udało się dodać job:', e)


def load_and_schedule_all():
    schedules = load_json_or_empty(SCHEDULES_FILE, [])
    for s in schedules:
        schedule_job(s)


def get_local_download_dir():
    cfg = load_json_or_empty(CONFIG_FILE, {})
    local_cfg = cfg.get('local', {}) if cfg else {}
    return local_cfg.get('download_dir') or os.path.join(APP_DIR, 'downloads')


@app.route('/api/local_files', methods=['GET'])
def api_local_files():
    d = get_local_download_dir()
    files = []
    try:
        if os.path.exists(d):
            for fname in sorted(os.listdir(d), key=lambda x: os.path.getmtime(os.path.join(d, x)), reverse=True):
                fpath = os.path.join(d, fname)
                if os.path.isfile(fpath):
                    files.append({'name': fname, 'path': fpath, 'mtime': os.path.getmtime(fpath)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify(files)


@app.route('/api/local_files', methods=['DELETE'])
def api_local_files_delete():
    data = request.get_json() or {}
    path = data.get('path')
    if not path:
        return jsonify({'error': 'path required'}), 400
    # only allow deleting files inside the configured download dir
    d = os.path.abspath(get_local_download_dir())
    target = os.path.abspath(path)
    if not target.startswith(d):
        return jsonify({'error': 'path not allowed'}), 400
    try:
        if os.path.exists(target) and os.path.isfile(target):
            os.remove(target)
            return jsonify({'status': 'deleted'})
        else:
            return jsonify({'error': 'not_found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # ensure files exist
    for p, d in [(SCHEDULES_FILE, []), (JOBS_FILE, []), (CONFIG_FILE, {})]:
        if not os.path.exists(p):
            save_json(p, d)

    scheduler.start()
    load_and_schedule_all()
    app.run(host='0.0.0.0', port=8090)


# --- Google Drive optional helpers ---
# Google Drive integration removed
