# Soul App 知识库（MuMu 模拟器 / Android 15）

> **用途**：本地模型（soul-expert）的结构化知识来源。
> **来源**：全部为 2026-09-28 ~ 10-03 在副机 jian 的 MuMu 15.0 上**实测**所得（OCR + activity + 截图），无推断、无编造。
> **口径**：Soul 6.12.0 / Android 15。凡标「实测」= 真跑出来的；凡标「待确认」= 未验证。

---

## 一、环境与坐标系统（最重要，先读这一节）

### 1.1 运行环境
| 项 | 值 |
|---|---|
| 宿主 | 副机 **jian**（192.168.10.200），Windows 10 |
| 模拟器 | **MuMu Player 15.0**，CLI = `D:\MuMuPlayer\nx_main\mumu-cli.exe` |
| 安卓 | **Android 15**（Soul 6.12.0） |
| 设备分辨率 | **900 × 1600**（`wm size` 实测） |
| 截图尺寸 | **533 × 948**（窗口截图；比例 **900/533 ≈ 1.688**） |
| Python | `C:\Users\JIAN\.workbuddy\binaries\python\envs\soulocr\Scripts\python.exe`（含 RapidOCR） |

### 1.2 ⭐ 坐标系统铁律（踩坑最多的地方）
1. **一切 `input` 点击必须带 `-d <display>`**：`input -d 5 tap 615 1585`。
   不带 `-d` 会打到 display 0（MuMu 启动器桌面）→ 等于乱点。
2. **display 号是动态的**：Soul 跑在 MuMu 的**独立虚拟 display** 上，重启后会变
   （实测见过 **2 → 4 → 5**）。**绝不要写死**，每次用 `soul.display()` 动态查。
3. **截图与设备坐标的换算**：
   - 截图 PNG 是 **533×948**，`input`/OCR 用的是 **900×1600** 逻辑坐标。
   - 从 PNG 像素 X_shot 推设备坐标：`X_dev = X_shot × 900/533 ≈ X_shot × 1.688`；
     `Y_dev = Y_shot × 1600/948 ≈ Y_shot × 1.688`。
4. **OCR 坐标直接可用**：`soul_read.items()` 返回的 (x, y) **已经是 900×1600 设备坐标**，
   直接拿去 `input tap` 即可，不用换算。
5. **坐标会漂**：本文档里的坐标是**某一时刻实测值**，只作参考锚点。
   **正式操作优先「OCR 找文字 → 点它的坐标」**，不要死用坐标。

### 1.3 会话绑定铁律（为什么以前全失败）
- `mumu-cli sh` 与截图脚本都**绑定 Windows 会话**。Soul/MuMu 的 GUI 渲染窗口只活在
  **交互会话 Session 1**（jian 控制台登录）。
- 从 **SSH（Session 0）** 直接调 `mumu-cli sh` → 一律返回
  `errcode -201 "vm not running, can not connect NemuShell !!"`；截图则**全黑**。
- **正解**：用 `D:\MuMuPlayer\nx_main\PsExec64.exe -i 1 -accepteula <命令>` 把命令注入 Session 1。
- 但 `PsExec … xxx.cmd` 会**弹黑框**（cmd 控制台窗口）。**改用 `pythonw.exe` 跑 .py 全程无窗**：
  `PsExec64.exe -i 1 -accepteula <soulocr>\Scripts\pythonw.exe E:\soul\脚本.py`

### 1.4 截图正解
- ❌ `screencap -d 2` → RC=1 失败（独立虚拟 display 截不到）。
- ❌ `screencap`（默认）→ 截到的是 display 0（启动器），不是 Soul。
- ❌ `PrintWindow` 截 GPU 窗口 → 全黑（早期错误实现）。
- ✅ **`winshot.py`**：用 `PrintWindow(render_wnd, PW_RENDERFULLCONTENT)` 截 **MuMu 渲染窗口**
  → 输出 533×948 PNG，实测可用；再喂 RapidOCR 认字。

---

## 二、App 架构

