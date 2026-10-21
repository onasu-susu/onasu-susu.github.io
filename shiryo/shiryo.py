#!/usr/bin/env python3
"""資料置き場にファイルを暗号化して追加・整理するスクリプト

使い方（リポジトリのどこからでも実行できます）
  python3 shiryo/shiryo.py add ファイル ...                     いちばん上に追加してプッシュ
  python3 shiryo/shiryo.py add --to 塾用/大和大学過去問 ファイル ...  フォルダの中に追加してプッシュ
  python3 shiryo/shiryo.py list                                  フォルダとファイルの一覧
  python3 shiryo/shiryo.py mkdir 塾用 講義関連/微積分Ⅱ ...          フォルダを作ってプッシュ
  python3 shiryo/shiryo.py move 名前 フォルダ                     ファイルをフォルダへ移してプッシュ
                                                                 （名前は一部だけでもよい。「/」でいちばん上へ）
  python3 shiryo/shiryo.py remove 名前 ...                       ファイルを削除してプッシュ
  python3 shiryo/shiryo.py rmdir フォルダ ...                     空のフォルダを削除してプッシュ
  python3 shiryo/shiryo.py rename いまの名前 新しい名前              ファイル名を変えてプッシュ
  python3 shiryo/shiryo.py passwd                                パスワードを変えて、全部のファイルを暗号化し直してプッシュ

--no-push を付けると、コミット・プッシュをせずにファイルだけ書きかえます。
パスワードは最初の add のときに決めます。以降は同じパスワードを入れてください。
何回も続けて使うときは、次のようにするとパスワードを 1 回入れるだけですみます。
  read -s JUKU_PASSWORD && export JUKU_PASSWORD
"""

import base64
import datetime
import getpass
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.exceptions import InvalidTag
except ImportError:
    sys.exit(
        "暗号化に使う cryptography が入っていません。次のコマンドで入れてから、もう一度実行してください。\n"
        "  python3 -m pip install --user cryptography\n"
        "（Homebrew の Python でエラーになる場合は  brew install python-cryptography）"
    )

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
VAULT = DATA / "vault.json"
ITERATIONS = 600_000


def b64(b):
    return base64.b64encode(b).decode()


def unb64(s):
    return base64.b64decode(s)


def derive_key(password, salt, iterations):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(password.encode("utf-8"))


def ask_password(new):
    env = os.environ.get("JUKU_PASSWORD")
    if env:
        return env
    if not new:
        return getpass.getpass("パスワード: ")
    print("はじめてなので、資料置き場のパスワードを決めます。")
    return ask_new_password()


def ask_new_password():
    env = os.environ.get("JUKU_NEW_PASSWORD")
    if env:
        return env
    print("塾の PC などで入力するものです。単語を 3〜4 個つなげたような、長めのものがおすすめです。")
    print("名前やメールアドレスなど、人に知られている文字列は使わないでください。")
    while True:
        p = getpass.getpass("新しいパスワード: ")
        if len(p) < 8:
            print("8 文字以上にしてください。")
            continue
        if getpass.getpass("もう一度: ") != p:
            print("一致しませんでした。もう一度入れてください。")
            continue
        return p


# 暗号化して保存する中身：{"folders": [フォルダの一覧], "files": [ファイルの一覧], "uploader": アップロード受付の URL（あれば）}
# （フォルダができる前の古い形は、ファイルの一覧だけのリスト）
def load():
    """(key, salt, iterations, box) を返す。まだ何もなければ新しく作る"""
    if not VAULT.exists():
        salt = secrets.token_bytes(16)
        password = ask_password(new=True)
        return derive_key(password, salt, ITERATIONS), salt, ITERATIONS, {"folders": [], "files": []}
    vault = json.loads(VAULT.read_text())
    salt = unb64(vault["salt"])
    iterations = vault["iterations"]
    key = derive_key(ask_password(new=False), salt, iterations)
    try:
        plain = AESGCM(key).decrypt(unb64(vault["iv"]), unb64(vault["data"]), None)
    except InvalidTag:
        sys.exit("パスワードがちがいます。")
    box = json.loads(plain)
    if isinstance(box, list):
        box = {"folders": [], "files": box}
    for f in box["files"]:
        f.setdefault("folder", "")
    return key, salt, iterations, box


def save(key, salt, iterations, box):
    box["folders"] = sorted(set(box["folders"]))
    iv = secrets.token_bytes(12)
    plain = json.dumps(box, ensure_ascii=False).encode("utf-8")
    vault = {
        "v": 2,
        "kdf": "PBKDF2-SHA256",
        "iterations": iterations,
        "salt": b64(salt),
        "iv": b64(iv),
        "data": b64(AESGCM(key).encrypt(iv, plain, None)),
    }
    DATA.mkdir(exist_ok=True)
    VAULT.write_text(json.dumps(vault, indent=2) + "\n")


