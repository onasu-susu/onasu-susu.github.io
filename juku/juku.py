#!/usr/bin/env python3
"""塾のプリント置き場にファイルを暗号化して追加・削除するスクリプト

使い方（リポジトリのどこからでも実行できます）
  python3 juku/juku.py add ~/Downloads/プリント.pdf ...   ファイルを追加してプッシュ
  python3 juku/juku.py list                               入っているファイルの一覧
  python3 juku/juku.py remove ファイル名 ...              ファイルを削除してプッシュ
  python3 juku/juku.py rename いまの名前 新しい名前          ファイル名を変えてプッシュ
  python3 juku/juku.py passwd                             パスワードを変えて、全部のファイルを暗号化し直してプッシュ

--no-push を付けると、コミット・プッシュをせずにファイルだけ書きかえます。
パスワードは最初の add のときに決めます。以降は同じパスワードを入れてください。
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

JUKU = Path(__file__).resolve().parent
ROOT = JUKU.parent
DATA = JUKU / "data"
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
    print("はじめてなので、プリント置き場のパスワードを決めます。")
    return ask_new_password()


def ask_new_password():
    env = os.environ.get("JUKU_NEW_PASSWORD")
    if env:
        return env
    print("塾の PC で入力するものです。単語を 3〜4 個つなげたような、長めのものがおすすめです。")
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


def load():
    """(key, salt, iterations, files) を返す。まだ何もなければ新しく作る"""
    if not VAULT.exists():
        salt = secrets.token_bytes(16)
        password = ask_password(new=True)
        return derive_key(password, salt, ITERATIONS), salt, ITERATIONS, []
    vault = json.loads(VAULT.read_text())
    salt = unb64(vault["salt"])
    iterations = vault["iterations"]
    key = derive_key(ask_password(new=False), salt, iterations)
    try:
        plain = AESGCM(key).decrypt(unb64(vault["iv"]), unb64(vault["data"]), None)
    except InvalidTag:
        sys.exit("パスワードがちがいます。")
    return key, salt, iterations, json.loads(plain)


def save(key, salt, iterations, files):
    iv = secrets.token_bytes(12)
    plain = json.dumps(files, ensure_ascii=False).encode("utf-8")
    vault = {
        "v": 1,
        "kdf": "PBKDF2-SHA256",
        "iterations": iterations,
        "salt": b64(salt),
        "iv": b64(iv),
        "data": b64(AESGCM(key).encrypt(iv, plain, None)),
    }
    DATA.mkdir(exist_ok=True)
    VAULT.write_text(json.dumps(vault, indent=2) + "\n")


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
    git("commit", "-m", message)
    print("プッシュしています…")
    git("push")
    print("完了しました。サイトに出るまで数分かかることがあります。")


def add(paths, push):
    paths = [Path(p).expanduser() for p in paths]
    for p in paths:
        if not p.is_file():
            sys.exit(f"ファイルが見つかりません: {p}")
        if p.stat().st_size > 50 * 1024 * 1024:
            sys.exit(f"50MB をこえるファイルは置けません: {p.name}")
    if push:
        prepare_git()
    key, salt, iterations, files = load()
    DATA.mkdir(exist_ok=True)
    # 日付順に並べられるよう、追加した時刻まで記録する
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for p in paths:
        old = [f for f in files if f["name"] == p.name]
        for f in old:
            (DATA / f"{f['id']}.bin").unlink(missing_ok=True)
        files = [f for f in files if f["name"] != p.name]
        fid = secrets.token_hex(12)
        iv = secrets.token_bytes(12)
        body = p.read_bytes()
        (DATA / f"{fid}.bin").write_bytes(iv + AESGCM(key).encrypt(iv, body, None))
        files.append({"id": fid, "name": p.name, "size": len(body), "added": now})
        print(("上書き: " if old else "追加: ") + p.name)
    save(key, salt, iterations, files)
    if push:
        publish(f"塾のプリントを{len(paths)}件追加")


def remove(names, push):
    if push:
        prepare_git()
    key, salt, iterations, files = load()
    for name in names:
        hit = [f for f in files if f["name"] == name]
        if not hit:
            sys.exit(f"その名前のファイルはありません: {name}（list で名前を確認できます）")
        for f in hit:
            (DATA / f"{f['id']}.bin").unlink(missing_ok=True)
        files = [f for f in files if f["name"] != name]
        print("削除: " + name)
    save(key, salt, iterations, files)
    if push:
        publish(f"塾のプリントを{len(names)}件削除")


def passwd(push):
    if push:
        prepare_git()
    if not VAULT.exists():
        sys.exit("まだファイルがないので、変えるパスワードがありません。最初の add でパスワードを決めてください。")
    print("いまのパスワードを入れてください。")
    old_key, _, _, files = load()
    print("新しいパスワードを決めます。")
    salt = secrets.token_bytes(16)
    key = derive_key(ask_new_password(), salt, ITERATIONS)
    # 全部のファイルを新しい鍵で暗号化し直す。古い鍵では開けなくなるよう、ファイル名（id）も新しくする
    for f in files:
        old = DATA / f"{f['id']}.bin"
        buf = old.read_bytes()
        body = AESGCM(old_key).decrypt(buf[:12], buf[12:], None)
        f["id"] = secrets.token_hex(12)
        iv = secrets.token_bytes(12)
        (DATA / f"{f['id']}.bin").write_bytes(iv + AESGCM(key).encrypt(iv, body, None))
        old.unlink()
        print("暗号化し直し: " + f["name"])
    save(key, salt, ITERATIONS, files)
    print("パスワードを変えました。")
    if push:
        publish("塾のプリント置き場のパスワードを変更")


def rename(old, new, push):
    if push:
        prepare_git()
    key, salt, iterations, files = load()
    hit = [f for f in files if f["name"] == old]
    if not hit:
        sys.exit(f"その名前のファイルはありません: {old}（list で名前を確認できます）")
    if any(f["name"] == new for f in files):
        sys.exit(f"同じ名前のファイルがすでにあります: {new}")
    for f in hit:
        f["name"] = new
    save(key, salt, iterations, files)
    print(f"名前を変えました: {old} → {new}")
    if push:
        publish("塾のプリントの名前を変更")


def show():
    if not VAULT.exists():
        print("まだファイルはありません。")
        return
    _, _, _, files = load()
    if not files:
        print("まだファイルはありません。")
    for f in sorted(files, key=lambda f: f["name"]):
        print(f"{f['added'][:10]}  {size(f['size']):>8}  {f['name']}")


def main():
    args = sys.argv[1:]
    push = "--no-push" not in args
    args = [a for a in args if a != "--no-push"]
    if (not args or args[0] not in ("add", "list", "remove", "rename", "passwd")
            or (args[0] in ("add", "remove") and len(args) < 2)
            or (args[0] == "rename" and len(args) != 3)):
        sys.exit(__doc__)
    if args[0] == "add":
        add(args[1:], push)
    elif args[0] == "remove":
        remove(args[1:], push)
    elif args[0] == "rename":
        rename(args[1], args[2], push)
    elif args[0] == "passwd":
        passwd(push)
    else:
        show()


if __name__ == "__main__":
    main()