- **只有 1 个 Activity 入口**：`MainActivity`（`cn.soulapp.android/cn.soulapp.android.component.startup.main.MainActivity`）。
  5 个底部 tab 全是 **Fragment 切换**，不换 Activity。
- `am start` 不能直达任意页面；`uiautomator dump` 在 MuMu 精简镜像**不可用**（缺 `libnativeloader.so`）。
- **定位元素的唯一可靠手段 = 截图 + OCR**（读文字 → 点坐标）。
- **验证状态的唯一可靠手段 = 读 SQLite 数据库**（`soul_im.py`）。

### 2.1 关键 Activity 对照表（实测）
| Activity（简写） | 对应页面 |
|---|---|
| `...component.startup.main.MainActivity` | 主框架：星球 / 广场 / 聊天 / 自己（tab 切换） |
| `...component.chat.ConversationActivity` | **会话页**（聊天窗口） |
| `...component.chat.CronyActivity` | 密友 / 关系页 |
| `...component.home.user.UserHomeActivity` | **资料页**（用户主页） |
| `...component.publish.publishchain.ui.activity.PublishChainActivity` | **发布弹层** |
| `cn.soulapp.android.miniprogram.core.activity.SLMiniAppMainActivity` | **小程序页**（如 MBTI 灵魂测试） |
| `cn.soul.android.soul_rn_sdk.multiengine.RnContainerActivity` | **RN 页面**：搜索页 / 用户主页 / 广场 feed |
| `cn.soulapp.android...TrampolineActivity` | 深链入口（`soul://`，path 仅 `/detail`、`/push_activity`、`/platformapi/startapp`） |

> ⚠️ **判「Soul 是否在前台」不能用 `"soulapp" in activity`** —— RN 页面 activity 是 `cn.soul.android.*`，
> **不含 soulapp**。正解：`activity.startswith("cn.soul")`（`cn.soulapp.*` 与 `cn.soul.*` 都命中）。

---

## 三、主框架：底部导航（900×1600 设备坐标 · 实测）

| Tab | OCR 文字坐标 | 说明 |
|---|---|---|
| **星球** | (103, 1585) | Soul 首页 / 匹配主战场（**唯一深色底页**） |
| **广场** | (287, 1585) | 动态流（顶部 关注/推荐/同城） |
| **发布** | (451, 1532) / 下方「瞬间」(451, 1558) | 中间凸起按钮 → **全屏弹层**，无底导航 |
| **聊天** | (615, 1585) | 会话列表（**默认页**） |
| **自己** | (802, 1585) | 个人主页 |

- 点击建议：直接点图标中心，y≈1583~1585 即可命中。
- **选中态判据（靠颜色，不靠文案）**：读底导航文字的像素均值，
  **`G-R > 35 且 B-R > 35` → 该 tab 选中**（选中为青色 ≈(140,232,230)，未选中灰 ≈(167,167,167)）。
- ⚠️ **不能只验「底导航有没有星球/广场/聊天」**——广场页和聊天页的底导航**长一样**，
  会误判成"已在聊天列表"。必须用上面的**颜色**判据。
- **底部红点**：广场(x≈287)、聊天(x≈615) 会挂红色数字角标，y≈1550。

### 3.1 图标含义（底导航，OCR 读不出图标靠视觉识别）
| 图标 | 位置 | 红点 | 含义 |
|---|---|---|---|
| 行星/土星环 | 星球 | — | 星球（首页/匹配） |
| 眼睛/光芒 | 广场 | 新动态数 | 广场 |
| 青绿圆形凸起 | 发布 | — | 发布瞬间 |
| 对话气泡 | 聊天 | 未读总数（服务端口径） | 聊天 |
| 人像头像 | 自己 | — | 自己 |

---

## 四、各页面详解

### 4.1 聊天页 · 会话列表（默认页）
- 顶部子 tab：`通讯录`(382,89) / `聊天`(518,89，默认选中)
- 搜索框：`Q搜索昵称或聊天记录`(202,188)
- 会话行结构：昵称 + 右侧时间 + 末条消息；**行距 ≈ 150~156 px**。

