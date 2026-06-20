#!/usr/bin/env python3
"""
YT Downloader v3
- yt-dlp 자동 설치
- 모드별 올바른 다운로드 (video/audio/subtitle/all)
- 영어 자막 별도 처리 (ko 우선, en 선택 가능)
- 다운로드 후 버튼 활성화 유지
- 다운로드 가능 항목 미리 표시 (화질/음성포맷/자막언어)
- Windows 한글 인코딩 수정
"""

import os, sys, json, uuid, threading, subprocess, webbrowser, time, traceback, shutil
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

# ── 경로 설정 ─────────────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys._MEIPASS)
    EXE_DIR  = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent
    EXE_DIR  = BASE_DIR

DOWNLOAD_DIR = EXE_DIR / "YT_Downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)
IS_WIN = sys.platform == "win32"

# ── yt-dlp 자동 설치 ──────────────────────────────────────────────────────
def setup_ytdlp() -> str:
    fname = "yt-dlp.exe" if IS_WIN else "yt-dlp"
    # 1) PyInstaller 번들 내부 (sys._MEIPASS) - 최우선
    if getattr(sys, "frozen", False):
        bundled = BASE_DIR / fname
        if bundled.exists():
            print(f"[yt-dlp] 번들 발견: {bundled}")
            return str(bundled)
    # 2) exe 옆 폴더
    local = EXE_DIR / fname
    if local.exists():
        print(f"[yt-dlp] 로컬 발견: {local}")
        return str(local)
    # 3) 시스템 PATH
    found = shutil.which("yt-dlp")
    if found:
        print(f"[yt-dlp] PATH 발견: {found}")
        return found
    # 4) pip install
    print("[yt-dlp] pip install 시도...")
    try:
        subprocess.run([sys.executable,"-m","pip","install","yt-dlp","-q"],
            check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        found = shutil.which("yt-dlp")
        if found: return found
        return f"{sys.executable} -m yt_dlp"
    except: pass
    # 5) GitHub 직접 다운로드
    print("[yt-dlp] GitHub 바이너리 다운로드...")
    try:
        import urllib.request
        url = ("https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
               if IS_WIN else "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp")
        dest = EXE_DIR / fname
        urllib.request.urlretrieve(url, dest)
        if not IS_WIN: os.chmod(dest, 0o755)
        return str(dest)
    except Exception as e: print(f"[yt-dlp] 실패: {e}")
    return "yt-dlp"

def setup_ffmpeg() -> str:
    fname = "ffmpeg.exe" if IS_WIN else "ffmpeg"
    # 1) PyInstaller 번들 내부
    if getattr(sys, "frozen", False):
        bundled = BASE_DIR / fname
        if bundled.exists():
            print(f"[ffmpeg] 번들 발견: {bundled}")
            return str(bundled)
    # 2) exe 옆 폴더
    local = EXE_DIR / fname
    if local.exists(): return str(local)
    # 3) 시스템 PATH
    return shutil.which("ffmpeg") or ""

print("[YT Downloader] 초기화 중...")
YTDLP  = setup_ytdlp()
FFMPEG = setup_ffmpeg()
print(f"[YT Downloader] yt-dlp : {YTDLP}")
print(f"[YT Downloader] ffmpeg : {FFMPEG or '없음'}")

def ytdlp_cmd(args):
    if YTDLP.startswith(sys.executable):
        return [sys.executable, "-m", "yt_dlp"] + args
    return [YTDLP] + args

def make_si():
    if not IS_WIN: return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return si

def get_enc():
    if not IS_WIN: return "utf-8"
    import locale
    return locale.getpreferredencoding(False) or "cp949"

def human_size(b):
    for u in ["B","KB","MB","GB"]:
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def fmt_dur(sec):
    if not sec: return "-"
    h, r = divmod(int(sec), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

# ── 진행 상태 저장소 ───────────────────────────────────────────────────────
progress_store = {}

# ── 다운로드 로직 ─────────────────────────────────────────────────────────
def make_video_fmt(quality: str | None, has_ffmpeg: bool) -> str:
    """
    ffmpeg 유무 + 선택 화질에 따라 yt-dlp 포맷 셀렉터 문자열 반환.

    [ffmpeg 있음]
      DASH video-only + audio-only 두 스트림을 별도로 받아 병합.
      quality 지정 시: 정확히 그 높이 우선, 없으면 그 이하 최고
      ext 제약 없음 → webm/mp4 모두 커버
    [ffmpeg 없음]
      영상+음성이 합쳐진 통합(progressive) 포맷만 선택.
      quality 지정 시: 정확히 그 높이 우선, 없으면 통합 중 최고
    """
    if has_ffmpeg:
        if quality:
            # 정확히 그 화질 → 그 이하 최고 → 최고화질 fallback
            return (
                f"bestvideo[height={quality}]+bestaudio"
                f"/bestvideo[height<={quality}]+bestaudio"
                f"/bestvideo+bestaudio/best"
            )
        else:
            return "bestvideo+bestaudio/best"
    else:
        # ffmpeg 없음: 통합 포맷만 (vcodec≠none AND acodec≠none)
        if quality:
            return (
                f"best[height={quality}][vcodec!=none][acodec!=none]"
                f"/best[height<={quality}][vcodec!=none][acodec!=none]"
                f"/best[vcodec!=none][acodec!=none]/best"
            )
        else:
            return "best[vcodec!=none][acodec!=none]/best"


def run_download(job_id, url, mode, quality=None, sub_langs=None):
    progress_store[job_id] = {"status": "running", "output": [], "files": []}
    out_dir = DOWNLOAD_DIR / job_id
    out_dir.mkdir(exist_ok=True)

    def log(msg):
        progress_store[job_id]["output"].append(msg)
        print(f"[{job_id}] {msg}")

    try:
        out_tmpl = str(out_dir / "%(title)s.%(ext)s")
        base = ["--no-playlist", "-o", out_tmpl, "--ignore-errors", "--no-abort-on-error"]
        if FFMPEG:
            base += ["--ffmpeg-location", str(Path(FFMPEG).parent)]

        langs = sub_langs if sub_langs else "ko"
        has_ff = bool(FFMPEG)

        sub_opts = [
            "--write-subs", "--write-auto-subs",
            "--sub-langs", langs,
            "--sub-format", "srt/vtt/best",
            "--convert-subs", "srt",
        ]

        if mode == "video":
            fmt = make_video_fmt(quality, has_ff)
            if has_ff:
                cmd = ytdlp_cmd(base + ["-f", fmt, "--merge-output-format", "mp4", url])
            else:
                cmd = ytdlp_cmd(base + ["-f", fmt, url])

        elif mode == "audio":
            cmd = ytdlp_cmd(base + [
                "-f", "bestaudio/best",
                "-x", "--audio-format", "mp3", "--audio-quality", "0",
                url])

        elif mode == "subtitle":
            cmd = ytdlp_cmd(base + sub_opts + ["--skip-download", url])

        elif mode == "all":
            fmt = make_video_fmt(quality, has_ff)
            if has_ff:
                cmd = ytdlp_cmd(base + ["-f", fmt, "--merge-output-format", "mp4"]
                                + sub_opts + [url])
            else:
                cmd = ytdlp_cmd(base + ["-f", fmt] + sub_opts + [url])
        else:
            progress_store[job_id]["status"] = "error"; return

        log(f"⬇ {mode.upper()} 다운로드 시작")
        log(f"🔧 포맷: {' '.join(cmd[cmd.index('-f')+1:cmd.index('-f')+2]) if '-f' in cmd else '기본'}")
        log(f"🔧 ffmpeg: {'있음 (고화질 병합 가능)' if FFMPEG else '없음 (통합포맷만 가능)'}")
        enc = get_enc()
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding=enc, errors="replace",
            startupinfo=make_si()
        )
        for line in proc.stdout:
            line = line.rstrip()
            if line: log(line)
        proc.wait()

        downloaded = [f for f in out_dir.iterdir() if f.is_file()]

        if proc.returncode != 0 and not downloaded:
            progress_store[job_id]["status"] = "error"
            log("❌ 다운로드 실패 — 파일이 생성되지 않았습니다")
            return
        if proc.returncode != 0:
            log("⚠️ 일부 오류가 있었지만 파일은 저장되었습니다")

        # all 모드: ffmpeg로 MP3 추가 추출
        if mode == "all" and FFMPEG:
            mp4s = list(out_dir.glob("*.mp4"))
            if mp4s:
                mp3 = mp4s[0].with_suffix(".mp3")
                log("🎵 MP3 변환 중...")
                r = subprocess.run(
                    [FFMPEG,"-i",str(mp4s[0]),"-q:a","0","-map","a",str(mp3),"-y","-loglevel","error"],
                    startupinfo=make_si(), capture_output=True
                )
                if mp3.exists(): log("✅ MP3 변환 완료")
                else: log(f"⚠️ MP3 변환 실패 (ffmpeg 오류): {r.stderr.decode(errors='replace')}")

        files = [{"name": f.name, "size": human_size(f.stat().st_size), "path": f"/dl/{job_id}/{f.name}"}
                 for f in sorted(out_dir.iterdir()) if f.is_file()]
        progress_store[job_id]["files"] = files
        progress_store[job_id]["status"] = "done"
        log(f"✅ 완료! {len(files)}개 파일 저장됨")

    except Exception:
        progress_store[job_id]["status"] = "error"
        progress_store[job_id]["output"].append(traceback.format_exc())

# ── HTML ──────────────────────────────────────────────────────────────────
# ── HTML ──────────────────────────────────────────────────────────────────
HTML_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>YT Downloader</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:#0d0f14; --surface:#161922; --surface2:#1e2230; --surface3:#252a38;
  --border:#272c3a; --accent:#ff3a3a; --accent2:#ff6b35;
  --text:#e8eaf0; --muted:#6b7280; --success:#22c55e; --warn:#f59e0b;
  --font:-apple-system,'Malgun Gothic','맑은 고딕','Apple SD Gothic Neo',sans-serif;
  --mono:'Consolas','Courier New',monospace;
}
body{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:36px 16px 80px;}
header{text-align:center;margin-bottom:36px;}
.logo{display:inline-flex;align-items:center;gap:10px;margin-bottom:6px;}
.logo-icon{width:40px;height:40px;background:linear-gradient(135deg,var(--accent),var(--accent2));border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:20px;}
.logo h1{font-size:24px;font-weight:700;background:linear-gradient(90deg,#fff 60%,var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
header p{color:var(--muted);font-size:13px;}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px;width:100%;max-width:700px;margin-bottom:16px;}
.url-row{display:flex;gap:8px;}
input[type="text"]{flex:1;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-family:var(--mono);font-size:13px;padding:11px 14px;outline:none;transition:border-color .2s;}
input[type="text"]:focus{border-color:var(--accent);}
input[type="text"]::placeholder{color:var(--muted);}
.btn{background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-family:var(--font);font-size:13px;font-weight:600;padding:11px 18px;cursor:pointer;white-space:nowrap;transition:all .18s;}
.btn:hover:not(:disabled){background:var(--border);}
.btn:disabled{opacity:.45;cursor:not-allowed;}
.ffmpeg-banner{border-radius:8px;font-size:12px;padding:9px 14px;margin-bottom:14px;display:none;font-family:var(--mono);}
.ffmpeg-banner.ok{background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.2);color:#4ade80;}
.ffmpeg-banner.warn{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.3);color:#fbbf24;}
#video-info{display:none;margin-top:20px;padding-top:20px;border-top:1px solid var(--border);}
.thumb-row{display:flex;gap:14px;align-items:flex-start;margin-bottom:16px;}
.thumb-wrap{position:relative;flex-shrink:0;border-radius:8px;overflow:hidden;width:130px;aspect-ratio:16/9;background:var(--surface2);}
.thumb-wrap img{width:100%;height:100%;object-fit:cover;}
.play-badge{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);background:rgba(0,0,0,.6);border-radius:50%;width:30px;height:30px;display:flex;align-items:center;justify-content:center;font-size:13px;}
.video-meta h3{font-size:14px;font-weight:600;line-height:1.4;margin-bottom:6px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
.meta-tags{display:flex;flex-wrap:wrap;gap:5px;}
.meta-tag{background:var(--surface2);border:1px solid var(--border);border-radius:4px;font-size:11px;color:var(--muted);padding:2px 7px;}
.avail-panel{background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin-bottom:16px;}
.avail-title{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);margin-bottom:10px;}
.avail-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;}
@media(max-width:520px){.avail-grid{grid-template-columns:1fr 1fr;}}
.avail-col h4{font-size:11px;color:var(--muted);margin-bottom:6px;font-weight:600;}
.avail-tags{display:flex;flex-wrap:wrap;gap:4px;}
.tag-dash{background:rgba(34,197,94,.12);border:1px solid rgba(34,197,94,.3);border-radius:4px;font-size:11px;color:#4ade80;padding:2px 7px;font-family:var(--mono);}
.tag-progressive{background:rgba(96,165,250,.12);border:1px solid rgba(96,165,250,.3);border-radius:4px;font-size:11px;color:#93c5fd;padding:2px 7px;font-family:var(--mono);}
.tag-locked{background:rgba(107,114,128,.1);border:1px solid rgba(107,114,128,.2);border-radius:4px;font-size:11px;color:var(--muted);padding:2px 7px;font-family:var(--mono);text-decoration:line-through;}
.tag-audio{background:var(--surface3);border:1px solid var(--border);border-radius:4px;font-size:11px;color:var(--text);padding:2px 7px;font-family:var(--mono);}
.tag-sub{background:var(--surface3);border:1px solid var(--border);border-radius:4px;font-size:11px;color:var(--text);padding:2px 7px;}
.tag-none{font-size:11px;color:var(--muted);padding:2px 7px;font-style:italic;}
.tag-legend{font-size:10px;color:var(--muted);margin-top:8px;line-height:1.6;}
.section-label{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);margin-bottom:10px;margin-top:16px;}
.mode-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;}
@media(max-width:480px){.mode-grid{grid-template-columns:repeat(2,1fr);}}
.mode-btn{background:var(--surface2);border:2px solid var(--border);border-radius:10px;color:var(--muted);cursor:pointer;font-family:var(--font);font-size:13px;font-weight:600;padding:13px 8px;text-align:center;transition:all .18s;}
.mode-btn .icon{font-size:20px;display:block;margin-bottom:4px;}
.mode-btn:hover{border-color:var(--muted);color:var(--text);}
.mode-btn.active{border-color:var(--accent);color:var(--text);background:rgba(255,58,58,.08);}
.option-row{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}
.opt-btn{border-radius:6px;color:var(--muted);cursor:pointer;font-family:var(--mono);font-size:12px;padding:5px 12px;transition:all .15s;border:1px solid var(--border);background:var(--surface2);}
.opt-btn:hover:not(:disabled){border-color:var(--muted);color:var(--text);}
.opt-btn.active{border-color:var(--accent);color:var(--text);background:rgba(255,58,58,.1);}
.opt-btn.best{border-color:var(--accent2);color:var(--accent2);}
.opt-btn:disabled{opacity:.35;cursor:not-allowed;text-decoration:line-through;}
#quality-section,#sub-section{display:none;}
.no-ffmpeg-hint{background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);border-radius:6px;color:#fbbf24;font-size:11px;padding:7px 10px;margin-top:8px;display:none;}
.btn-download{width:100%;background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;border-radius:10px;color:#fff;cursor:pointer;font-family:var(--font);font-size:15px;font-weight:700;padding:14px;margin-top:16px;transition:opacity .2s;}
.btn-download:hover:not(:disabled){opacity:.88;}
.btn-download:disabled{opacity:.4;cursor:not-allowed;}
#progress-card{display:none;width:100%;max-width:700px;}
.progress-header{display:flex;align-items:center;gap:10px;margin-bottom:12px;}
.spinner{width:17px;height:17px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite;flex-shrink:0;}
@keyframes spin{to{transform:rotate(360deg);}}
.spinner.done{animation:none;border-color:var(--success);border-top-color:var(--success);}
.spinner.error{animation:none;border-color:#ef4444;border-top-color:#ef4444;}
.log-box{background:#0a0c10;border:1px solid var(--border);border-radius:8px;font-family:var(--mono);font-size:12px;line-height:1.6;max-height:220px;overflow-y:auto;padding:12px 14px;color:#9ca3af;white-space:pre-wrap;word-break:break-all;}
.log-box::-webkit-scrollbar{width:4px;}
.log-box::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
#files-section{display:none;margin-top:16px;padding-top:16px;border-top:1px solid var(--border);}
.file-item{display:flex;align-items:center;justify-content:space-between;background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:10px 14px;margin-bottom:7px;}
.file-name{font-family:var(--mono);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:420px;}
.file-right{display:flex;align-items:center;gap:10px;flex-shrink:0;}
.file-size{font-size:11px;color:var(--muted);}
.btn-dl{background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;border-radius:6px;color:#fff;cursor:pointer;font-size:12px;font-weight:600;padding:5px 12px;text-decoration:none;white-space:nowrap;}
.save-notice{background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.2);border-radius:7px;color:#4ade80;font-size:12px;padding:8px 12px;margin-top:10px;font-family:var(--mono);display:none;}
.error-msg{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);border-radius:8px;color:#f87171;font-size:13px;padding:10px 14px;margin-top:10px;display:none;}
#init-banner{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.3);border-radius:8px;color:#fbbf24;font-size:13px;padding:10px 14px;margin-bottom:14px;display:none;}
.after-dl-actions{display:none;margin-top:14px;padding-top:14px;border-top:1px solid var(--border);}
.after-dl-actions p{font-size:12px;color:var(--muted);margin-bottom:8px;}
.after-dl-row{display:flex;gap:8px;flex-wrap:wrap;}
.btn-sm{background:var(--surface2);border:1px solid var(--border);border-radius:7px;color:var(--text);cursor:pointer;font-size:12px;font-weight:600;padding:7px 14px;transition:all .15s;}
.btn-sm:hover{border-color:var(--muted);}
.btn-sm.accent{border-color:var(--accent);color:var(--accent);}
</style>
</head>
<body>
<header>
  <div class="logo"><div class="logo-icon">&#9654;</div><h1>YT Downloader</h1></div>
  <p>YouTube 영상을 다양한 형식으로 저장하세요</p>
</header>
<div style="width:100%;max-width:700px">
  <div id="init-banner">&#9203; yt-dlp 설치 중입니다. 잠시 후 다시 시도하세요...</div>
</div>
<div class="card">
  <div class="url-row">
    <input type="text" id="url-input" placeholder="https://www.youtube.com/watch?v=...">
    <button class="btn" id="fetch-btn" onclick="fetchInfo()">정보 가져오기</button>
  </div>
  <div id="error-msg" class="error-msg"></div>
  <div id="video-info">
    <div class="ffmpeg-banner" id="ffmpeg-banner"></div>
    <div class="thumb-row">
      <div class="thumb-wrap"><img id="thumb-img" src="" alt=""><div class="play-badge">&#9654;</div></div>
      <div class="video-meta">
        <h3 id="video-title"></h3>
        <div class="meta-tags">
          <span class="meta-tag" id="meta-channel"></span>
          <span class="meta-tag" id="meta-duration"></span>
          <span class="meta-tag" id="meta-views"></span>
        </div>
      </div>
    </div>
    <div class="avail-panel">
      <div class="avail-title">&#128203; 실제 다운로드 가능 항목</div>
      <div class="avail-grid">
        <div class="avail-col">
          <h4>&#127916; 영상 화질</h4>
          <div class="avail-tags" id="avail-video"></div>
          <div id="avail-legend" class="tag-legend"></div>
        </div>
        <div class="avail-col">
          <h4>&#127925; 음성 포맷</h4>
          <div class="avail-tags" id="avail-audio"></div>
        </div>
        <div class="avail-col">
          <h4>&#128172; 자막 언어</h4>
          <div class="avail-tags" id="avail-sub"></div>
        </div>
      </div>
    </div>
    <div class="section-label">다운로드 형식 선택</div>
    <div class="mode-grid">
      <button class="mode-btn active" data-mode="video" onclick="selectMode(this)"><span class="icon">&#127916;</span>영상</button>
      <button class="mode-btn" data-mode="audio" onclick="selectMode(this)"><span class="icon">&#127925;</span>음성 (MP3)</button>
      <button class="mode-btn" data-mode="subtitle" onclick="selectMode(this)"><span class="icon">&#128172;</span>자막 스크립트</button>
      <button class="mode-btn" data-mode="all" onclick="selectMode(this)"><span class="icon">&#128230;</span>모두</button>
    </div>
    <div id="quality-section">
      <div class="section-label" style="margin-top:12px;">화질 선택</div>
      <div class="option-row" id="quality-row"></div>
      <div class="no-ffmpeg-hint" id="no-ffmpeg-hint">
        &#9888; ffmpeg 없음 — 취소선 화질은 DASH 스트림으로 선택 불가.
        ffmpeg를 설치하면 고화질을 이용할 수 있습니다.
      </div>
    </div>
    <div id="sub-section">
      <div class="section-label" style="margin-top:12px;">자막 언어 선택
        <span style="font-weight:400;color:var(--muted);text-transform:none;letter-spacing:0;font-size:11px;margin-left:6px;">(복수 선택 가능)</span>
      </div>
      <div class="option-row" id="sub-row"></div>
    </div>
    <button class="btn-download" id="dl-btn" onclick="startDownload()">&#11015; 다운로드 시작</button>
  </div>
</div>
<div class="card" id="progress-card">
  <div class="progress-header">
    <div class="spinner" id="spinner"></div>
    <span id="progress-label" style="font-weight:600;font-size:15px;">다운로드 중...</span>
  </div>
  <div class="log-box" id="log-box"></div>
  <div id="save-notice" class="save-notice"></div>
  <div id="files-section">
    <div class="section-label" style="margin-top:0">다운로드 완료 파일</div>
    <div id="files-list"></div>
  </div>
  <div class="after-dl-actions" id="after-dl-actions">
    <p>&#10003; 다운로드 완료 — 추가 작업을 선택하세요</p>
    <div class="after-dl-row">
      <button class="btn-sm accent" onclick="dlAnotherMode()">&#128260; 같은 영상 다른 형식으로</button>
      <button class="btn-sm" onclick="dlNewUrl()">&#128195; 다른 영상 다운로드</button>
    </div>
  </div>
</div>
<script>
let currentMode='video',selectedQuality=null,selectedSubs=new Set(['ko']);
let hasFfmpeg=false,heightData=[],availSubs=[],pollTimer=null;

document.getElementById('url-input').addEventListener('keydown',e=>{if(e.key==='Enter')fetchInfo();});

(async()=>{
  try{
    const r=await fetch('/ready');const d=await r.json();
    if(!d.ready){
      document.getElementById('init-banner').style.display='block';
      document.getElementById('fetch-btn').disabled=true;
      const t=setInterval(async()=>{
        const r2=await fetch('/ready');const d2=await r2.json();
        if(d2.ready){clearInterval(t);document.getElementById('init-banner').style.display='none';document.getElementById('fetch-btn').disabled=false;}
      },3000);
    }
  }catch(e){}
})();

async function fetchInfo(){
  const url=document.getElementById('url-input').value.trim();
  if(!url)return showError('YouTube URL을 입력해주세요.');
  hideError();document.getElementById('video-info').style.display='none';
  const btn=document.getElementById('fetch-btn');
  btn.textContent='조회 중...';btn.disabled=true;
  try{
    const res=await fetch('/info',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
    const data=await res.json();
    if(!res.ok||data.error){showError(data.error||'오류 발생');return;}
    hasFfmpeg=data.has_ffmpeg;heightData=data.heights||[];availSubs=data.subtitles||[];
    document.getElementById('thumb-img').src=data.thumbnail;
    document.getElementById('video-title').textContent=data.title;
    document.getElementById('meta-channel').textContent='📺 '+data.channel;
    document.getElementById('meta-duration').textContent='⏱ '+data.duration;
    document.getElementById('meta-views').textContent='👁 '+data.view_count+'회';
    // ffmpeg 배너
    const banner=document.getElementById('ffmpeg-banner');
    if(hasFfmpeg){banner.className='ffmpeg-banner ok';banner.textContent='✅ ffmpeg 감지됨 — DASH 고화질 병합 다운로드 가능';}
    else{banner.className='ffmpeg-banner warn';banner.textContent='⚠️ ffmpeg 없음 — 통합 포맷(최대 '+(data.progressive_max||'720')+'p)만 가능. 고화질은 ffmpeg 설치 필요.';}
    banner.style.display='block';
    renderVideoTags(heightData,hasFfmpeg);
    renderAudioTags(data.audio_formats||[]);
    renderSubTags(availSubs);
    buildQualityButtons();buildSubButtons();
    selectMode(document.querySelector('.mode-btn[data-mode="video"]'));
    document.getElementById('video-info').style.display='block';
  }catch(e){showError('오류: '+e.message);}
  finally{btn.textContent='정보 가져오기';btn.disabled=false;}
}

function renderVideoTags(heights,ffmpeg){
  const el=document.getElementById('avail-video'),leg=document.getElementById('avail-legend');
  el.innerHTML='';leg.innerHTML='';
  if(!heights.length){el.innerHTML='<span class="tag-none">정보 없음</span>';return;}
  heights.forEach(h=>{
    const span=document.createElement('span');
    if(!ffmpeg&&h.needs_ffmpeg){span.className='tag-locked';span.title='ffmpeg 없이 불가 (DASH 스트림)';}
    else if(h.needs_ffmpeg){span.className='tag-dash';span.title='DASH — ffmpeg 병합 (고화질)';}
    else{span.className='tag-progressive';span.title='통합포맷 — ffmpeg 없이 가능';}
    if(h.tbr)span.title+=' / '+h.tbr+'kbps';
    span.textContent=h.height+'p';el.appendChild(span);
  });
  const hasDash=heights.some(h=>h.needs_ffmpeg),hasProg=heights.some(h=>!h.needs_ffmpeg);
  let lg='';
  if(hasDash&&ffmpeg)lg+='🟢 DASH(ffmpeg 병합)  ';
  if(hasDash&&!ffmpeg)lg+='⬜ 취소선=ffmpeg 필요  ';
  if(hasProg)lg+='🔵 통합포맷(바로 다운)';
  leg.textContent=lg;
}
function renderAudioTags(fmts){
  const el=document.getElementById('avail-audio');el.innerHTML='';
  if(!fmts.length){el.innerHTML='<span class="tag-none">없음</span>';return;}
  fmts.forEach(f=>{const s=document.createElement('span');s.className='tag-audio';s.textContent=f;el.appendChild(s);});
}
function renderSubTags(subs){
  const el=document.getElementById('avail-sub');el.innerHTML='';
  if(!subs.length){el.innerHTML='<span class="tag-none">없음</span>';return;}
  subs.forEach(s=>{const span=document.createElement('span');span.className='tag-sub';span.textContent=s.name+(s.auto?' (자동)':'');el.appendChild(span);});
}

function buildQualityButtons(){
  const row=document.getElementById('quality-row');row.innerHTML='';selectedQuality=null;
  const best=document.createElement('button');
  best.className='opt-btn best active';best.textContent='최고 화질';best.dataset.val='';
  best.onclick=()=>selectQuality(best);row.appendChild(best);
  heightData.forEach(h=>{
    const b=document.createElement('button');
    b.className='opt-btn';b.dataset.val=String(h.height);
    const disabled=!hasFfmpeg&&h.needs_ffmpeg;
    b.textContent=h.height+'p';
    if(disabled){b.disabled=true;b.title='ffmpeg 없이는 선택 불가 (DASH 스트림)';}
    else{b.onclick=()=>selectQuality(b);}
    row.appendChild(b);
  });
  const hint=document.getElementById('no-ffmpeg-hint');
  hint.style.display=(!hasFfmpeg&&heightData.some(h=>h.needs_ffmpeg))?'block':'none';
}
function selectQuality(btn){
  document.querySelectorAll('#quality-row .opt-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');selectedQuality=btn.dataset.val||null;
}
function buildSubButtons(){
  const row=document.getElementById('sub-row');row.innerHTML='';selectedSubs=new Set();
  const list=availSubs.length>0?availSubs:[{code:'ko',name:'한국어',auto:false},{code:'en',name:'영어',auto:false}];
  list.forEach(s=>{
    const b=document.createElement('button');const isKo=s.code==='ko';
    b.className='opt-btn'+(isKo?' active':'');
    b.textContent=s.name+' ('+s.code+')'+(s.auto?' 자동':'');b.dataset.val=s.code;
    if(isKo)selectedSubs.add(s.code);
    b.onclick=()=>toggleSub(b,s.code);row.appendChild(b);
  });
}
function toggleSub(btn,code){
  if(selectedSubs.has(code)){if(selectedSubs.size===1)return;selectedSubs.delete(code);btn.classList.remove('active');}
  else{selectedSubs.add(code);btn.classList.add('active');}
}
function selectMode(btn){
  document.querySelectorAll('.mode-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');currentMode=btn.dataset.mode;
  document.getElementById('quality-section').style.display=(currentMode==='video'||currentMode==='all')?'block':'none';
  document.getElementById('sub-section').style.display=(currentMode==='subtitle'||currentMode==='all')?'block':'none';
}
async function startDownload(){
  const url=document.getElementById('url-input').value.trim();if(!url)return;
  document.getElementById('dl-btn').disabled=true;
  document.getElementById('progress-card').style.display='block';
  document.getElementById('log-box').textContent='';
  document.getElementById('files-section').style.display='none';
  document.getElementById('files-list').innerHTML='';
  document.getElementById('after-dl-actions').style.display='none';
  document.getElementById('save-notice').style.display='none';
  document.getElementById('spinner').className='spinner';
  document.getElementById('progress-label').textContent='다운로드 중...';
  const payload={url,mode:currentMode,sub_langs:[...selectedSubs].join(',')};
  if((currentMode==='video'||currentMode==='all')&&selectedQuality)payload.quality=selectedQuality;
  const res=await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const {job_id}=await res.json();
  if(pollTimer)clearInterval(pollTimer);
  pollTimer=setInterval(async()=>{
    const r=await fetch('/status/'+job_id);const d=await r.json();
    const box=document.getElementById('log-box');
    box.textContent=d.output.join('\\n');box.scrollTop=box.scrollHeight;
    if(d.status==='done'){
      clearInterval(pollTimer);
      document.getElementById('spinner').className='spinner done';
      document.getElementById('progress-label').textContent='✅ 다운로드 완료';
      showFiles(d.files);document.getElementById('dl-btn').disabled=false;
      document.getElementById('after-dl-actions').style.display='block';
    }else if(d.status==='error'){
      clearInterval(pollTimer);
      document.getElementById('spinner').className='spinner error';
      document.getElementById('progress-label').textContent='❌ 다운로드 실패';
      document.getElementById('dl-btn').disabled=false;
    }
  },1000);
}
function showFiles(files){
  if(!files||!files.length)return;
  document.getElementById('save-notice').textContent='📁 저장 위치: YT_Downloads 폴더 (프로그램 실행 폴더)';
  document.getElementById('save-notice').style.display='block';
  const list=document.getElementById('files-list');list.innerHTML='';
  files.forEach(f=>{
    const ext=f.name.split('.').pop().toLowerCase();
    const icon=ext==='mp4'?'🎬':ext==='mp3'?'🎵':(ext==='srt'||ext==='vtt')?'💬':'📄';
    list.innerHTML+='<div class="file-item"><span class="file-name">'+icon+' '+f.name+'</span>'+
      '<div class="file-right"><span class="file-size">'+f.size+'</span>'+
      '<a class="btn-dl" href="'+f.path+'" download="'+f.name+'">저장</a></div></div>';
  });
  document.getElementById('files-section').style.display='block';
}
function dlAnotherMode(){
  document.getElementById('progress-card').style.display='none';
  document.getElementById('after-dl-actions').style.display='none';
  selectMode(document.querySelector('.mode-btn[data-mode="video"]'));
  window.scrollTo({top:0,behavior:'smooth'});
}
function dlNewUrl(){
  document.getElementById('url-input').value='';
  document.getElementById('video-info').style.display='none';
  document.getElementById('progress-card').style.display='none';
  hideError();window.scrollTo({top:0,behavior:'smooth'});
}
function showError(msg){const e=document.getElementById('error-msg');e.textContent=msg;e.style.display='block';}
function hideError(){document.getElementById('error-msg').style.display='none';}
</script>
</body>
</html>"""


# ── HTTP 핸들러 ───────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n)) if n else {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/":
            self.send_html(HTML_PAGE)
        elif p == "/ready":
            self.send_json({"ready": True, "ytdlp": YTDLP})
        elif p.startswith("/status/"):
            job_id = p.split("/")[-1]
            self.send_json(progress_store.get(job_id,
                {"status": "not_found", "output": [], "files": []}))
        elif p.startswith("/dl/"):
            parts = p.split("/")
            if len(parts) >= 4:
                fp = DOWNLOAD_DIR / parts[2] / "/".join(parts[3:])
                if fp.exists():
                    data = fp.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition",
                        f'attachment; filename="{fp.name}"')
                    self.send_header("Content-Length", len(data))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_json({"error": "파일 없음"}, 404)
            else:
                self.send_json({"error": "잘못된 경로"}, 400)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        p = urlparse(self.path).path

        if p == "/info":
            data = self.read_json()
            url = data.get("url", "").strip()
            if not url:
                self.send_json({"error": "URL을 입력하세요"}, 400); return
            try:
                enc = get_enc()
                cmd = ytdlp_cmd(["--dump-json", "--no-playlist", url])
                r = subprocess.run(cmd, capture_output=True, timeout=30,
                                   encoding=enc, errors="replace",
                                   startupinfo=make_si())
                if r.returncode != 0:
                    err = (r.stderr or "").strip().split("\n")[-1] or "영상 정보를 가져올 수 없습니다."
                    self.send_json({"error": err}, 400); return

                info = json.loads(r.stdout)
                fmts = info.get("formats", [])
                has_ff = bool(FFMPEG)

                # ── 영상 화질 ────────────────────────────────────────────────
                # ffmpeg 있음: DASH video-only 포맷 화질 (실제 병합 가능)
                # ffmpeg 없음: 영상+음성 통합(progressive) 포맷 화질만
                video_formats = []  # {"height": int, "ext": str, "vcodec": str, "tbr": float}

                if has_ff:
                    # DASH video-only: vcodec 있고 acodec이 none
                    for f in fmts:
                        h = f.get("height")
                        vc = f.get("vcodec", "none") or "none"
                        ac = f.get("acodec", "none") or "none"
                        if h and vc != "none" and ac == "none":
                            video_formats.append({
                                "height": h,
                                "ext": f.get("ext", "?"),
                                "vcodec": vc.split(".")[0],
                                "tbr": f.get("tbr") or f.get("vbr") or 0,
                                "format_id": f.get("format_id", ""),
                            })
                else:
                    # 통합 포맷: vcodec AND acodec 모두 있는 것
                    for f in fmts:
                        h = f.get("height")
                        vc = f.get("vcodec", "none") or "none"
                        ac = f.get("acodec", "none") or "none"
                        if h and vc != "none" and ac != "none":
                            video_formats.append({
                                "height": h,
                                "ext": f.get("ext", "?"),
                                "vcodec": vc.split(".")[0],
                                "tbr": f.get("tbr") or 0,
                                "format_id": f.get("format_id", ""),
                            })

                # height별로 최고 tbr 포맷만 남기기
                best_by_height = {}
                for vf in video_formats:
                    h = vf["height"]
                    if h not in best_by_height or vf["tbr"] > best_by_height[h]["tbr"]:
                        best_by_height[h] = vf

                # 화질 목록: 내림차순 정렬, ffmpeg 필요 여부 포함
                height_list = []
                for h in sorted(best_by_height.keys(), reverse=True):
                    vf = best_by_height[h]
                    height_list.append({
                        "height": h,
                        "ext": vf["ext"],
                        "codec": vf["vcodec"],
                        "needs_ffmpeg": has_ff,  # DASH면 ffmpeg로 병합한 것
                        "tbr": round(vf["tbr"]) if vf["tbr"] else None,
                    })

                # 통합 포맷 최대 화질 (ffmpeg 없을 때 상한선 안내용)
                progressive_max = 0
                for f in fmts:
                    h = f.get("height") or 0
                    vc = f.get("vcodec", "none") or "none"
                    ac = f.get("acodec", "none") or "none"
                    if h and vc != "none" and ac != "none":
                        progressive_max = max(progressive_max, h)

                # ── 음성 포맷 ────────────────────────────────────────────────
                # audio-only 포맷 (vcodec=none, acodec 있음)
                audio_seen = {}  # ext → best abr
                for f in fmts:
                    vc = f.get("vcodec", "none") or "none"
                    ac = f.get("acodec", "none") or "none"
                    if vc == "none" and ac != "none":
                        ext = f.get("ext", "?").upper()
                        abr = f.get("abr") or f.get("tbr") or 0
                        if ext not in audio_seen or abr > audio_seen[ext]:
                            audio_seen[ext] = abr
                audio_fmts = [
                    f"{ext} {int(abr)}kbps" if abr else ext
                    for ext, abr in sorted(audio_seen.items())
                ]
                if not audio_fmts:
                    # 통합 포맷에서 오디오 추출 가능 여부
                    for f in fmts:
                        ac = f.get("acodec", "none") or "none"
                        if ac != "none":
                            audio_fmts = ["MP3 (변환 후 저장)"]
                            break

                # ── 자막 ─────────────────────────────────────────────────────
                subs_raw = info.get("subtitles", {})
                auto_raw = info.get("automatic_captions", {})
                sub_list = []
                seen_codes = set()
                lang_names = {
                    "ko": "한국어", "en": "영어", "ja": "일본어",
                    "zh": "중국어", "zh-Hans": "중국어(간체)", "zh-Hant": "중국어(번체)",
                    "es": "스페인어", "fr": "프랑스어", "de": "독일어",
                }
                # 수동 자막 먼저
                for code in subs_raw:
                    base_code = code.split("-")[0]
                    if base_code in seen_codes: continue
                    seen_codes.add(base_code)
                    sub_list.append({"code": base_code,
                                     "name": lang_names.get(base_code, code),
                                     "auto": False})
                # 자동 자막
                for code in auto_raw:
                    base_code = code.split("-")[0]
                    if base_code in seen_codes: continue
                    seen_codes.add(base_code)
                    sub_list.append({"code": base_code,
                                     "name": lang_names.get(base_code, code),
                                     "auto": True})
                sub_list.sort(key=lambda x: (
                    0 if x["code"] == "ko" else 1 if x["code"] == "en" else 2,
                    x["code"]
                ))

                self.send_json({
                    "title": info.get("title", ""),
                    "thumbnail": info.get("thumbnail", ""),
                    "duration": fmt_dur(info.get("duration", 0)),
                    "channel": info.get("channel", info.get("uploader", "")),
                    "view_count": f"{info.get('view_count', 0):,}",
                    "has_ffmpeg": has_ff,
                    "progressive_max": progressive_max,
                    "heights": height_list[:8],           # 상세 객체 배열
                    "audio_formats": audio_fmts[:6],
                    "subtitles": sub_list[:12],
                })
            except subprocess.TimeoutExpired:
                self.send_json({"error": "시간 초과"}, 408)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif p == "/start":
            data = self.read_json()
            url = data.get("url", "").strip()
            if not url:
                self.send_json({"error": "URL 없음"}, 400); return
            job_id = str(uuid.uuid4())[:8]
            threading.Thread(
                target=run_download,
                args=(job_id, url, data.get("mode", "video"),
                      data.get("quality"), data.get("sub_langs")),
                daemon=True
            ).start()
            self.send_json({"job_id": job_id})

        else:
            self.send_json({"error": "not found"}, 404)

# ── 중복 실행 방지 ────────────────────────────────────────────────────────
LOCK_FILE = EXE_DIR / "ytdl.lock"
FIXED_PORT = 17860

def is_already_running() -> bool:
    """락파일로 이미 실행 중인지 확인"""
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            # 해당 PID 프로세스가 실제로 살아있는지 확인
            if IS_WIN:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True, text=True, startupinfo=make_si()
                )
                if str(pid) in result.stdout:
                    return True
            else:
                os.kill(pid, 0)  # 시그널 0 = 존재 확인만
                return True
        except (ValueError, OSError, ProcessLookupError):
            pass
        # 죽은 프로세스의 락파일 → 삭제
        try: LOCK_FILE.unlink()
        except: pass
    return False

def write_lock():
    try: LOCK_FILE.write_text(str(os.getpid()))
    except: pass

def remove_lock():
    try: LOCK_FILE.unlink()
    except: pass

# ── 메인 ─────────────────────────────────────────────────────────────────
def main():
    # 이미 실행 중이면 브라우저만 열고 종료
    if is_already_running():
        url = f"http://127.0.0.1:{FIXED_PORT}"
        print(f"[YT Downloader] 이미 실행 중 → 브라우저 열기: {url}")
        webbrowser.open(url)
        return

    PORT = FIXED_PORT
    server = None
    for port in range(PORT, PORT + 10):
        try:
            server = HTTPServer(("127.0.0.1", port), Handler)
            PORT = port; break
        except OSError:
            continue

    if server is None:
        print("[YT Downloader] 포트를 열 수 없습니다.")
        return

    write_lock()

    url = f"http://127.0.0.1:{PORT}"
    print(f"[YT Downloader] 접속: {url}")
    print(f"[YT Downloader] 저장: {DOWNLOAD_DIR}")
    print("[YT Downloader] 종료하려면 이 창을 닫으세요.")

    # 브라우저: 이미 열려있으면 새 탭 안 열리도록 체크
    def open_browser_once():
        time.sleep(1.2)
        # 서버가 응답하는지 먼저 확인
        try:
            import urllib.request as ur
            ur.urlopen(f"http://127.0.0.1:{PORT}/ready", timeout=2)
        except:
            pass
        webbrowser.open(url)

    threading.Thread(target=open_browser_once, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[YT Downloader] 종료")
    finally:
        remove_lock()
        server.shutdown()

if __name__ == "__main__":
    main()
