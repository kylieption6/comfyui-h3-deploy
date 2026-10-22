#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GH_PROXY = "https://ghfast.top/"
GH_PROXY_FALLBACKS = [
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "",
]
PIP_INDEXES = [
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.tuna.tsinghua.edu.cn/simple",
]
HF_MIRROR = "https://hf-mirror.com"
CLOUDFLARED_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"

COMFY_REPO = "https://github.com/comfyanonymous/ComfyUI.git"

MODELS = [
    {
        "repo": "smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models",
        "path": "minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors",
        "dest": "diffusion_models",
        "size": 20970379632,
        "sha256": "a629cfea8d89a071b140c6e1935dc9a23e72de6badc18975a2bb9e6d1423d76d",
    },
    {
        "repo": "Comfy-Org/MiniMax-H3",
        "path": "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
        "dest": "text_encoders",
        "size": 27141342152,
        "sha256": None,
    },
    {
        "repo": "Comfy-Org/MiniMax-H3",
        "path": "vae/minimax_h3_video_vae_int8_convrot.safetensors",
        "dest": "vae",
        "size": 2811065184,
        "sha256": None,
    },
    {
        "repo": "Comfy-Org/MiniMax-H3",
        "path": "vae/minimax_h3_audio_vae_fp32.safetensors",
        "dest": "vae",
        "size": 605254808,
        "sha256": None,
    },
    {
        "repo": "lightx2v/Minimax-h3-Turbo",
        "path": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
        "dest": "loras",
        "size": 1956192992,
        "sha256": None,
    },
    {
        "repo": "lightx2v/Minimax-h3-Turbo",
        "path": "minimax_h3_ref2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
        "dest": "loras",
        "size": 1956193000,
        "sha256": None,
    },
]

UNET_NAME = "minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors"
CLIP_NAME = "qwen3vl_32b_minimax_h3_int8_convrot.safetensors"
VIDEO_VAE_NAME = "minimax_h3_video_vae_int8_convrot.safetensors"
AUDIO_VAE_NAME = "minimax_h3_audio_vae_fp32.safetensors"
LORA_FL2V = "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
LORA_REF2V = "minimax_h3_ref2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"


def log(msg):
    print(f"[deploy] {msg}", flush=True)


def run(cmd, check=True, env=None, quiet=False):
    printable = cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)
    if not quiet:
        log(f"$ {printable}")
    kwargs = {"shell": isinstance(cmd, str), "env": env}
    if quiet:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    proc = subprocess.run(cmd, **kwargs)
    if check and proc.returncode != 0:
        raise RuntimeError(f"命令失败 ({proc.returncode}): {printable}")
    return proc.returncode


def which(name):
    return shutil.which(name)


def is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


def apt_install(pkgs):
    missing = [p for p in pkgs if not which(p)]
    if not missing:
        return
    if not is_root():
        log(f"缺少 {missing} 且非 root，跳过 apt 安装，将使用替代方案")
        return
    log(f"安装系统依赖: {missing}")
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    if run("apt-get update -qq", check=False, env=env, quiet=True) != 0:
        log("apt-get update 失败，继续尝试安装")
    run(["apt-get", "install", "-y", "-qq"] + missing, check=False, env=env, quiet=True)


