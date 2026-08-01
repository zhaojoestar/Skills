# vision - 让 Claude Code 拥有眼睛（Kimi K2.5）

一个 Claude Code skill，通过调用 Moonshot（月之暗面）**Kimi K2.5** 多模态 API，让当前模型获得视觉能力——识别本地图片、URL 图片、剪贴板截图，并能主动截屏查看屏幕。

适用于：使用不支持视觉的模型（如 DeepSeek）接入 Claude Code，但需要"看图"能力的场景。

## 功能

- ✅ 识别本地图片文件（PNG/JPG/WebP 等）
- ✅ 识别剪贴板截图（Win+Shift+S 后直接说"读剪贴板"）
- ✅ 识别图片 URL（自动下载）
- ✅ 主动全屏截图（"看看我的屏幕"；调试时自动截屏分析）
- ✅ 大图自动压缩（>4MB 自动缩放，无感使用）
- ✅ OCR 文字提取、界面识别
- ✅ `--thinking` 深度推理模式（复杂图表/公式）
- ✅ 零依赖（Python 标准库，无需 pip install）

## 安装

```powershell
# 1. 把本目录（vision 文件夹）复制到你的 Claude Code 技能目录
#    Windows: C:\Users\<你的用户名>\.claude\skills\vision\
#    macOS/Linux: ~/.claude/skills/vision/
#    重启 Claude Code 即可生效（技能列表中出现 vision 即成功）

# 2. 申请 Moonshot API key：https://platform.moonshot.cn （新用户有赠送额度）

# 3. 配置 key
python C:\Users\<你的用户名>\.claude\skills\vision\scripts\vision.py --config "sk-你的key"

# 4. 验证
python C:\Users\<你的用户名>\.claude\skills\vision\scripts\vision.py --check
```

## 使用

对 Claude Code 说以下话术即可自动触发：

| 你说 | 效果 |
|------|------|
| "识别这张图" / 给出图片路径 | 识别本地图片 |
| "看看我的屏幕" / "截个图看看" | 全屏截图并分析 |
| "读下剪贴板" | 识别剪贴板截图 |
| "这张图里的文字是什么" | OCR 提取文字 |
| "这个界面为什么报错" | 主动截图 + 分析（开发调试场景自动触发） |

### 命令行直接使用

```powershell
python vision.py <图片路径或URL> ["问题"]    # 识别文件/URL
python vision.py --clipboard ["问题"]        # 识别剪贴板
python vision.py --screenshot ["问题"]       # 截屏识别
python vision.py --thinking <来源> ["问题"]  # 深度推理模式
python vision.py --config "sk-xxx"           # 配置 API key
python vision.py --check                     # 检查配置与网络
python vision.py --mock <来源>               # 离线模拟（开发调试用）
```

## 费用

Kimi K2.5 按量计费：约 $0.6/百万输入 tokens、$3/百万输出 tokens。识别一张图片通常消耗几千 tokens，单张成本低于 0.01 美元。额度耗尽时脚本会明确提示充值或更换 key。

## 项目结构

```
vision/
├── SKILL.md            # 技能指令（触发场景 + 使用方式）
├── scripts/vision.py   # 核心脚本（Python 标准库，零依赖）
├── config.example.json # 配置示例（复制为 config.json 使用）
└── tests/              # unittest 测试（35 个用例）
```

## 工作原理

1. 图片获取：本地读取 / URL 下载 / PowerShell 读取剪贴板 / 全屏截图
2. 大图自动压缩（>4MB 时用 System.Drawing 缩放为 JPEG）
3. base64 编码后以 OpenAI 兼容格式发送到 `https://api.moonshot.cn/v1/chat/completions`
4. Kimi K2.5 返回中文描述（默认提示词：画面描述 + OCR + 界面识别）

> 注：剪贴板与截屏功能依赖 Windows + PowerShell（System.Drawing）；图片文件/URL 识别跨平台可用。