### 4.2 聊天页 · 通讯录（会话行**右滑**进入）
| 元素 | 坐标 |
|---|---|
| 搜索框「Q搜索备注、昵称」 | (172,188) |
| 密友 | (72,301) |
| 备注 | (73,421) |
| 关注我（右侧计数） | (89,541) |
| 官方号 | (89,661) |
| 群组 | (72,781) |
| 我的关注(N) / 全部 / 密友 | (123,905) / (731,905) / (834,905) |

### 4.3 会话页（ConversationActivity）
| 元素 | 坐标 | 备注 |
|---|---|---|
| 左上角返回箭头 | ≈(50,105)* | **绝不要用 keyevent 4**（根页面会退出 App） |
| 昵称标题 | y≈103 | 用于校验「是否进对人」 |
| 关注 按钮 | (771,105) | |
| 「N小时前」 | (345,104) | |
| 查看主页 | (835,342) | → **资料页** |
| 匹配度 NN% | (312,386) | |
| 她的引力签 | y≈453 | |
| 快捷语 | y≈1228~1332 | 早周末愉快 / 换一批 等 |
| 输入框 | (451,1532) | 占位「输入新消息」 |
| 发送按钮 | 输入后由灰变绿 | 位置随输入框右侧 |

> *返回箭头/输入框坐标沿用旧 720×1280 刻度，建议用 OCR 动态定位。
> **返回聊天列表的正解 = 会话页右滑**（见 §5 手势），比点箭头更稳。

### 4.4 资料页（UserHomeActivity）
| 元素 | 坐标 |
|---|---|
| 分享 | (759,100) |
| IP属地：XX | (798,151) |
| 匹配度 NN% | (450,437) |
| 「NNN天，NN个瞬间」 | (448,563) |
| 私聊 按钮 | (620,1553) |
| 「邀请ta发瞬间」 | (447,1456) |
| MBTI/标签（如 ENFJ、单身汪） | y≈600 附近 |

**进入方式**：① 会话页点「查看主页」；② **广场 feed 左滑**（见 §5）。

### 4.5 搜索页（RN 页面）
| 元素 | 坐标 |
|---|---|
| 取消 | (848,189) |
| 搜索框「Q搜索昵称或聊天记录」 | (202,189) |
> 搜索结果行的**昵称文字不可点**，只有右侧「私聊」按钮可点（旧刻度 x=629, y=行y+20）。

### 4.6 星球页（匹配主战场）
| 功能 | 坐标 | 说明 |
|---|---|---|
| 灵魂测试 | (141,94) | → 小程序 |
| 筛选 | (829,93) | → 筛选弹层 |
| **灵魂匹配** | (126,1327) | 下方「今日剩余N次」(124,1370) |
| **语音匹配** | (556,1325) | 下方「今日剩余N次」(547,1369) |
| 当前在线人数 | (453,1114) | 「当前NNNNNNN人在线」 |
| 同城卡/加速卡/定位卡 | (547,1208) | |

> ⚠️ **「灵魂匹配」点一下就扣次数**（实测 今日剩余 50 → 49）。此外会弹「语音匹配使用规范」弹窗，
> 弹窗按钮 **「我知道了」(449,1070)**。
> ⏰ **时段规则**：白天 **08:00~19:00 只回复**；晚上 **19:00~次日 08:00** 才允许匹配/奇遇铃。

### 4.7 星球 · 筛选弹层
| 元素 | 坐标 |
|---|---|
| 蒙面 | (117,1147) |
| 星球 | (117,1273) |
| 匹配 | (117,1402) |
| 各「不限」 | (730,1146)/(730,1274)/(730,1401) |
| **确定** | (451,1522) |

### 4.8 广场页
- 顶部：`推荐`(449,94) / `关注`(351,92) / `同城`(553,93)
- 下方：用户动态流（昵称 + 时间 + 正文 + 点赞/评论）。
- 会**插入广告卡片**（如「@抖音极速版 / 广告× / 领现金」），注意过滤。

