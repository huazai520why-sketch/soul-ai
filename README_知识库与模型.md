# Soul 知识库 + 本地模型（交付说明）

> 2026-10-03 建立。把 Soul App 的实测界面知识 + 江华人设，做成两个本地 Ollama 模型。

## 一、交付物清单

| 文件 | 说明 |
|---|---|
| `SOUL_KB.md` | **界面知识库母版**（页面/元素/坐标/手势/命令/安全红线），人读用 |
| `SOUL_KB.json` | 同一知识库的**结构化版**（程序/其他模型复用） |
| `SOUL_PERSONA.md` | **江华人设母版**（来源：`桌面\江华\江华.html`） |
| `Modelfile.soul` | 生成 `soul-expert` 的 Ollama 配置 |
| `Modelfile.persona` | 生成 `jianghua` 的 Ollama 配置 |
| `soul_llm.py` | **调用助手**（一行命令问模型） |
| `_build_soul_kb.py` / `_build_persona.py` | 母版 → Modelfile/JSON 的生成脚本 |

## 二、两个模型（本机 Ollama，G:\ollama）

| 模型 | 基础 | 用途 | temperature |
|---|---|---|---|
| **soul-expert** | qwen3:8b | 答 Soul 界面/坐标/手势/命令/安全 | 0.3（求准） |
| **jianghua** | qwen3:8b | 生成 Soul 聊天消息（江华口吻） | 0.85（求活） |

> 为什么分两个：**「操作知识」要严谨、「聊天话术」要活**，混在一个system prompt 里两头不讨好。

## 三、怎么用

### 本机
```bash
python D:\AI\soul_ui\soul_llm.py expert  "Soul 底部聊天 tab 坐标？"
python D:\AI\soul_ui\soul_llm.py persona "对方说：发张照片看看呗" --n 3
```

### 副机（jian）—— 已实测可跨机调用
```cmd
set OLLAMA_HOST=192.168.10.210:11434
python E:\soul\soul_llm.py persona "对方说：在干嘛呢"
```
> 依赖：本机 Ollama 监听 `0.0.0.0:11434`（已配）+ 本机防火墙已放行入站 11434
> （规则名 `Ollama 11434 (LAN)`，2026-10-03 加）。

### 直接调 API（任意语言）
```python
POST http://192.168.10.210:11434/api/generate
{"model":"jianghua","prompt":"...","stream":false,"think":false}
```

## 三补、⚠️ 人设口径冲突（已对齐，待你拍板）

`soul-chat` 技能里有一份 **2026-09-25 你确认过的聊天硬口径**，与 `江华.html` 有 3 处不一致：
年龄（32 vs 未写）、工作口径（普工两班倒 vs 技术流）、以及**你说过绝不能再犯的**
「不说写代码/做互联网」。

**当前处理 = 冲突时以技能硬口径为准**（详见 `SOUL_PERSONA.md` §8）：
对外仍说「厂里上班 普工 两班倒」，**不出现代码/脚本/程序/互联网等词**，
`江华.html` 的性格/口吻/网名（北大落榜生）/故事线全部吸收，爱好合并。

👉 **如果 `江华.html` 才是最新口径、要覆盖技能旧口径，告诉我一声，我改一处即可。**

## 四、验证结果（2026-10-03 实测）

**soul-expert**：10 题全过 —— display 动态性、截图走 winshot、会话页右滑返回、
白天不点匹配、小程序圆点关闭、pending() 判待回、广场左滑进资料页、
奇遇铃立即私聊、多实例切换、SSH -201 成因与 PsExec 解法，均与知识库一致。

**jianghua**：10 个聊天场景回归 —— **超 20 字 = 0、复述对方话 = 0、违禁词 = 0**。
口吻达"先防后暖"，重庆味（啷个/晓得/啥子）与自嘲（穷得叮当响）到位。

> ⚠️ 8B 模型**偶发自相矛盾/复述/编造亲戚**，所以加了**确定性后处理闸**
> （`soul_llm.clean_persona_lines`）：自动剔掉「复述对方原话」和「含代码/脚本/程序/互联网」
> 的行，被剔的行会打日志（stderr）。**不赌模型自觉，靠代码兜底。**

## 五、改动后怎么重建

改完 `SOUL_KB.md` 或 `SOUL_PERSONA.md` 后：
```bash
python D:\AI\工作台\_build_soul_kb.py      # → SOUL_KB.json + Modelfile.soul
python D:\AI\工作台\_build_persona.py      # → Modelfile.persona
ollama create soul-expert -f D:\AI\soul_ui\Modelfile.soul
ollama create jianghua    -f D:\AI\soul_ui\Modelfile.persona
```

## 六、注意

- `num_ctx 16384`（知识库约 1 万字符，够用）。若以后知识库变大，需同步调大。
- 知识库全部来自**实测**；模型被要求「没写明的必须说未收录」，但 8B 模型**偶有轻微脑补**，
  涉及坐标/跳转的答案仍建议对照 `SOUL_KB.md` 复核。
- 坐标是 900×1600 设备坐标、会漂移，**正式操作一律优先 OCR 动态定位**。