def http_json(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def http_post(url, payload, timeout=30):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def git_clone(repo, dest):
    for proxy in GH_PROXY_FALLBACKS:
        url = proxy + repo
        log(f"克隆 ComfyUI: {url}")
        rc = run(["git", "clone", "--depth", "1", url, str(dest)], check=False)
        if rc == 0:
            return
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
    raise RuntimeError("ComfyUI 克隆失败，请检查网络或镜像")


def pip_install(uv, python, args, extra_env=None):
    last = None
    for index in PIP_INDEXES:
        cmd = [str(uv), "pip", "install", "--python", str(python), "--index-url", index] + args
        try:
            run(cmd)
            return
        except RuntimeError as exc:
            last = exc
            log(f"镜像 {index} 安装失败，尝试下一个")
    raise last


def ensure_comfy(root: Path, skip_clone: bool):
    comfy = root / "ComfyUI"
    if (comfy / "main.py").exists():
        log(f"检测到已有 ComfyUI: {comfy}")
    elif skip_clone:
        raise RuntimeError(f"{comfy} 不存在且指定了 --skip-clone")
    else:
        git_clone(COMFY_REPO, comfy)
    return comfy


def ensure_python_env(comfy: Path, skip: bool):
    venv = comfy / "venv"
    py = venv / "bin" / "python"
    if not py.exists():
        if skip:
            raise RuntimeError("虚拟环境不存在且指定了 --skip-install")
        log("创建 Python 虚拟环境")
        run([sys.executable, "-m", "venv", str(venv)])
    pipe = venv / "bin" / "pip"
    if skip and (comfy / "venv" / "bin" / "uv").exists():
        pass
    else:
        run([str(pipe), "install", "-q", "--upgrade", "pip", "-i", PIP_INDEXES[1]], quiet=True)
        run([str(pipe), "install", "-q", "uv", "-i", PIP_INDEXES[1]], quiet=True)
    uv = venv / "bin" / "uv"
    import_check = run(
        [str(py), "-c", "import torch, torchvision, transformers, safetensors"],
        check=False,
        quiet=True,
    )
    if import_check != 0:
        if skip:
            raise RuntimeError("Python 依赖未安装且指定了 --skip-install")
        log("安装 PyTorch (CUDA)")
        pip_install(uv, py, ["torch", "torchvision", "torchaudio"])
        log("安装 ComfyUI 依赖")
        pip_install(uv, py, ["-r", str(comfy / "requirements.txt")])
    return py


def cuda_report(py: Path):
    code = (
        "import torch;"
        "print('CUDA', torch.cuda.is_available(), torch.__version__);"
        "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no gpu')"
    )
    proc = subprocess.run([str(py), "-c", code], capture_output=True, text=True)
    out = (proc.stdout + proc.stderr).strip()
    log("GPU 检查: " + out.replace("\n", " | "))
    if "CUDA False" in proc.stdout:
        log("警告: PyTorch 未检测到 CUDA，视频生成会非常慢")


def file_ready(path: Path, expected: int, verify_header=True):
    if not path.exists() or path.stat().st_size != expected:
        return False
    if verify_header:
        try:
            with open(path, "rb") as fh:
                n = struct.unpack("<Q", fh.read(8))[0]
                if n <= 0 or n > 200 * 1024 * 1024:
                    return False
                json.loads(fh.read(n))
        except Exception:
            return False
    return True


def download_url(url, dest: Path):
    tmp = dest.with_suffix(dest.suffix + ".part")
    pos = tmp.stat().st_size if tmp.exists() else 0
    headers = {"Range": f"bytes={pos}-"} if pos else {}
    req = urllib.request.Request(url, headers=headers)
    mode = "ab" if pos else "wb"
    log(f"下载(fallback): {dest.name} 从 {pos} 字节继续")
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, mode) as fh:
        while True:
            chunk = r.read(8 * 1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
    tmp.replace(dest)


def download_model(item, models_dir: Path, hf_mirror):
    name = Path(item["path"]).name
    dest = models_dir / item["dest"] / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if file_ready(dest, item["size"]):
        log(f"已存在: {item['dest']}/{name}")
        return dest
    url = f"{hf_mirror}/{item['repo']}/resolve/main/{item['path']}"
    log(f"下载 {item['dest']}/{name} ({item['size'] / 1e9:.1f} GB)")
    if which("aria2c"):
        cmd = [
            "aria2c", "-c", "-x16", "-s16", "-k1M", "--file-allocation=none",
            "--console-log-level=warn", "--summary-interval=0", "--max-tries=5",
            "--retry-wait=5", "--auto-file-renaming=false", "-d", str(dest.parent),
            "-o", dest.name, url,
        ]
        run(cmd, check=False)
    if not file_ready(dest, item["size"]):
        try:
            download_url(url, dest)
        except Exception as exc:
            log(f"aria2c 与直连下载均未完成: {exc}")
    if not file_ready(dest, item["size"]):
        raise RuntimeError(f"文件校验失败: {dest}")
    return dest


def ensure_models(models_dir: Path, hf_mirror, skip=False):
    if skip:
        log("跳过模型下载（--no-models）")
        return
    for item in MODELS:
        download_model(item, models_dir, hf_mirror)


def server_ready(port):
    try:
        http_json(f"http://127.0.0.1:{port}/system_stats", timeout=3)
        return True
    except Exception:
        return False


def ensure_server(comfy: Path, py: Path, port: int, wait=600):
    if server_ready(port):
        log(f"ComfyUI 已在 {port} 端口运行，直接复用")
        return
    log_path = comfy / "comfyui.log"
    log(f"启动 ComfyUI，日志: {log_path}")
    with open(log_path, "ab") as fh:
        subprocess.Popen(
            [str(py), "main.py", "--listen", "127.0.0.1", "--port", str(port)],
            cwd=str(comfy), stdout=fh, stderr=fh, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    start = time.time()
    while time.time() - start < wait:
        if server_ready(port):
            log("ComfyUI 已就绪")
            return
        time.sleep(3)
    raise RuntimeError(f"ComfyUI 启动超时，请查看 {log_path}")


def ensure_cloudflared(root: Path):
    binary = root / "cloudflared"
    if binary.exists() and os.access(binary, os.X_OK):
        return binary
    log("下载 cloudflared")
    for prefix in GH_PROXY_FALLBACKS:
        if run(["curl", "-fL", "--max-time", "180", "-o", str(binary), prefix + CLOUDFLARED_URL], check=False, quiet=True) == 0:
            binary.chmod(0o755)
            return binary
    raise RuntimeError("cloudflared 下载失败")


def ensure_tunnel(root: Path, port: int, enabled=True):
    if not enabled:
        log("跳过 Cloudflare 隧道（--no-tunnel）")
        return None
    log_path = root / "cloudflared.log"
    if log_path.exists():
        text = log_path.read_text(errors="ignore")
        m = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", text)
        alive = subprocess.run(["pgrep", "-f", "cloudflared tunnel"], capture_output=True, text=True)
        if m and alive.stdout.strip():
            log(f"复用已有隧道: {m[-1]}")
            return m[-1]
    binary = ensure_cloudflared(root)
    with open(log_path, "w") as fh:
        subprocess.Popen(
            [str(binary), "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
            stdout=fh, stderr=fh, stdin=subprocess.DEVNULL, start_new_session=True,
        )
    start = time.time()
    while time.time() - start < 90:
        if log_path.exists():
            m = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", log_path.read_text(errors="ignore"))
            if m:
                log(f"公网地址: {m[-1]}")
                return m[-1]
        time.sleep(2)
    log("未获取到隧道地址，继续本地运行")
    return None


def snap_frames(seconds):
    n = max(5, round(seconds * 24))
    return n + (5 - n % 17) % 17


def file_bytes_digest(data: bytes):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def copy_input(comfy: Path, src: Path, prefix):
    src = Path(src).expanduser()
    if not src.exists():
        raise RuntimeError(f"输入图片不存在: {src}")
    inp = comfy / "input"
    inp.mkdir(parents=True, exist_ok=True)
    data = src.read_bytes()
    target = inp / src.name
    if target.exists() and file_bytes_digest(target.read_bytes()) != file_bytes_digest(data):
        target = inp / f"{src.stem}_{prefix}{src.suffix}"
    if target.exists() and file_bytes_digest(target.read_bytes()) == file_bytes_digest(data):
        log(f"输入图片已在 input 目录: {target.name}")
        return target.name
    target.write_bytes(data)
    log(f"输入图片已复制到 input: {target.name}")
    return target.name


def ensure_workflows(comfy: Path, script_dir: Path, enabled=True):
    if not enabled:
        log("跳过工作流安装（--no-workflows）")
        return
    src = script_dir / "workflows"
    if not src.is_dir():
        log(f"未找到工作流目录 {src}，跳过")
        return
    dst = comfy / "user" / "default" / "workflows"
    dst.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.glob("*.json")):
        data = item.read_bytes()
        target = dst / item.name
        if target.exists() and target.read_bytes() == data:
            log(f"工作流已存在: {item.name}")
            continue
        target.write_bytes(data)
        log(f"已安装工作流: {item.name}")


def build_graph(prompt, width, height, length, seed, mode, images):
    lora = LORA_REF2V if mode == "ref" else LORA_FL2V
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_NAME, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP_NAME, "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE_NAME}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE_NAME}},
        "5": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": lora, "strength_model": 1.0}},
        "6": {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["5", 0], "shift_video": 12.0, "shift_audio": 3.0}},
        "8": {"class_type": "BasicScheduler", "inputs": {"model": ["6", 0], "scheduler": "simple", "steps": 4, "denoise": 1.0}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["11", 0], "vae": ["4", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0], "audio": ["13", 0], "fps": 24}},
        "15": {"class_type": "SaveVideo", "inputs": {"video": ["14", 0], "filename_prefix": "video/MiniMax_H3/deploy", "format": "auto", "codec": "auto"}},
    }
    if mode == "ref":
        graph["7"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0], "prompt": prompt,
            "width": width, "height": height, "length": length, "ref_image_size": "match"}}
        for i, image in enumerate(images):
            node_id = str(20 + i)
            graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": image}}
            graph["7"]["inputs"][f"ref_images.ref_image_{i}"] = [node_id, 0]
        cond = ["7", 0]
        latent = ["7", 1]
    else:
        inputs = {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt,
                  "width": width, "height": height, "length": length}
        if mode == "i2v":
            graph["7"] = {"class_type": "LoadImage", "inputs": {"image": images[0]}}
            inputs["first_frame"] = ["7", 0]
        graph["16"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": inputs}
        cond = ["16", 0]
        latent = ["16", 1]
    graph["17"] = {"class_type": "BasicGuider", "inputs": {"model": ["6", 0], "conditioning": cond}}
    graph["11"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": ["10", 0], "guider": ["17", 0], "sampler": ["9", 0],
        "sigmas": ["8", 0], "latent_image": latent}}
    return graph


