# ComfyUI · MiniMax H3 一键部署

从零到出片：拉 ComfyUI（镜像）→ 装 CUDA 依赖 → 下载 int8 模型 → 起服务 → Cloudflare 快速隧道 → 直接生成视频，一个脚本跑完。

## 特性

- **全程走国内镜像**：ComfyUI 走 `ghfast.top` 等 GitHub 代理（多镜像回退）、pip 走阿里云 / 清华、模型走 `hf-mirror.com`
- **全 int8 模型**：hybrid b25-49、Qwen3-VL-32B 编码器、视频 VAE，体积与速度平衡
- **3 种生成模式**：文生（T2V）、图生（I2V）、参考生（R2V），均可 4 步加速
- **自动安装工作流**：把 T2V / I2V / R2V 三份工作流写入 ComfyUI 的 `user/default/workflows/`
- **输入图自动入库**：自带 `assets/1.png` 自动放入 `input/`；`--first-frame` / `--ref-images` 的本地图片也按原名复制进 `input/`
- **幂等可重跑**：目录/文件已存在自动跳过，不重复下载、不重复启动
- **Cloudflare 快速隧道**：免端口映射，直接给公网 URL

## 快速开始

```bash
git clone https://github.com/kylieption6/comfyui-h3-deploy.git
cd comfyui-h3-deploy

# 全新机器一键：部署 + 隧道 + 示例图/工作流 + 文生视频测试
python3 deploy_comfyui_h3.py --root ~/h3

# 自定义提示词
python3 deploy_comfyui_h3.py --root ~/h3 --prompt "A red paper lantern sways at night"

# 图生视频
python3 deploy_comfyui_h3.py --root ~/h3 --first-frame examples/sample.png --prompt "..."

# 参考生视频（多张）
python3 deploy_comfyui_h3.py --root ~/h3 --ref-images a.png b.png --prompt "<Picture 1> ... <Picture 2> ..."

# 只部署不生成
python3 deploy_comfyui_h3.py --root ~/h3 --no-generate
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--root` | `~/comfyui-h3` | 安装根目录 |
| `--port` | `8188` | 服务端口 |
| `--prompt` | 示例提示词 | 生成提示词 |
| `--width` / `--height` | `1344`/`768` | 输出分辨率（768p 匹配 LoRA 训练尺寸） |
| `--seconds` | `5.17` | 时长，自动对齐 17k+5 帧网格 |
| `--length` | `0` | 帧数，优先于 `--seconds` |
| `--seed` | `42` | 随机种子 |
| `--first-frame` | — | 图生视频首帧 |
| `--ref-images` | — | 参考图，可多张 |
| `--timeout` | `3600` | 单次生成超时（秒） |
| `--hf-mirror` | `https://hf-mirror.com` | HuggingFace 镜像 |
| `--no-tunnel` | — | 不建 Cloudflare 隧道 |
| `--no-models` | — | 跳过模型下载 |
| `--no-generate` | — | 只部署不生成 |
| `--no-workflows` | — | 不安装工作流 JSON |
| `--no-example` | — | 不复制 `assets/1.png` 到 input |
| `--skip-install` / `--skip-clone` | — | 跳过安装 / 克隆 |

## 自动安装的工作流

脚本会把仓库 `workflows/` 下的三份 JSON 复制到 `ComfyUI/user/default/workflows/`：

| 文件 | 用途 |
|---|---|
| `minimax_h3_t2v_turbo_4step_v1.0.json` | 文生视频 |
| `minimax_h3_i2v_turbo_4step_v1.0.json` | 图生视频（默认加载 `input/1.png`） |
| `minimax_h3_ref2v_turbo_4step_v1.0.json` | 参考生视频（默认加载 `input/1.png`） |

仓库自带 `assets/1.png`，部署时自动复制到 `ComfyUI/input/1.png`，I2V/R2V 工作流开箱即用。
用 `--first-frame` / `--ref-images` 指定的本地图片也会自动复制进 `ComfyUI/input/`，文件名保持不变。

## 自动下载的模型

| 组件 | 文件 | 大小 |
|---|---|---|
| 扩散模型（hybrid） | `minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors` | 19.5 GiB |
| 文本/多模态编码器 | `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | 25.3 GiB |
| 视频 VAE | `minimax_h3_video_vae_int8_convrot.safetensors` | 2.6 GiB |
| 音频 VAE | `minimax_h3_audio_vae_fp32.safetensors` | 0.58 GiB |
| T2V/I2V 加速 LoRA | `minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors` | 1.82 GiB |
| R2V 加速 LoRA | `minimax_h3_ref2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors` | 1.82 GiB |

合计约 52 GiB，需自行保证磁盘余量。

默认执行一次 T2V 生成（`--no-generate` 可关闭），完成后打印 `PUBLIC_URL` 与 `VIDEO_PATH`。

## 测试结果（A100 80GB）

| 场景 | 分辨率 | 帧数 | 4步采样耗时 |
|---|---|---|---|
| 文生视频 | 1344×768 | 124 | ~60s |
| 图生视频 | 768×1344 | 124 | ~90s |
| 参考生视频（2图） | 1344×768 | 124 | ~90s |

## 依赖

Python 3.12（系统自带即可）、git、curl、aria2。脚本会尝试通过 apt 自动安装缺失项。

## 常见问题

- **`CUDA False`**：确认驱动与 CUDA 版本匹配，或检查 `nvidia-smi` 是否可用。
- **隧道地址变了**：快速隧道重启后域名会变，脚本会复用已有隧道；如需固定域名请自行配置 named tunnel。
- **参考生图不一致**：把 `ref_image_size` 从 `match` 改成 `max`（更慢但一致性更好），需手动改工作流或脚本。
