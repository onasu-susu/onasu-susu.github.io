/**
 * 資料置き場のアップロード受付（Google Apps Script）
 *
 * 資料のページ（shiryo/index.html）から、ブラウザで暗号化済みのファイルと一覧（vault.json）を受け取り、
 * GitHub の onasu-susu.github.io リポジトリの shiryo/data/ に 1 コミットで保存する。
 *
 * GitHub のアクセストークンはこのスクリプトの「スクリプト プロパティ」にだけ置き、公開サイトには出さない。
 * 書きこめるのは shiryo/data/ の中（*.bin と vault.json）だけにしてある。
 *
 * スクリプト プロパティ
 *   GITHUB_TOKEN … GitHub の fine-grained トークン（onasu-susu.github.io だけ・Contents: Read and write）
 *   UPLOAD_KEY   … 資料のページの「アップロードの設定」に出る合言葉（資料のパスワードから作られる）
 */

const REPO = "onasu-susu/onasu-susu.github.io";
const BRANCH = "main";
const DIR = "shiryo/data";
const MAX_FILES = 20;
const MAX_BASE64 = 35 * 1024 * 1024; // 1 回で受け取る量（base64 で）

function doGet() {
  return ContentService.createTextOutput("資料のアップロード受付は動いています。");
}

function doPost(e) {
  try {
    const req = JSON.parse(e.postData.contents);
    const props = PropertiesService.getScriptProperties();
    const uploadKey = props.getProperty("UPLOAD_KEY");
    const token = props.getProperty("GITHUB_TOKEN");
    if (!uploadKey || !token) return reply({ ok: false, error: "not_configured" });
    if (!same(String(req.auth || ""), uploadKey.trim())) return reply({ ok: false, error: "auth" });

    const gh = github(token);
    if (req.action === "ping") return reply({ ok: true });
    if (req.action === "vault") return reply(latestVault(gh));
    if (req.action === "file") return reply(readFile(gh, req.id));
    if (req.action === "commit") return reply(commit(gh, req));
    return reply({ ok: false, error: "bad_request" });
  } catch (err) {
    return reply({ ok: false, error: "server", detail: String((err && err.message) || err) });
  }
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

// 合言葉の比べ方で長さや中身が分からないよう、最後まで比べる
function same(a, b) {
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

function github(token) {
  return function (path, method, body, accept) {
    const res = UrlFetchApp.fetch("https://api.github.com/repos/" + REPO + "/" + path, {
      method: method || "get",
      headers: {
        Authorization: "Bearer " + token,
        Accept: accept || "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      contentType: "application/json",
      payload: body ? JSON.stringify(body) : undefined,
      muteHttpExceptions: true,
    });
    const code = res.getResponseCode();
    if (code >= 300) {
      const err = new Error("github " + code);
      err.code = code;
      throw err;
    }
    return accept ? res : JSON.parse(res.getContentText());
  };
}

function latestVault(gh) {
  const head = gh("git/ref/heads/" + BRANCH).object.sha;
  const res = gh("contents/" + DIR + "/vault.json?ref=" + head, "get", null, "application/vnd.github.raw");
  return { ok: true, head: head, vault: res.getContentText() };
}

// アップロードしたばかりで、まだサイトに出ていないファイルを渡す
function readFile(gh, id) {
  if (!/^[0-9a-f]{24}$/.test(String(id))) return { ok: false, error: "bad_request" };
  const res = gh("contents/" + DIR + "/" + id + ".bin?ref=" + BRANCH, "get", null, "application/vnd.github.raw");
  return { ok: true, data: Utilities.base64Encode(res.getBlob().getBytes()) };
}

function commit(gh, req) {
  const files = req.files || [];
  const remove = req.remove || [];
  const isId = function (id) { return /^[0-9a-f]{24}$/.test(String(id)); };
  if (typeof req.head !== "string" || typeof req.vault !== "string" || typeof req.message !== "string") return { ok: false, error: "bad_request" };
  if (files.length > MAX_FILES || !files.every(function (f) { return isId(f.id) && typeof f.data === "string"; })) return { ok: false, error: "bad_request" };
  if (!remove.every(isId)) return { ok: false, error: "bad_request" };
  const total = files.reduce(function (n, f) { return n + f.data.length; }, 0);
  if (total > MAX_BASE64) return { ok: false, error: "too_large" };
  const vault = JSON.parse(req.vault);
  if (!vault.salt || !vault.iv || !vault.data || !vault.iterations) return { ok: false, error: "bad_request" };

  const head = gh("git/ref/heads/" + BRANCH).object.sha;
  if (head !== req.head) return { ok: false, error: "conflict" };
  const base = gh("git/commits/" + head);

  const tree = [];
  files.forEach(function (f) {
    const blob = gh("git/blobs", "post", { content: f.data, encoding: "base64" });
    tree.push({ path: DIR + "/" + f.id + ".bin", mode: "100644", type: "blob", sha: blob.sha });
  });
  remove.forEach(function (id) {
    tree.push({ path: DIR + "/" + id + ".bin", mode: "100644", type: "blob", sha: null });
  });
  const vaultBlob = gh("git/blobs", "post", { content: JSON.stringify(vault, null, 2) + "\n", encoding: "utf-8" });
  tree.push({ path: DIR + "/vault.json", mode: "100644", type: "blob", sha: vaultBlob.sha });

  const newTree = gh("git/trees", "post", { base_tree: base.tree.sha, tree: tree });
  const made = gh("git/commits", "post", { message: req.message.slice(0, 200), tree: newTree.sha, parents: [head] });
  try {
    gh("git/refs/heads/" + BRANCH, "patch", { sha: made.sha });
  } catch (err) {
    if (err.code === 422) return { ok: false, error: "conflict" };
    throw err;
  }
  return { ok: true, commit: made.sha };
}
