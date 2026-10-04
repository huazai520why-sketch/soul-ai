#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Soul 每日素材抓取器（零依赖，只用标准库 urllib）。

============================================================
结论先行（2026-09-25 全量实测，别再重复踩）：
------------------------------------------------------------
【能用】
  1. 天气  https://api.open-meteo.com/v1/forecast   免 key、稳定、100% 安全
     → 最值得每天用的源。"三江今天毛毛雨""明天降温"本身就是最真实的开场，
       而且永远不会重复、不会踩雷。
     ⚠️ 2026-09-26 实测：**本沙箱访问境外站点时通时不通**（open-meteo 报 WinError 10054 / curl 000），
        故已加**国内兜底源**：中国天气网 `d1.weather.com.cn/sk_2d|dingzhi/<citycode>.html`
        （綦江 citycode = `101043300`，取自 `toy1.weather.com.cn/search?cityname=綦江`）
        —— 必须带 `User-Agent` + `Referer`，否则 403。两个源都失败时**抛异常**，不静默。

【时间上下文】`python soul_daily.py now`
  → 输出「此刻（日期/星期/时段）+ 假期中/调休上班 + 今天什么日子 + 节气 + 临近 30 天的节日 + 天气 + 我的位置」。
  数据全部**硬编码在脚本里 + 联网核实过**（法定放假=国务院办公厅 2026 年通知；传统节日/节气=百度百科 2026 词条）。
  ⚠️ **该表按年维护**：年份缺失时脚本显式报错，必须人工补录（跨年时务必先补下一年）。
  2. 抖音热榜 https://aweme.snssdk.com/aweme/v1/hot/search/list/?device_platform=android
     → 老接口实测稳（46 条，含热度）。官方 web 接口要 a_bogus 签名 + csrf cookie，走不通。
  3. ~~虎扑恋爱区~~ https://m.hupu.com/api/v2/bbs/topicThreads?topicId=6&page=1
     → ❌ **实测后弃用**（接口本身是通的，但内容不能用）。
       10 条热帖里只有 1 条（"明天见面了咋办"）可用，其余是"兄弟们给几个嗯嗯，压抑了"
       "喜欢的女生是女同""如果你是女人你会选哪个"这类 —— **擦边/性别对立，搭讪场景零容忍**。
       信噪比 1/10 且风险高，不值得留。恋爱话题改用**手写常青聊资**（见技能 soul-chat）。
  4. B站热搜 https://api.bilibili.com/x/web-interface/search/square
     → 生活娱乐占比高（游戏/番剧/影视/美食）。
  以上 2/3/4 **默认关闭**，要显式加 --with-hot 才抓，且全部过安全过滤。

【没有的东西，别再找了】
  - **重庆本地榜/同城榜：不存在可直接抓的公开接口**。抖音/微博同城榜都要登录态；
    重庆本地新闻站（华龙网 200、上游新闻 200）时政与事故密集，不适合当搭讪素材。
    → 本地话题改用「綦江天气（每日真实）+ 本地常青聊资（火锅/小面/三江/主城/自驾，永久可用）」，
      见技能 soul-chat 的「重庆本地常青聊资」章节。
  - GitHub 上的热榜项目（imsyy/DailyHotApi，4070 stars，58 个榜单路由）**也没有本地榜**；
    其公共实例（api-hot.imsyy.top）从本机连不通，但源码里的原始接口可直接取用（抖音就是这个思路）。

【不能用，别再试】
  - 百度文库/知乎/微博/小红书：文库是 JS 空壳 + 登录/VIP 墙（curl 仅 2519B）；
    微博 ajax 接口 403、知乎热榜 401、小红书 302；微博/知乎一律要登录态。
  - 头条热榜、百度热搜：**时政密度极高**。2026-09-25 实测 85 条热搜里绝大多数是中美峰会内容，
    且过滤层出现漏网（"特朗普听到大熊猫将落户美国笑了""博主：高市把政治表演刻进骨子里"
    被误判为"可聊"）——**一旦误发就是事故，因此本脚本已移除这两个源**。
  - 每日60秒新闻：同样以时政为主，过滤后基本剩 0 条，纯噪音，已移除。
  - ONE·一个：正文 JS 渲染抓不到；且其"文艺金句"风格与用户明令的"去 AI 味"
    （禁文艺比喻、禁洞察式金句）**直接冲突**，已移除。
  - RSSHub 公共实例 403、vvhan/hitokoto 在当前网络连不通。
============================================================

用法:
    python soul_daily.py refresh             # 默认只抓天气（安全）
    python soul_daily.py refresh --with-hot  # 额外抓 B站热搜并严格过滤
    python soul_daily.py today               # 打印当日缓存（过期自动 refresh）
    python soul_daily.py check               # 连通性自检
