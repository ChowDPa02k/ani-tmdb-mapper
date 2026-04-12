# Cron Prompt 示例

用于定时自动化（如 WorkBuddy Automation / crontab）的 prompt 模板。

## 推荐配置

- **频率**: 每周一、四 10:00
- **Schedule**: `FREQ=WEEKLY;BYDAY=MO,TH;BYHOUR=10;BYMINUTE=0`

## Prompt

```
加载 ani-tmdb-mapper skill，按 SKILL.md 的完整工作流执行：
1. cd 到 /Users/zhoudingpeng/Appdata/openclaw-data/workspace/ani-tmdb-mapper，运行 python3 ani_tmdb_mapper.py --no-cache-refresh
2. 如果所有标题已映射（输出包含 "All titles are already mapped"），直接结束，不发布
3. 如果有未映射标题：读取 mapping_context.json，分析每个标题的 TMDB 季集映射，更新 confirmed.json
4. 重新生成 mapping.json 和 mappings_kubespider.json：
   python3 -c "from ani_tmdb_mapper import ConfirmedMappingManager, generate_mapping_json, generate_kubespider_json; mgr = ConfirmedMappingManager(); generate_mapping_json(mgr); generate_kubespider_json(mgr)"
5. 运行 bash release.sh 发布新版本
6. 简要汇报结果

注意：
- 总集篇/recap/SP 不做映射，等正式剧集发布后再确认
- 不要泄露 .env 中的 API Key
```

## 其他可用的 Cron 表达式

| 场景 | RRULE |
|------|-------|
| 每周一、四 10:00 | `FREQ=WEEKLY;BYDAY=MO,TH;BYHOUR=10;BYMINUTE=0` |
| 每天 09:00 | `FREQ=DAILY;BYHOUR=9;BYMINUTE=0` |
| 每季度首日 10:00 | 手动触发（新番季切换时建议全量刷新缓存） |
