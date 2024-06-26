import hashlib
import json
import os
import shutil
import textwrap
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, send_file
from key_generator.key_generator import generate as key_generator

app = Flask(__name__)

app.config['DATA_FOLDER'] = Path('./data') if not os.getenv("IN_DOCKER") else Path('/app/data/')
app.config['UPLOAD_FOLDER'] = app.config['DATA_FOLDER'] / 'modpacks'


def format_bytes(size):
    power = 2 ** 10
    n = 0
    units = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f}{units[n]}"


@app.route('/download/<code>', methods=['GET'])
def download_file(code):
    modpack = app.config['UPLOAD_FOLDER'] / f"{code}.zip"
    if not code or not os.path.exists(modpack):
        return {"error": "Invalid code"}, 400
    return send_file(modpack, as_attachment=True)


@app.route('/user', methods=['GET'])
def def_users():
    act = request.args.get("act")
    if act not in ['new', 'check']:
        return jsonify({"error": "Unknown act"}), 400
    nick = request.args.get("nick")
    pswd = request.args.get("pswd")
    if not nick or not pswd:
        return jsonify({"error": "No nick or pswd provided."}), 400
    with open(app.config['DATA_FOLDER'] / "users.json", "r") as f:
        users = json.load(f)
    user = None
    if users['users'].get(nick):
        user = users['users'][nick]
    if act == "new":
        if user:
            return jsonify({"error": "User already created."})
        token = key_generator(5, ['-', '{', ')', '@', '=', '<>'], 3, 4).get_key()
        while True:
            if not users['link'].get(token):
                break
            token = key_generator(5, [':'], 3, 4).get_key()
        user_info = {nick: {"pswd": hashlib.sha256(pswd.encode()).hexdigest(), "token": token, "modpacks": []}}
        users['users'].update(user_info)
        users['link'][token] = nick
        with open(app.config['DATA_FOLDER'] / "users.json", "w") as f:
            json.dump(users, f, indent=2)
        return jsonify({"token": token})
    if act == "check":
        if user and hashlib.sha256(pswd.encode()).hexdigest() == user['pswd']:
            return jsonify({"token": user['token']})
        return jsonify({"error": "Bad password or user not found."})


def _create_empty_modpack(code, nick, users=None):
    modpack = app.config['UPLOAD_FOLDER'] / code
    os.makedirs(modpack)
    os.makedirs(modpack / "mods")
    os.makedirs(modpack / "config")
    info = {"name": request.args.get("name"), "code": code, "owner": nick, "size": 0, "files_count": 0,
            "mods": {}, "config": {}}
    with open(modpack / "info.json", "w") as f:
        json.dump(info, f)
    open(app.config['UPLOAD_FOLDER'] / f"{code}.zip", "w").close()
    if users:
        users['users'][nick]['modpacks'].append(code)
        with open(app.config['DATA_FOLDER'] / "users.json", "w") as f:
            json.dump(users, f, indent=2)