def folder_path(text):
    """「塾用/大和大学過去問」のような形にそろえる。「/」や空ならいちばん上（""）"""
    parts = [p.strip() for p in text.replace("\\", "/").split("/") if p.strip()]
    return "/".join(parts)


def add_folder(box, folder):
    """フォルダと、その上のフォルダをまとめて作る"""
    parts = folder.split("/") if folder else []
    for i in range(1, len(parts) + 1):
        box["folders"].append("/".join(parts[:i]))


# いちばん上のフォルダは、この順番で並べる（ほかは名前順）
ORDER = ["塾用", "講義関連", "その他"]


def rank(folder):
    return ORDER.index(folder) if folder in ORDER else len(ORDER)


def parent(folder):
    return folder.rsplit("/", 1)[0] if "/" in folder else ""


def where(f):
    return (f["folder"] + "/" if f["folder"] else "") + f["name"]


def size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def git(*args):
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"git {args[0]} でエラーになりました。\n{r.stderr.strip()}")
    return r.stdout.strip()


def prepare_git():
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        sys.exit(f"いまのブランチは {branch} です。main に切りかえてから実行してください（git checkout main）。")
    print("最新の状態を取ってきています…")
    git("pull", "--ff-only")


def publish(message):
    git("add", "-A", "--", str(DATA))
    if not git("status", "--porcelain", "--", str(DATA)):
        print("変更はありませんでした。")
        return
    git("commit", "-m", message)
    print("プッシュしています…")
    git("push")
    print("完了しました。サイトに出るまで数分かかることがあります。")


def find(box, name):
    """名前がぴったり合うファイル。なければ、名前の一部に name をふくむファイル"""
    exact = [f for f in box["files"] if f["name"] == name or where(f) == name]
    if exact:
        return exact
    return [f for f in box["files"] if name in f["name"]]


def add(paths, folder, push):
    paths = [Path(p).expanduser() for p in paths]
    for p in paths:
        if not p.is_file():
            sys.exit(f"ファイルが見つかりません: {p}")
        if p.stat().st_size > 50 * 1024 * 1024:
            sys.exit(f"50MB をこえるファイルは置けません: {p.name}")
    if push:
        prepare_git()
    key, salt, iterations, box = load()
    add_folder(box, folder)
    DATA.mkdir(exist_ok=True)
    # 日付順に並べられるよう、追加した時刻まで記録する
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for p in paths:
        old = [f for f in box["files"] if f["name"] == p.name and f["folder"] == folder]
        for f in old:
            (DATA / f"{f['id']}.bin").unlink(missing_ok=True)
        box["files"] = [f for f in box["files"] if f not in old]
        fid = secrets.token_hex(12)
        iv = secrets.token_bytes(12)
        body = p.read_bytes()
        (DATA / f"{fid}.bin").write_bytes(iv + AESGCM(key).encrypt(iv, body, None))
        entry = {"id": fid, "name": p.name, "size": len(body), "added": now, "folder": folder}
        box["files"].append(entry)
        print(("上書き: " if old else "追加: ") + where(entry))
    save(key, salt, iterations, box)
    if push:
        publish(f"資料を{len(paths)}件追加" + (f"（{folder}）" if folder else ""))


def mkdir(folders, push):
    if push:
        prepare_git()
    key, salt, iterations, box = load()
    for folder in folders:
        folder = folder_path(folder)
        if not folder:
            continue
        add_folder(box, folder)
        print("フォルダ: " + folder)
    save(key, salt, iterations, box)
    if push:
        publish("資料のフォルダを作成")


def move(name, folder, push):
    if push:
        prepare_git()
    key, salt, iterations, box = load()
    hit = find(box, name)
    if not hit:
        sys.exit(f"その名前のファイルはありません: {name}（list で名前を確認できます）")
    add_folder(box, folder)
    for f in hit:
        if any(g is not f and g["name"] == f["name"] and g["folder"] == folder for g in box["files"]):
            sys.exit(f"移す先に同じ名前のファイルがあります: {f['name']}")
    for f in hit:
        before = where(f)
        f["folder"] = folder
        print(f"移動: {before} → {folder or '（いちばん上）'}/")
    save(key, salt, iterations, box)
    if push:
        publish(f"資料を{len(hit)}件移動" + (f"（{folder}）" if folder else ""))


def remove(names, push):
    if push:
        prepare_git()
    key, salt, iterations, box = load()
    for name in names:
        hit = [f for f in box["files"] if f["name"] == name or where(f) == name]
        if not hit:
            sys.exit(f"その名前のファイルはありません: {name}（list で名前を確認できます）")
        for f in hit:
            (DATA / f"{f['id']}.bin").unlink(missing_ok=True)
            print("削除: " + where(f))
        box["files"] = [f for f in box["files"] if f not in hit]
    save(key, salt, iterations, box)
    if push:
        publish(f"資料を{len(names)}件削除")


