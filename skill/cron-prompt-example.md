# Cron Prompt 示例

用于定时自动化（如 WorkBuddy Automation / crontab）的 prompt 模板。

## 推荐配置

- **频率**: 每周一、四 10:00
- **Schedule**: `FREQ=WEEKLY;BYDAY=MO,TH;BYHOUR=10;BYMINUTE=0`

## Prompt

```
执行 ani-tmdb-mapper skill 的完整工作流：

1. 运行映射脚本（--no-cache-refresh）
2. 如果全部已映射，输出"无变化"并结束
3. 如果有未映射项，分析 mapping_context.json，按 SKILL.md 判断规则处理
4. 更新 confirmed.json，重新生成 mapping.json 和 mappings_kubespider.json
5. 如有变动，执行 release.sh 发布新版本
6. 汇报结果

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