@app.route('/upload/<code>/<token>', methods=['POST', 'GET'])
def upload_file(code, token):
    with open(app.config['DATA_FOLDER'] / "users.json", "r") as f:
        users = json.load(f)
    nick = users['link'].get(token)
    if not nick:
        return jsonify({"error": "Invalid token"}), 400

    if request.method == 'GET':
        if code == "get_code":
            code = key_generator(2, ['-', ], 4, 5, capital='mix').get_key()
            modpack = app.config['UPLOAD_FOLDER'] / code
            while True:
                if not os.path.exists(modpack):
                    break
                code = key_generator(2, ['-', ], 4, 5, capital='mix').get_key()
                modpack = app.config['UPLOAD_FOLDER'] / code
            _create_empty_modpack(code, nick, users)
            return {"code": code}
        else:
            modpack = app.config['UPLOAD_FOLDER'] / code
            if not code or not os.path.exists(modpack):
                return {"error": "Invalid code"}, 400
            act = request.args.get("act")
            if act not in ['reset', 'lock', 'unlock']:
                return jsonify({"error": "Unknown act"}), 400
            lockfile = modpack / "lock"
            if act == "unlock":
                os.remove(lockfile)
                return {"message": f"modpack unlocked"}
            else:
                if os.path.isfile(modpack / "lock"):
                    return {"message": f"modpack locked"}
            if act == "lock":
                open(lockfile, "w").close()
                return {"message": f"modpack locked"}
            if act == "reset":
                shutil.rmtree(modpack)
                os.remove(app.config['UPLOAD_FOLDER'] / f"{code}.zip")
                _create_empty_modpack(code, nick)
                return {"message": "modpack emtpy"}
        return {"error": "Invalid request"}, 400

    files = request.files
    if not files:
        return jsonify({"error": "No files provided."}), 400

    modpack = app.config['UPLOAD_FOLDER'] / code
    if not code or not os.path.exists(modpack):
        return {"error": "Invalid code"}, 400

    with open(modpack / "info.json", "r") as f:
        info = json.load(f)

    if info['owner'] != nick:
        return {"error": "You not owner of that modpack."}, 403

    uploaded_count = 0
    uploaded_size = 0
    for file in files:
        file = files.get(file)
        flm = file.filename.replace('\\', '/')
        file_path = ftype = None

        mx = flm.find("mods/")
        if mx != -1:
            ftype = "mods"
            file_path = modpack / flm[mx:]
        cx = flm.find("config/")
        if cx != -1:
            ftype = "config"
            file_path = modpack / flm[cx:]

        if file_path:
            os.makedirs(file_path.parent, exist_ok=True)
            sha256_hash = hashlib.sha256()
            for byte_block in iter(lambda: file.read(4096), b""):
                sha256_hash.update(byte_block)
            hash = sha256_hash.hexdigest()
            file.seek(0)
            sname = str(file_path.name)
            if info[ftype].get(sname):
                if info[ftype][sname]['sha256'] == hash:
                    continue
            file.save(file_path)
            size = os.path.getsize(file_path)
            info['size'] += size
            info[ftype].update({sname: {"path": str(file_path), "size": size, "sha256": hash}})
            info['files_count'] += 1
            uploaded_count += 1
            uploaded_size += size

    if uploaded_count > 0:
        with open(modpack / "info.json", "w") as f:
            json.dump(info, f, indent=2)
        with zipfile.ZipFile(app.config['UPLOAD_FOLDER'] / f"{code}.zip", 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(modpack):
                for file in files:
                    fp = os.path.join(root, file)
                    # noinspection PyTypeChecker
                    zipf.write(fp, os.path.relpath(fp, modpack))

    return jsonify(
        {"message": f"{uploaded_count} files with size {format_bytes(uploaded_size)} uploaded for code {code}."})


@app.route('/info/<path:code>', methods=['GET'])
def info_by_code(code):
    modpack = app.config['UPLOAD_FOLDER'] / code
    oformat = request.args.get("format") or "html"
    if not code or not os.path.exists(modpack):
        if oformat == "json":
            return {"error": "Invalid code"}, 400
        return f"<center><h1>Сборка \"{code}\" не найдена</h1></center>", 400
    with open(modpack / "info.json", "r") as f:
        info = json.load(f)
    if oformat == "json":
        return jsonify(info)
    return textwrap.dedent(f"""
        <center><h1>Modpack code: {code}</h1></center>
        <p style="font-size: 1.5em;">Общая информация информация о сборке</p>
        <p style="font-size: 1.17em;">
        Название: \"<a style="font-weight: bold;">{info['name']}</a>\"<br>
        Создатель: {info['owner']}<br>
        Всего файлов: {info['files_count']}<br>
        Размер: {format_bytes(info['size'])}<br>
        Последние обновление: {datetime.fromtimestamp(os.path.getmtime(app.config['UPLOAD_FOLDER'] / f"{code}.zip"))}<br>
        Путь до архива: {app.config['UPLOAD_FOLDER'] / f"{code}.zip"}
        </p>
        <p style="font-size: 1.5em;">Список файлов:</p>
        <p style="font-size: 1.4em;">Моды:</p>
        <p style="font-size: 1.17em;">
        {"<br>".join([f"{i}. {v}" for i, v in enumerate(info['mods'].keys(), 1)])}
        </p>
        <p style="font-size: 1.4em;">Остальное:</p>
        <p style="font-size: 1.17em;">
        {"<br>".join([i['path'] for i in info['config'].values()])}
        </p>
        """)


os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
if not os.path.exists(app.config['DATA_FOLDER'] / "users.json"):
    with open(app.config['DATA_FOLDER'] / "users.json", "w") as _f:
        json.dump({"users": {}, "link": {}}, _f)

if __name__ == '__main__':
    app.run(port=8000, debug=True)