def wait_prompt(port, prompt_id, timeout=3600):
    start = time.time()
    while time.time() - start < timeout:
        try:
            history = http_json(f"http://127.0.0.1:{port}/history/{prompt_id}", timeout=10)
        except Exception:
            history = {}
        if prompt_id in history:
            record = history[prompt_id]
            status = record.get("status", {})
            if status.get("status_str") == "error":
                messages = [m for m in status.get("messages", []) if m[0] == "execution_error"]
                raise RuntimeError(f"生成失败: {messages}")
            return record
        time.sleep(3)
    raise RuntimeError("等待生成超时")


def find_output(record, comfy: Path):
    for node in record.get("outputs", {}).values():
        for video in node.get("images", []):
            if video.get("filename", "").endswith((".mp4", ".webm", ".mov")):
                path = comfy / "output" / video.get("subfolder", "") / video["filename"]
                return path
    return None


def generate(comfy, port, args):
    mode = "t2v"
    images = []
    if args.ref_images:
        mode = "ref"
        images = [copy_input(comfy, p, f"ref{i}") for i, p in enumerate(args.ref_images)]
    elif args.first_frame:
        mode = "i2v"
        images = [copy_input(comfy, args.first_frame, "first")]
    length = args.length or snap_frames(args.seconds)
    prompt = args.prompt
    graph = build_graph(prompt, args.width, args.height, length, args.seed, mode, images)
    log(f"提交任务: mode={mode}, {args.width}x{args.height}, {length} 帧, 4 步")
    resp = http_post(f"http://127.0.0.1:{port}/prompt", {"prompt": graph, "client_id": "deploy-script"})
    prompt_id = resp["prompt_id"]
    log(f"任务已入队: {prompt_id}")
    record = wait_prompt(port, prompt_id, args.timeout)
    path = find_output(record, comfy)
    if path and path.exists():
        log(f"视频已生成: {path}")
        print(f"VIDEO_PATH={path}")
    else:
        log("任务完成但未找到输出文件")
    return path