### 4.9 自己页（个人主页）
| 元素 | 坐标 |
|---|---|
| 艺术家（标签） | (124,96) |
| 加好友 | (724,96) |
| 设置 | (835,96) |
| IP属地：XX | (801,159) |
| 超级星 | (124,697) |
| 引力签 · 立即提升 | (450,1008) |
| 全部 / 恰火 | (65,1087) / (796,1087) |

### 4.10 发布弹层（PublishChainActivity · 全屏）
| 元素 | 坐标 |
|---|---|
| 发布（右上） | (828,93) |
| 加话题 | (114,1377) |
| 你在哪里 | (111,1445) |
| 广场可见 | (800,1445) |
| @ Ta | (249,1446) |
| 发文字 | (256,1535) |
| 相册 | (386,1537) |
| 拍照片 | (512,1537) |
| 拍视频 | (653,1537) |
> 无底导航；关闭用左上角返回或 BACK。

### 4.11 小程序页（SLMiniAppMainActivity）
- 例：**MBTI 灵魂测试**（「MBTI灵魂测试 / 45.9万人测过 / 洞察自我」）。
- ⭐ **关闭方式 = 点右上角圆点 (855,94)**（用户指点 + 实测有效，
  点一下 act 立刻从 SLMiniAppMainActivity 回到 MainActivity）。
- ⚠️ 左上角 (56,116) 的箭头**关不掉**小程序；兜底用 `input keyevent 4`（BACK）。

### 4.12 奇遇铃卡片（陌生人随机匹配推送 · 高价值）
> 优先级最高：**弹出就优先点「立即私聊」开聊，绝不主动关闭**。
| 元素 | 坐标（旧刻度） | 说明 |
|---|---|---|
| 标题 `Soul·奇遇铃` | (124,144) | 铃铛带爱心动效 |
| 「同城奇遇」橙胶囊 | (248,147) | 匹配类型 |
| × 关闭 | ≈(640,145) | 右上角（一般不要点） |
| 对方头像 / 昵称 | (360,240) / (360,298) | |
| **「立即私聊」白底大按钮** | (360,541) | 点它进入会话 |
> 背景紫→蓝渐变。卡片**会自动消失**，检测到要立刻处理。
> 关掉的奇遇铃会落到聊天列表变成「奇遇铃-稍后再聊」会话（没消失，仍可进）。

---

## 五、手势地图（实测 · 900×1600）

| 位置 | 手势 | 效果 |
|---|---|---|
| 聊天列表 | **上滑** | 列表滚动（新会话从上方出现） |
| 聊天列表 | **下滑** | 回顶 |
| 会话行 | **右滑** | → **通讯录页** |
| 会话行 | **左滑** | 无反应 |
| 会话行 | **长按** | → **密友/关系页** `CronyActivity` |
| 广场 feed | **左滑** | → **用户主页（资料页）** `UserHomeActivity` |
| 广场 feed | 上滑 | 滚动到下一条 |
| 广场 feed | 下滑 | 无变化 |
| 星球网格 | 上滑 / 下滑 | 滚动 |
| **会话页** | **右滑** | → **返回聊天列表**（推荐用这个返回） |
| 会话页 | 上滑 / 下滑 | 无变化（无历史消息时） |
| 小程序 | 点右上角圆点 | 关闭 |

> `mumu-cli sh -c "input -d <display> swipe X1 Y1 X2 Y2 [ms]"` 执行滑动。
> 长按：`input -d <display> swipe X Y X Y 800`（同点、800ms）。

---

## 六、常用操作命令（全部经 PsExec 注入 Session 1）

