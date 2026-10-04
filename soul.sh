#!/usr/bin/env bash
# 无黑窗调用 Soul 脚本（2026-09-29）
#
# 背景：用户反馈「电脑总是跳黑色命令窗口」。
# 根因：soul 是一步一进程架构，一轮里要起几十次 python.exe；
#       python.exe 是控制台子系统程序，每次启动 Windows 都会分配一个 conhost 窗口 → 黑窗闪一下。
# 修法：换成 pythonw.exe（GUI 子系统，不分配控制台）+ 输出重定向到临时文件再回显。
#       已实测：pythonw 在 Git Bash 下**仍会同步等待**（3.1s 测试通过），输出重定向正常。
#
# 用法：  bash soul.sh soul_auto.py
#         bash soul.sh soul_reply.py "昵称" "内容"
PYW="C:/Users/JIAN/.workbuddy/binaries/python/envs/soulocr/Scripts/pythonw.exe"
cd "$(dirname "$0")" || exit 1

LOG="_soulrun_$$.log"
"$PYW" "$@" > "$LOG" 2>&1
rc=$?
cat "$LOG"
rm -f "$LOG"
exit $rc