def main():
    parser = argparse.ArgumentParser(description="ComfyUI + MiniMax H3 一键部署与生成")
    parser.add_argument("--root", default=str(Path.home() / "comfyui-h3"), help="安装根目录")
    parser.add_argument("--port", type=int, default=8188)
    parser.add_argument("--prompt", default=("A cinematic close-up of a white fox running through a snowy pine forest at dawn, "
                                             "breath visible in the cold air, slow motion, shallow depth of field. "
                                             "Audio: soft wind, snow crunching, distant birds."))
    parser.add_argument("--width", type=int, default=1344)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--seconds", type=float, default=5.17)
    parser.add_argument("--length", type=int, default=0, help="帧数(17k+5)，优先于 --seconds")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--first-frame", default=None, help="图生视频首帧图片")
    parser.add_argument("--ref-images", nargs="+", default=None, help="参考生视频参考图，可多张")
    parser.add_argument("--timeout", type=int, default=3600, help="单次生成超时秒数")
    parser.add_argument("--hf-mirror", default=HF_MIRROR)
    parser.add_argument("--no-tunnel", action="store_true")
    parser.add_argument("--no-models", action="store_true")
    parser.add_argument("--no-generate", action="store_true")
    parser.add_argument("--no-workflows", action="store_true")
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--skip-clone", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    apt_install(["git", "curl", "aria2"])
    if not which("git"):
        raise RuntimeError("缺少 git，无法继续")

    comfy = ensure_comfy(root, args.skip_clone)
    py = ensure_python_env(comfy, args.skip_install)
    cuda_report(py)
    ensure_models(comfy / "models", args.hf_mirror, args.no_models)
    ensure_workflows(comfy, Path(__file__).resolve().parent, not args.no_workflows)
    ensure_server(comfy, py, args.port)
    tunnel = ensure_tunnel(root, args.port, not args.no_tunnel)

    if tunnel:
        print(f"PUBLIC_URL={tunnel}")
    if not args.no_generate:
        generate(comfy, args.port, args)

    log("全部完成")
    if tunnel:
        log(f"ComfyUI 公网入口: {tunnel}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"[deploy] 失败: {exc}", file=sys.stderr)
        sys.exit(1)