"""
import json
import os
import random
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "soul_daily.json")

LAT, LON = 29.05, 106.70          # 重庆綦江三江镇一带
PLACE = "重庆綦江"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# ==================== 安全过滤 ====================
# ① 黑名单（硬性）：命中即丢弃。时政 / 政要 / 军事外交 / 领土 / 灾难 / 恶性案件 / 评论解读
BLACK = [
    # 时政与政要
    "主席", "总书记", "总统", "总理", "首相", "元首", "首脑", "夫人", "外长", "国务卿",
    "国安顾问", "政治局", "常委", "人大", "政协", "国务院", "中央", "党政", "官员", "干部",
    "纪委", "巡视", "反腐", "落马", "贪腐", "受贿", "双开", "处分", "任命", "免职", "选举",
    "特朗普", "拜登", "哈里斯", "万斯", "普京", "泽连斯基", "马克龙", "莫迪", "高市",
    "石破", "岸田", "内塔尼亚胡", "马斯克", "白宫", "克里姆林", "唐宁街",
    # 外交 / 会谈 / 双边
    "外交", "会见", "会谈", "茶叙", "会晤", "访问", "峰会", "国宴", "欢迎宴会", "致辞",
    "两国", "中美", "中俄", "中日", "中欧", "中方", "美方", "俄方", "双边",
    # 领土主权（提及即丢，聊天场景零容忍）
    "台湾", "台海", "台独", "涉台", "香港", "港独", "新疆", "西藏", "藏独", "疆独",
    "南海", "一国两制", "统一",
    # 军事 / 制裁
    "国防", "军演", "军事", "导弹", "军舰", "航母", "战机", "部队", "开火", "战争",
    "停火", "袭击", "恐袭", "制裁", "关税", "贸易战", "封锁",
    # 灾难 / 事故
    "地震", "洪水", "泥石流", "山体滑坡", "爆炸", "起火", "火灾", "坠机", "空难",
    "沉船", "海啸", "遇难", "身亡", "死亡", "失联", "坍塌", "事故", "中毒",
    # 恶性案件 / 社会负面
    "杀人", "命案", "凶案", "自杀", "跳楼", "绑架", "拐卖", "诈骗", "洗钱", "贩毒",
    "判刑", "获刑", "起诉", "盗窃", "猥亵", "性侵", "家暴", "维权", "抗议", "游行",
    # 评论 / 解读类（聊天里最尬）
    "评论员", "有何警示", "立场", "回应称", "表态",
    # 宏观政策
    "央行", "降息", "加息", "限购", "证监会", "退市", "货币政策",
    # 擦边 / 低俗（虎扑恋爱区高频）
    "同居", "床上", "睡过", "身材", "胸", "裸", "擦边", "情人", "约炮", "出轨", "小三",
    "海王", "绿茶", "骚", "撩骚", "开车段子", "荤",
    # 性别对立（聊天里绝对不碰）
    "女权", "男权", "彩礼", "捞女", "舔狗", "渣男", "渣女", "打拳", "低人一等",
    "性别对立", "厌男", "厌女",
    # 感情负面
    "分手", "离婚", "失恋", "冷战", "被甩", "劈腿", "复合失败", "相亲被拒",
    # 社会 / 法律 / 舆情（新闻味太重，不适合搭讪）
    "起诉", "败诉", "判决", "判了", "违法", "被查", "歧视", "霸凌", "举报",
    "维权", "舆情", "网络暴力", "封号", "监管", "罚款",
    # 负面情绪 / 疾病
    "抑郁", "焦虑症", "失眠症", "治疗", "癌症", "住院",
]

# ② 白名单（收紧）：必须命中其一，才算"能拿去聊天"的生活向话题。
#    刻意不收体育/新闻类——热搜体育常与政治同框，且做聊天素材价值低。
WHITE = [
    # 游戏 / 二游
    "原神", "崩坏", "绝区零", "第五人格", "王者", "英雄联盟", "LOL", "永劫", "蛋仔",
    "游戏", "手游", "端游", "联动", "新版本", "版本", "皮肤", "赛季", "开服", "内测",
    # 动漫 / 番剧
    "番剧", "动画", "动漫", "漫画", "ova", "op", "ed", "剧场版", "国漫", "声优", "手办",
    # 影视 / 综艺
    "电影", "电视剧", "综艺", "上映", "票房", "开播", "首播", "收官", "预告", "花絮",
    "导演", "主演", "演员", "剧组", "续集", "重映",
    # 音乐 / 演出
    "新歌", "专辑", "演唱会", "音乐节", "livehouse", "翻唱", "歌单", "单曲",
    # 美食 / 消费
    "美食", "好吃", "小吃", "火锅", "奶茶", "咖啡", "月饼", "外卖", "探店", "菜市场",
    "穿搭", "美妆", "口红", "发型", "汉服", "配色",
    # 生活 / 宠物 / 出行
    "宠物", "猫", "狗", "萌宠", "种花", "收纳", "手工", "露营", "爬山", "自驾",
    "民宿", "民宿", "citywalk", "打卡", "景区", "门票",
    # 节日 / 时令（中秋国庆这类，安全且好聊）
    "中秋", "国庆", "元旦", "春节", "端午", "七夕", "假期", "放假", "调休", "抢票",
    # 抖音/虎扑高频的生活词
    "月亮", "文案", "许愿", "月饼", "烧烤", "小龙虾", "串串", "面馆", "早餐", "做饭",
    "减肥", "健身", "跑步", "篮球", "球赛", "看球", "游戏", "开黑", "追剧", "电视剧",
    "相亲", "约会", "见面", "表白", "女朋友", "男朋友", "对象", "脱单", "恋爱",
]


def _hit(text, words):
    return [w for w in words if w in text]


def _safe(text, mode="dual"):
    """返回 (是否可用, 原因)。

    mode="dual"      黑白名单双闸（用于热搜类，噪音大）
    mode="blackonly" 只过黑名单 + 长度（用于"天然就是生活向"的版面，如虎扑恋爱区）
    """
    t = (text or "").strip()
    if len(t) < 4 or len(t) > 40:
        return False, "长度不符"
    b = _hit(t, BLACK)
    if b:
        return False, "敏感词:" + b[0]
    if mode == "blackonly":
        return True, "仅过黑名单"
    w = _hit(t, WHITE)
    if not w:
        return False, "非生活向"
    return True, "命中:" + w[0]


def _get(url, timeout=15):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "*/*")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


# ==================== 源 ====================
WCODE = {0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
         51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 61: "小雨", 63: "中雨",
         65: "大雨", 66: "冻雨", 67: "冻雨", 71: "小雪", 73: "中雪", 75: "大雪",
         80: "阵雨", 81: "中阵雨", 82: "强阵雨", 95: "雷阵雨", 96: "雷阵雨带冰雹", 99: "强雷暴"}


def src_weather():
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
           "precipitation_probability_max&timezone=Asia%%2FShanghai&forecast_days=3"
           % (LAT, LON))
    d = _get(url)
    dd = d.get("daily", {})
    days = []
    for i, t in enumerate(dd.get("time", [])):
        days.append({
            "date": t,
            "code": (dd.get("weather_code") or [None])[i],
            "tmax": (dd.get("temperature_2m_max") or [None])[i],
            "tmin": (dd.get("temperature_2m_min") or [None])[i],
            "rain": (dd.get("precipitation_probability_max") or [None])[i],
        })
    return days


def weather_text():
    """天气主入口：open-meteo（3 天预报）→ 失败自动降级中国天气网（国内直连）。

    ⚠️ 两个源都失败时**抛异常**（由调用方记录到 errors），绝不返回空当成功。
    2026-09-26 实测：代理断掉时 open-meteo 直接 WinError 10054，故必须有国内兜底。
    """
    try:
        w = _weather_openmeteo()
        w["source"] = "open-meteo"
        return w
    except Exception as e1:
        w = _weather_cn()          # 再失败就让异常冒出去
        w["degraded"] = "open-meteo 不可用(%s)，已降级中国天气网" % str(e1)[:50]
        return w


def _weather_openmeteo():
    days = src_weather()
    if not days:
        return None
    d0 = days[0]
    w = WCODE.get(d0.get("code"), "未知")
    line = "%s %s %s~%s℃" % (PLACE, w, d0.get("tmin"), d0.get("tmax"))
    rain = d0.get("rain")
    if rain is not None and rain >= 40:
        line += " 有雨概率%d%%" % rain
    nxt = ""
    if len(days) > 1:
        d1 = days[1]
        nxt = "｜明天%s %s~%s℃" % (WCODE.get(d1.get("code"), "?"), d1.get("tmin"), d1.get("tmax"))
    return {"text": line, "tomorrow": nxt, "raw": days}


# ---- 备用源：中国天气网（国内直连、免 key；必须带 UA + Referer，否则 403）----
CN_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
CN_CITY = "101043300"   # 綦江（取自 toy1.weather.com.cn/search?cityname=綦江，2026-09-26 核实）


def _get_cn(url, referer="http://www.weather.com.cn/"):
    req = urllib.request.Request(url, headers={
        "User-Agent": CN_UA, "Referer": referer,
        "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=12) as r:
        raw = r.read()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", "ignore")


def _cn_obj(txt, marker):
    """从 `var xxx ={...}` 这类 JS 里抠出第一个完整 JSON 对象（按括号配平，不靠 rfind）。"""
    i = txt.find(marker)
    if i < 0:
        raise ValueError("未找到标记 %s" % marker)
    j = txt.find("{", i)
    if j < 0:
        raise ValueError("标记 %s 后无 JSON" % marker)
    depth = 0
    for k in range(j, len(txt)):
        if txt[k] == "{":
            depth += 1
        elif txt[k] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(txt[j:k + 1])
    raise ValueError("标记 %s 的 JSON 括号不配平" % marker)


def _weather_cn():
    base = "http://d1.weather.com.cn"
    sk = _cn_obj(_get_cn("%s/sk_2d/%s.html" % (base, CN_CITY),
                         "http://www.weather.com.cn/weather1d/%s.shtml" % CN_CITY), "dataSK")
    try:
        dz = _cn_obj(_get_cn("%s/dingzhi/%s.html" % (base, CN_CITY)), '"weatherinfo"')
    except Exception:
        dz = {}
    nm = "重庆" + (sk.get("cityname") or "綦江")
    wx = sk.get("weather") or dz.get("weather") or "?"
    tmax = (dz.get("temp") or "").replace("℃", "")
    tmin = (dz.get("tempn") or "").replace("℃", "")
    rng = "%s~%s℃" % (tmin, tmax) if (tmin and tmax) else "?"
    line = "%s %s %s（%s实况 %s℃ 湿度%s %s%s）" % (
        nm, wx, rng, sk.get("time", "--"), sk.get("temp", "?"),
        sk.get("SD") or "?", sk.get("WD") or "", sk.get("WS") or "")
    if sk.get("aqi"):
        line += " 空气%s" % sk["aqi"]
    nxt = ""
    if dz:
        nxt = "｜今天 %s %s~%s℃" % (dz.get("weather", ""), tmin, tmax)
    return {"text": line, "tomorrow": nxt, "raw": {"sk": sk, "dz": dz}}


def src_bilibili():
    d = _get("https://api.bilibili.com/x/web-interface/search/square?limit=30")
    out = []
    for it in (d.get("data", {}).get("trending", {}) or {}).get("list", []) or []:
        kw = (it.get("keyword") or it.get("show_name") or "").strip()
        if kw:
            out.append({"from": "B站热搜", "text": kw})
    return out


def src_douyin():
    """抖音热榜。官方 web 接口要签名，用 snssdk 老接口，实测稳定（46 条）。

    DailyHotApi（github.com/imsyy/DailyHotApi）走的是
    www.douyin.com/aweme/v1/web/hot/search/list?device_platform=webapp&aid=6383
    + 先取 passport_csrf_token；本机实测该路走不通，老接口反而稳。
    """
    d = _get("https://aweme.snssdk.com/aweme/v1/hot/search/list/?device_platform=android")
    wl = (d.get("data", {}) or {}).get("word_list") or d.get("word_list") or []
    out = []
    for it in wl:
        kw = (it.get("word") or "").strip()
        if kw:
            out.append({"from": "抖音热榜", "text": kw, "hot": it.get("hot_value")})
    return out


def src_hupu_love():
    """虎扑恋爱区热帖标题（topicId=6）。

    这是最贴"恋爱/见面"的公开源——标题就是真实恋爱情境（"明天见面了咋办"这类），
    可直接当话题引子。因为整个版面天然是生活向，用 blackonly 过滤（只拦擦边/对立/负面）。
    ⚠️ 步行街主干道(1) 和 校园区(11) **不要用**：社会新闻与性别话题密集，实测出现
       "北理工女教师裴某言论引爆网络""女权人士"等，搭讪场景零容忍。
    """
    d = _get("https://m.hupu.com/api/v2/bbs/topicThreads?topicId=6&page=1")
    ts = (d.get("data", {}) or {}).get("topicThreads") or []
    out = []
    for it in ts:
        kw = (it.get("title") or "").strip()
        if kw:
            out.append({"from": "虎扑恋爱区", "text": kw, "hot": it.get("replies")})
    return out


# 每源过滤策略：dual=黑白双闸；blackonly=仅黑名单
SOURCE_MODE = {"抖音热榜": "dual", "B站热搜": "dual"}


# ==================== 组装 ====================
def refresh(with_hot=False, verbose=True):
    result = {"date": date.today().strftime("%Y-%m-%d"),
              "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
              "weather": None, "topics": [], "dropped": 0, "errors": [],
              "with_hot": bool(with_hot)}
    try:
        result["weather"] = weather_text()
    except Exception as e:
        result["errors"].append("天气: " + str(e)[:90])
        if verbose:
            print("!! 天气抓取失败: %s" % e)

    if with_hot:
        seen = set()
        for sname, fn in (("抖音热榜", src_douyin), ("B站热搜", src_bilibili)):
            mode = SOURCE_MODE.get(sname, "dual")
            try:
                raw = fn()
            except Exception as e:
                result["errors"].append("%s: %s" % (sname, str(e)[:90]))
                if verbose:
                    print("!! %s 抓取失败: %s" % (sname, e))
                continue
            for it in raw:
                ok, why = _safe(it["text"], mode)
                if not ok:
                    result["dropped"] += 1
                    continue
                key = re.sub(r"\W+", "", it["text"])[:14]
                if key in seen:
                    continue
                seen.add(key)
                rec = {"from": sname, "text": it["text"], "why": why}
                if it.get("hot") is not None:
                    rec["hot"] = it["hot"]
                result["topics"].append(rec)

    # 最终安全断言：输出前再跑一次黑名单，任何一条可疑就剔除（防白名单/黑名单改错）
    kept = []
    for it in result["topics"]:
        if _hit(it["text"], BLACK):
            result["dropped"] += 1
            continue
        kept.append(it)
    result["topics"] = kept

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    if verbose:
        print("已写入 %s" % OUT)
    return result


# ============================================================================
# 时间 / 节假日 / 节气 上下文（2026-09-25 新增）
# ----------------------------------------------------------------------------
# 为什么需要：聊天要"贴当下"——几点、星期几、什么天气、什么日子。
#   "三江今天毛毛雨""今天中秋 你吃月饼没" 比 "在干嘛" 像真人得多，且零风险。
#
# 数据来源（2026-09-25 联网逐条核实，**禁止凭记忆改**）：
#   ① 法定放假/调休：国务院办公厅《关于2026年部分节假日安排的通知》国办发明电〔2025〕7号
#   ② 传统节日 + 二十四节气交节日期：百度百科「2026年」词条
#
# ⚠️ 本表**按年维护**：年份不在表中时，脚本会**显式报错**（不静默、不猜），
#    必须人工补录后再用。跨年（12 月下旬）时务必先补下一年。
# ============================================================================

# (起月, 起日, 止月, 止日, 名称, 备注)
LEGAL_OFF = {
    2026: [
        (1, 1, 1, 3, "元旦", "共3天；1/4(周日)调休上班"),
        (2, 15, 2, 23, "春节", "共9天；2/14(周六)、2/28(周六)调休上班"),
        (4, 4, 4, 6, "清明节", "共3天"),
        (5, 1, 5, 5, "劳动节", "共5天；5/9(周六)调休上班"),
        (6, 19, 6, 21, "端午节", "共3天"),
        (9, 25, 9, 27, "中秋节", "共3天；中秋高速不免费"),
        (10, 1, 10, 7, "国庆节", "共7天；9/20(周日)、10/10(周六)调休上班"),
    ],
}
MAKEUP_WORK = {
    2026: ["2026-01-04", "2026-02-14", "2026-02-28",
           "2026-05-09", "2026-09-20", "2026-10-10"],
}
# kind: trad=中国传统节日 ｜ memo=祭祀类(不宜道贺) ｜ term=节气 ｜ other=洋节/商业节
FEST = {
    2026: [
        (1, 5, "小寒", "term"), (1, 20, "大寒", "term"),
        (2, 4, "立春", "term"), (2, 14, "情人节", "other"),
        (2, 17, "春节·正月初一", "trad"), (2, 18, "雨水", "term"),
        (3, 3, "元宵节", "trad"), (3, 5, "惊蛰", "term"),
        (3, 8, "妇女节", "other"), (3, 20, "春分", "term"), (3, 20, "龙抬头", "trad"),
        (4, 5, "清明·节气", "term"), (4, 20, "谷雨", "term"),
        (5, 5, "立夏", "term"), (5, 20, "520", "other"), (5, 21, "小满", "term"),
        (6, 1, "儿童节", "other"), (6, 5, "芒种", "term"),
        (6, 19, "端午节", "trad"), (6, 21, "夏至", "term"),
        (7, 7, "小暑", "term"), (7, 23, "大暑", "term"),
        (8, 7, "立秋", "term"), (8, 19, "七夕节", "trad"),
        (8, 23, "处暑", "term"), (8, 27, "中元节", "memo"),
        (9, 7, "白露", "term"), (9, 10, "教师节", "other"),
        (9, 23, "秋分", "term"), (9, 25, "中秋节", "trad"),
        (10, 1, "国庆节", "trad"), (10, 8, "寒露", "term"),
        (10, 18, "重阳节", "trad"), (10, 23, "霜降", "term"),
        (11, 7, "立冬", "term"), (11, 9, "寒衣节", "memo"),
        (11, 11, "双十一", "other"), (11, 22, "小雪", "term"), (11, 23, "下元节", "memo"),
        (12, 7, "大雪", "term"), (12, 22, "冬至", "term"),
        (12, 24, "平安夜", "other"), (12, 25, "圣诞节", "other"),
    ],
}

# (起时, 止时, 标签, 建议)  —— 覆盖 0~24，判断 now.hour 落在哪段
SLOTS = [
    (0, 5, "后半夜", "她还醒着＝大概率也睡不着；别用'还没睡？'开场，直接说自己在干嘛"),
    (5, 8, "早上", "聊早起、上班路上、今天天气（我 6 点起）"),
    (8, 12, "上午", "我在车间，她也在忙 → 消息短一点，别连发"),
    (12, 14, "中午", "聊吃饭最自然（食堂/外卖/带饭）"),
    (14, 18, "下午", "聊今天累不累、几点下班、晚饭吃啥"),
    (18, 20, "傍晚·刚下班", "★ 最好开口的时段：'刚下班 累成狗'"),
    (20, 23, "晚上", "她的空闲时间，能多聊几句，适合聊兴趣"),
    (23, 24, "深夜", "快睡了，别开新话题，接完话就收"),
]


def _slot(hour):
    for a, b, label, hint in SLOTS:
        if a <= hour < b:
            return label, hint
    return "晚上", ""


def _kind_note(kinds):
    if "memo" in kinds:
        return "（祭祀类节日 · **不要道贺、不要提节日快乐**）"
    if "term" in kinds and "trad" not in kinds and "other" not in kinds:
        return "（节气 · 可以聊天气/时令吃食）"
    return ""


# ---- 方言（重庆话 / 綦江土话）2026-09-26 新增 ----
# 来源：百度百科「重庆话」词条 ＋ 綦江本地站点（qj023.com《綦江的方言俏皮话》、重庆綦江网《綦江人说话幽默风趣》），联网核实。
# ⚠️ 綦江土话里**粗俗/擦边词极多**（宝批龙、少批跨、求不楞腾、卵人、雀雀…）→ 已整批拉黑，见 DIALECT_NO。
# 分级：safe=任何阶段可用 ｜ mid=聊熟了再用 ｜ tease=只作轻调侃（需较高亲密度）
DIALECT = [
    ("要得", "好的/行", "safe", "答应、应和，最百搭"),
    ("巴适", "舒服/好/爽", "safe", "夸吃的、夸天气、夸安排"),
    ("安逸", "舒服/满意", "safe", "比巴适更口语"),
    ("晓得", "知道", "safe", "别再说\"知道了\""),
    ("啥子", "什么", "safe", "最常用的替换词"),
    ("啷个", "怎么/怎么样", "safe", "问句里用"),
    ("一哈儿", "一会儿", "safe", "\"我一哈儿就下班\""),
    ("好耍", "好玩", "safe", "聊去哪玩"),
    ("落雨", "下雨", "safe", "配天气用，比\"下雨\"土"),
    ("嘎嘎", "肉", "safe", "吃饭场景，很接地气"),
    ("刹一脚", "（开车）停一下/喊停", "safe", "★ 我有车，特别贴人设"),
    ("扎起", "加油/撑场子", "safe", "给她打气"),
    ("哦豁", "哎呀/完了", "safe", "小意外、小吐槽"),
    ("撇托", "省事/干脆", "safe", "\"这样最撇托\""),
    ("赶场", "赶集", "safe", "聊本地日常"),
    ("累惨了", "累坏了", "safe", "★ 綦江拿\"惨了\"加强语气：好惨了/巴适惨了"),
    ("龙门阵", "闲聊", "mid", "\"摆龙门阵\"=聊天"),
    ("卡卡角角", "角角落落", "mid", "聊小店、找地方"),
    ("丁丁猫", "蜻蜓", "mid", "生活小景"),
    ("偷油婆", "蟑螂", "mid", "吐槽用"),
    ("巴适惨了", "特别好", "mid", "夸得用力一点"),
    ("搞归一", "做完/弄完", "mid", "\"活路搞归一了\""),
    ("毛焦火辣", "心急/烦躁", "mid", "她吐槽忙/烦时"),
    ("弟娃 / 妹儿", "对年轻人的称呼", "mid", "本地人真这么叫，别用\"小姐姐\""),
    ("哈儿 / 憨包", "傻（调侃）", "tease", "只在她先调侃你时才回敬，语气要软"),
]
DIALECT_NO = ["婆娘", "堂客", "宝批龙", "少批跨", "求不楞腾", "卵人", "雀雀",
              "老子", "龟儿", "傻撮撮", "该背时", "二流子", "神头儿"]


def dialect_pick(n=4):
    """按日期稳定轮换：同一天多次调用结果一致，隔天换一批。"""
    pool = [d for d in DIALECT if d[2] in ("safe", "mid")]
    return random.Random(date.today().toordinal()).sample(pool, min(n, len(pool)))


def _fest_dates(year):
    rows = FEST.get(year)
    return None if rows is None else [(date(year, m, d), n, k) for m, d, n, k in rows]


def _legal_ranges(year):
    rows = LEGAL_OFF.get(year)
    return None if rows is None else [
        (date(year, m1, d1), date(year, m2, d2), n, note) for m1, d1, m2, d2, n, note in rows]


def ctx_lines(now=None, weather=None, days_ahead=30):
    """生成「时间上下文」文本行。返回 (lines, errors)。

    weather 可传 soul_daily 已抓的天气 dict（避免重复请求）。
    """
    now = now or datetime.now()
    today = now.date()
    y = today.year
    lines, errs = [], []
    wk = "一二三四五六日"[today.weekday()]
    label, hint = _slot(now.hour)

    lines.append("【此刻】%s %s · 周%s · %s" % (today.strftime("%Y-%m-%d"), now.strftime("%H:%M"), wk, label))
    if hint:
        lines.append("         → %s" % hint)

    lr = _legal_ranges(y)
    fd = _fest_dates(y)
    if lr is None:
        errs.append("!! 法定节假日表未录入 %d 年 —— 请人工补录 LEGAL_OFF/MAKEUP_WORK（跨年必须更新）" % y)
    if fd is None:
        errs.append("!! 节日/节气表未录入 %d 年 —— 请人工补录 FEST（跨年必须更新）" % y)

    # 假期中 / 调休上班
    today_s = today.strftime("%Y-%m-%d")
    for a, b, name, note in (lr or []):
        if a <= today <= b:
            lines.append("【假期中】%s 第 %d 天／共 %d 天（%s~%s）"
                         % (name, (today - a).days + 1, (b - a).days + 1,
                            a.strftime("%m/%d"), b.strftime("%m/%d")))
            break
    if today_s in (MAKEUP_WORK.get(y) or []):
        lines.append("【调休】⚠️ 今天是**调休上班日**（不是休息日，别问她\"放假去哪玩了\"）")

    # 今天是什么日子
    todays = [(n, k) for d, n, k in (fd or []) if d == today]
    if todays:
        lines.append("【今天】" + "、".join(n for n, _ in todays) + _kind_note([k for _, k in todays]))

    # 节气：上一个已过
    terms = [(d, n) for d, n, k in (fd or []) if k == "term" and d < today]
    if terms:
        d, n = terms[-1]
        lines.append("【节气】%s 已过 %d 天" % (n, (today - d).days))

    # 临近（days_ahead 天内）：法定放假带区间优先，同名节日不重复列
    ahead = {}
    for a, b, name, note in (lr or []):
        delta = (a - today).days
        if 0 < delta <= days_ahead:
            ahead[name] = (delta, "%s(%s~%s 放假%d天)"
                           % (name, a.strftime("%m/%d"), b.strftime("%m/%d"), (b - a).days + 1))
    for d, n, k in (fd or []):
        delta = (d - today).days
        if 0 < delta <= days_ahead:
            base = n.split("·")[0]
            if base in ahead:
                continue
            ahead[base] = (delta, "%s(%s)" % (n, d.strftime("%m/%d")))
    if ahead:
        parts = ["%s 还有%d天" % (txt, delta)
                 for delta, txt in sorted(ahead.values())][:5]
        lines.append("【临近】" + " ｜ ".join(parts))

    # 天气
    if weather:
        lines.append("【天气】" + weather.get("text", "") + weather.get("tomorrow", ""))
        if weather.get("degraded"):
            lines.append("         ⚠️ " + weather["degraded"])

    # 地理位置锚点（固定事实，不随天变；聊天里用来聊本地/聊距离/聊见面）
    lines.append("【我的位置】重庆綦江·三江镇（自己住）｜开车到主城约 1 小时｜有车会开")
    lines.append("           本地常青料：綦江小面/火锅/古剑山/夜宵摊/三江早雾")

    return lines, errs


def ctx_facts(now=None, weather=None):
    """把时间/天气/节日转成「可以直接说出口的事实句」，供模型改写（禁止原样照搬）。"""
    now = now or datetime.now()
    today = now.date()
    label, _ = _slot(now.hour)
    facts = []
    facts.append("现在 %s（%s）" % (now.strftime("%H:%M"), label))
    if weather:
        w = weather.get("text", "")
        if w:
            facts.append(w)
    todays = [(n, k) for d, n, k in (_fest_dates(today.year) or []) if d == today]
    if todays:
        facts.append("今天是" + "、".join(n for n, _ in todays))
    for a, b, name, note in (_legal_ranges(today.year) or []):
        if a <= today <= b:
            facts.append("现在在%s假里（第%d天）" % (name, (today - a).days + 1))
            break
    # 地理位置（固定事实）
    facts.append("我在綦江三江，开车到主城一个小时")
    facts.append("可用的本地词：" + "、".join(w for w, _, _, _ in dialect_pick(3)))
    # 下一个法定假期（从表里推，不写死日期）
    for a, b, name, note in (_legal_ranges(today.year) or []):
        if a > today:
            facts.append("%s 还有%d天，放%d天" % (name, (a - today).days, (b - a).days + 1))
            break
    return facts


def render_ctx(now=None, weather=None):
    lines, errs = ctx_lines(now=now, weather=weather)
    now = now or datetime.now()
    print("== Soul 时间上下文 | %s ==" % now.strftime("%Y-%m-%d %H:%M %A"))
    for ln in lines:
        print("  " + ln)
    print("\n【可用事实（挑贴合的，按 v2 方法论改写后再发，禁止原样照搬）】")
    for f in ctx_facts(now=now, weather=weather):
        print("  · " + f)
    print("\n【方言 · 重庆话/綦江土话（按日期轮换，今天用这几个）】")
    for w, mean, lvl, use in dialect_pick(4):
        print("  · %s（%s）— %s%s" % (w, mean, use, "  [熟了再用]" if lvl == "mid" else ""))
    print("  ⚠️ 一次最多用 1~2 个词，**别整句方言**；先丢最通用的（要得/巴适/啥子）试探：")
    print("     她接方言 → 再加码；她没反应/看不懂 → 立刻收回普通话。对方是外地人就少用。")
    print("  ⛔ 绝不用（粗俗/擦边/只能对配偶）：" + "、".join(DIALECT_NO))
    print("\n【用法】时间/天气/节日/地理位置/方言都只当**具体事实**用（几点、啥天气、什么日子、多远、怎么说），")
    print("        比\"在干嘛\"像真人得多；挑贴她上下文的重写后再发，禁止原样照搬。")
    print("【她的位置】必须从 Soul 会话资料里读（共同点含\"重庆\"=GPS 同城；距离 km 在她资料/主页里）")
    print("        —— ⛔ 不许自己猜她在哪，也⛔ 不许拿位置查户口（\"你哪人/住哪\"）")
    print("【红线】⛔ 禁止借节日/天气/地缘扯时政、政要、军事、领土主权、灾难事故、恶性案件。")
    for e in errs:
        print("  " + e)


def _load_today():
    if not os.path.exists(OUT):
        return None
    try:
        with open(OUT, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if d.get("date") != date.today().strftime("%Y-%m-%d"):
        return None
    return d


def render(d):
    print("== Soul 每日素材 | %s (生成 %s) ==" % (d["date"], d.get("generated_at")))
    wt = d.get("weather")
    if wt:
        print("\n【天气 · 可直接当真实开场】")
        print("  " + wt["text"] + wt.get("tomorrow", ""))
    else:
        print("\n【天气】抓取失败，见下方异常")
    topics = d.get("topics", [])
    if d.get("with_hot"):
        groups = {}
        for t in topics:
            groups.setdefault(t["from"], []).append(t)
        order = ["抖音热榜", "B站热搜"]
        labels = {"抖音热榜": "抖音破冰话题", "B站热搜": "B站破冰话题"}
        print()
        for src in order:
            rows = groups.get(src)
            if not rows:
                continue
            print("【%s %d 条】" % (labels.get(src, src), len(rows)))
            for t in rows[:12]:
                print("  - %s" % t["text"])
        for src, rows in groups.items():
            if src not in order:
                print("【%s %d 条】" % (src, len(rows)))
                for t in rows[:12]:
                    print("  - %s" % t["text"])
        print("  已过滤 %d 条（时政/灾难/案件/擦边/性别对立/非生活向）" % d.get("dropped", 0))
    else:
        print("\n【破冰话题】本轮未抓（热搜含时政风险，默认关闭；需要时加 --with-hot）")
    for e in d.get("errors", []):
        print("  !! " + e)
    print("\n-- 用法：只当破冰引子，必须结合她的上下文改写，禁止硬套、禁止原文照搬 --")


def cmd_check(args):
    print("连通性自检:")
    for name, fn in (("天气(open-meteo)", src_weather), ("抖音热榜", src_douyin),
                     ("B站热搜", src_bilibili)):
        try:
            r = fn()
            print("  OK   %-16s %d 条" % (name, len(r)))
        except Exception as e:
            print("  FAIL %-16s %s" % (name, str(e)[:70]))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, args = sys.argv[1], sys.argv[2:]
    with_hot = "--with-hot" in args
    if cmd == "refresh":
        render(refresh(with_hot=with_hot))
    elif cmd == "today":
        d = _load_today()
        if d is None:
            print("(当日缓存不存在或已过期，自动抓取...)")
            d = refresh(with_hot=with_hot, verbose=False)
        render(d)
    elif cmd == "now":
        # 时间上下文：日期/星期/时段/节假日/节气 + 天气（先读当日缓存，缺失才现抓）
        d = _load_today()
        w = (d or {}).get("weather")
        if not w:
            try:
                w = weather_text()
            except Exception as e:
                w = None
                print("!! 天气抓取失败: %s（其余上下文照常输出）" % str(e)[:80])
        render_ctx(weather=w)
    elif cmd == "check":
        cmd_check(args)
    else:
        print("!! 未知命令: %s" % cmd)
        print(__doc__)


if __name__ == "__main__":
    main()
