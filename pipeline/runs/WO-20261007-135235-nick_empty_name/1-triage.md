# ① agent0 定界

- 确认问题：True
- 边界：nick 表中存在两条归一化后为空的名称，导致 _row_sid 子串匹配污染，进而引发红点行身份解析失败

## 证据
- **soul_memory.db.nick: uid=499192198**：nick 表中 uid=499192198 的归一化名称为空
- **soul_memory.db.nick: uid=134486858**：nick 表中 uid=134486858 的归一化名称为空
- **E:/soul/agentM/monitoring_rules/nick_empty_name.py:15**：监控规则中明确指出 nick 表中归一化后为空的名称会污染 _row_sid 子串匹配

## 同源风险
- 红点行身份解析失败
- 子串匹配污染
- 数据一致性风险

## 修复边界
- 修：修复 nick 表中归一化后为空的名称，确保其不污染 _row_sid 子串匹配
- 不修：不修改其他表或逻辑，不涉及身份解析模块的其他部分