```bash
MU=D:\MuMuPlayer\nx_main\mumu-cli.exe
PY=<soulocr>\Scripts\python.exe
PYW=<soulocr>\Scripts\pythonw.exe

# 启动 / 关闭 Soul（实例0）
mumu-cli control -v 0 app launch --package cn.soulapp.android
mumu-cli control -v 0 app close  --package cn.soulapp.android

# 读界面（截图+OCR，返回 文本 + 设备坐标 x/y）
python E:\soul\soul_read.py

# 读消息 / 未读 / 待回（数据库，最准）
python E:\soul\soul_im.py pull
python E:\soul\soul_im.py unread
python E:\soul\soul_im.py pending      # ← 判「谁在等我回」用这个

# 发消息（自动定位 + 发送 + 数据库校验）
python E:\soul\soul_reply.py "昵称" "内容"

# 任意点击 / 滑动 / 返回
mumu-cli sh -v 0 -c "input -d <display> tap X Y"
mumu-cli sh -v 0 -c "input -d <display> swipe X1 Y1 X2 Y2 300"
mumu-cli sh -v 0 -c "input keyevent 4"          # BACK

# 输入中文（官方 input_text，原生支持，无需 ADBKeyboard）
mumu-cli control -v 0 tool cmd -c input_text -t "中文内容"

# 截图（窗口，非 screencap）
python E:\soul\winshot.py E:\soul\wshot.png
```

---

## 七、多实例（多开）

| 项 | 实例 0 | 实例 1 |
|---|---|---|
| 名称 | 17320432910 | 17265041951 |
| render_wnd | `000304B8` | `041A063C` |
| adb_port | 16384 | 16416 |
| display（实测） | 4 | 2 |
| 目录 | `D:\MuMuPlayer\vms\MuMuPlayer-15.0-0` | `...-15.0-1` |

- **切换实例**：`mumu-cli` 加 `-v <index>`；脚本用环境变量 `SOUL_VMINDEX=<n>`，
  截图自动走 `wshot.<n>.png` + `.winshot_hwnd.<n>` 缓存。
- 启动第二个实例：`mumu-cli control -v 1 launch` → `control -v 1 show_window`。
- 克隆新实例：`mumu-cli clone --vmindex <n> --number <k>`（或 `--vmindex all`）。
- **两实例完全隔离**：句柄不同、display 不同、Soul 配置/权限状态各自独立
  （实测点实例1会弹它自己的「允许 Soul 拨打电话吗」权限窗，实例0早已配好）。

---

## 八、安全红线（违反会真出事故）

1. ⛔ **必须带 `-d <display>`**，否则点到桌面。
2. ⛔ **截图必须走窗口**（winshot），`screencap` 拿不到 Soul。
3. ⛔ **会话页根页面不要用 `keyevent 4`**，会直接退出 App（用右滑返回）。
4. ⛔ **发消息前必须确认当前在「聊天列表」页**——否则行坐标可能落到会话页
   顶部的「关注后可邀请通话」按钮上，**以你的名义误发通话邀请卡**（真实事故）。
5. ⛔ **判「谁在等我回」用 `pending()`，永远不要用 `session.unreadCount`**
   （后者含僵尸未读，与 UI 角标口径不一致）。
6. ⛔ **白天（08:00~19:00）只回复，不点匹配/奇遇铃**（点一次就扣次数且会打扰对方）。
7. ⛔ **奇遇铃不主动关**，它是最优质的获客渠道。
8. ⛔ **昵称含 emoji 要传去 emoji 子串**（如「星河入梦✨」传「星河入梦」走模糊匹配）。
9. ⛔ **闲置 >36h 的会话别用默认 6 屏去翻**（会沉到列表深处找不到），
   先放宽扫描深度（`SOUL_FIND_PAGES=30`）或先搜索。
10. ⛔ **不要用 `PsExec … .cmd`**（弹黑框），用 `pythonw.exe` 跑 `.py`。

---

## 九、知识来源索引
- 界面主文档：`E:\soul\SOUL_界面地图.md`
- 遍历数据（OCR 坐标）：`E:\soul\_uimap\*.json` / `*.png`
- 手势数据：`E:\soul\_uimap\gestures\*.json`
- 可见流程：`E:\soul\_uimap\demo\flow.log`
- 代码：`soul_read.py`（读屏）/ `soul_reply.py`（发消息）/ `soul_im.py`（数据库）/
  `winshot.py`（截图）/ `soul.py`（点击·display 探测）