def rmdir(folders, push):
    if push:
        prepare_git()
    key, salt, iterations, box = load()
    for folder in folders:
        folder = folder_path(folder)
        if folder not in box["folders"]:
            sys.exit(f"そのフォルダはありません: {folder}")
        inside = [f for f in box["files"] if f["folder"] == folder or f["folder"].startswith(folder + "/")]
        sub = [d for d in box["folders"] if d.startswith(folder + "/")]
        if inside or sub:
            sys.exit(f"フォルダの中が空ではありません: {folder}（先に中身を移すか消してください）")
        box["folders"] = [d for d in box["folders"] if d != folder]
        print("フォルダを削除: " + folder)
    save(key, salt, iterations, box)
    if push:
        publish("資料のフォルダを削除")


def passwd(push):
    if push:
        prepare_git()
    if not VAULT.exists():
        sys.exit("まだファイルがないので、変えるパスワードがありません。最初の add でパスワードを決めてください。")
    print("いまのパスワードを入れてください。")
    old_key, _, _, box = load()
    print("新しいパスワードを決めます。")
    salt = secrets.token_bytes(16)
    key = derive_key(ask_new_password(), salt, ITERATIONS)
    # 全部のファイルを新しい鍵で暗号化し直す。古い鍵では開けなくなるよう、ファイル名（id）も新しくする
    for f in box["files"]:
        old = DATA / f"{f['id']}.bin"
        buf = old.read_bytes()
        body = AESGCM(old_key).decrypt(buf[:12], buf[12:], None)
        f["id"] = secrets.token_hex(12)
        iv = secrets.token_bytes(12)
        (DATA / f"{f['id']}.bin").write_bytes(iv + AESGCM(key).encrypt(iv, body, None))
        old.unlink()
        print("暗号化し直し: " + where(f))
    save(key, salt, ITERATIONS, box)
    print("パスワードを変えました。")
    if box.get("uploader"):
        print("※ ブラウザからのアップロードを使っている場合は、資料のページを新しいパスワードで開き、")
        print("   「アップロードの設定」に出る新しい合言葉を、Apps Script の UPLOAD_KEY に入れかえてください。")
    if push:
        publish("資料置き場のパスワードを変更")


def rename(old, new, push):
    if push:
        prepare_git()
    key, salt, iterations, box = load()
    hit = [f for f in box["files"] if f["name"] == old or where(f) == old]
    if not hit:
        sys.exit(f"その名前のファイルはありません: {old}（list で名前を確認できます）")
    for f in hit:
        if any(g["name"] == new and g["folder"] == f["folder"] for g in box["files"]):
            sys.exit(f"同じフォルダに同じ名前のファイルがすでにあります: {new}")
    for f in hit:
        f["name"] = new
    save(key, salt, iterations, box)
    print(f"名前を変えました: {old} → {new}")
    if push:
        publish("資料の名前を変更")


def show():
    if not VAULT.exists():
        print("まだファイルはありません。")
        return
    _, _, _, box = load()

    def walk(folder, depth):
        indent = "  " * depth
        subs = sorted((d for d in set(box["folders"]) if parent(d) == folder), key=lambda d: (rank(d), d))
        for d in subs:
            print(f"{indent}📁 {d.rsplit('/', 1)[-1]}/")
            walk(d, depth + 1)
        for f in sorted((f for f in box["files"] if f["folder"] == folder), key=lambda f: f["name"]):
            print(f"{indent}{f['added'][:10]}  {size(f['size']):>8}  {f['name']}")

    if not box["files"] and not box["folders"]:
        print("まだファイルはありません。")
        return
    walk("", 0)


def main():
    args = sys.argv[1:]
    push = "--no-push" not in args
    args = [a for a in args if a != "--no-push"]
    folder = ""
    if "--to" in args:
        i = args.index("--to")
        if i + 1 >= len(args):
            sys.exit("--to のあとにフォルダ名を書いてください。")
        folder = folder_path(args[i + 1])
        del args[i:i + 2]
    commands = ("add", "list", "mkdir", "move", "remove", "rmdir", "rename", "passwd")
    if (not args or args[0] not in commands
            or (args[0] in ("add", "remove", "mkdir", "rmdir") and len(args) < 2)
            or (args[0] in ("rename", "move") and len(args) != 3)):
        sys.exit(__doc__)
    cmd = args[0]
    if cmd == "add":
        add(args[1:], folder, push)
    elif cmd == "mkdir":
        mkdir(args[1:], push)
    elif cmd == "move":
        move(args[1], folder_path(args[2]), push)
    elif cmd == "remove":
        remove(args[1:], push)
    elif cmd == "rmdir":
        rmdir(args[1:], push)
    elif cmd == "rename":
        rename(args[1], args[2], push)
    elif cmd == "passwd":
        passwd(push)
    else:
        show()


if __name__ == "__main__":
    main()
