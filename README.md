# ANi → TMDB Mapper

将 ANi 发布的动画文件名自动映射到 TMDB (The Movie Database) 的季度和集号。

## 为什么需要这个工具？

ANi 和 TMDB 对同一部动画的「季/集」划分经常不一致：

- **连续编号**: ANi 将第二季从 ep13 开始计，但 TMDB 各季独立编号（ep1 起）
- **季名差异**: ANi 标记「第四季」，TMDB 合并为单一季度
- **X.5 集**: ANi 的 ep12.5 应映射到 TMDB 的特别篇 (S00)
- **中间季缺失**: ANi 可能未获得某些季的授权（正常现象）

## 接入方式

https://cdn.jsdelivr.net/gh/ChowDPa02k/ani-tmdb-mapper@main/mapping.json

## 工作流程

```
1. 抓取 ANi RSS → 解析当前发布的文件名
2. 查询 TMDB API → 获取剧集的季结构
3. 读取 confirmed.json → 跳过已确认的映射
4. 查询 ANi 历史目录缓存 → 获取每季的实际集号范围
5. 对未映射的标题生成 LLM prompt → 推理映射关系
6. 人工确认后写入 confirmed.json → 重新运行生成 mapping.json
```

## 安装使用

```bash
# 纯 Python，无额外依赖
python ani_tmdb_mapper.py                    # 完整运行
python ani_tmdb_mapper.py --dry-run          # 仅解析 RSS，不查 TMDB
python ani_tmdb_mapper.py --refresh-cache    # 强制刷新 ANi 目录缓存
python ani_tmdb_mapper.py --no-cache-refresh # 跳过缓存刷新
```

## 配置

创建 `.env` 文件：

```env
TMDB_API_KEY=your_api_key_here
# HTTP_PROXY=http://127.0.0.1:7890   # 可选
```

TMDB API Key 申请: https://www.themoviedb.org/settings/api

## 文件说明

| 文件 | 说明 | Git |
|------|------|-----|
| `ani_tmdb_mapper.py` | 主脚本 | ✅ |
| `confirmed.json` | 已确认的映射（含 tmdb_id、季、偏移） | ✅ |
| `mapping.json` | 最终输出，供下游使用（含 tmdb_id） | ✅ |
| `mappings_kubespider.json` | KubeSpider 格式输出（不含 tmdb_id） | ✅ |
| `ani_directory_cache.db` | ANi 目录结构 SQLite 缓存 | ✅ |
| `.env` | API Key 和代理配置 | ❌ |
| `*_context.json` | LLM 上下文数据（临时） | ❌ |
| `*_prompt.md` | LLM 提示词（临时） | ❌ |

## 映射格式 (mapping.json)

```json
{
  "mappings": {
    "EXACT_ANi_TITLE": {
      "tmdb_id": 12345,
      "tmdb_season": 1
    },
    "EXACT_ANi_TITLE Season 2": {
      "tmdb_id": 12345,
      "tmdb_season": 2,
      "episode_offset": -12,
      "ani_category": "1781832f-1e7f-52ec-9df4-8646a9dfe12b"
    }
  }
}
```

每个条目包含：
- **tmdb_id**: TMDB 电视剧 ID（可用于直接查询 TMDB API）
- **tmdb_season**: 对应的 TMDB 季号
- **episode_offset**（可选）: `ANi_集号 + offset = TMDB_集号`
- **ani_category**（可选）: ANi 系列 UUID，仅 2026-07+ 新番携带

## ANi Category UUID 支持

ANi RSS 自 2026-07 季度起新增 `<category>` 字段，作为新番系列的唯一标识（UUID）：

- **不同季度视为独立系列**：同一部作品的 S1 和 S2 携带不同的 UUID
- **季度内番名变动 UUID 不变**：ANi 中途改名时，UUID 保持稳定，可继续匹配已有映射
- **仅作用于 2026-07 及之后开播的番剧**：旧番无此字段，继续使用标题精确匹配

### 匹配优先级

```
1. category UUID 匹配（仅新番有）→ 命中则返回映射
2. 标题精确匹配（传统方式）→ 回退方案
```

旧番剧完全不受影响，向后兼容。当新番在季度中途改名时，category UUID 保证映射不丢失。

## ANi 目录缓存策略

- SQLite 缓存 ANi 完整目录结构
- 默认只刷新最近 4 个季度目录（约 1 年）
- 更旧的目录使用缓存，减少服务器压力和反爬虫风险
- `--refresh-cache` 强制全量刷新

## 实战案例

- [KubeSpider + ANi 源提供者配置指南](https://github.com/ChowDPa02k/kubespider/blob/main/docs/zh/user_guide/ani_source_provider/README.md) — 将 mapping.json 集成到 KubeSpider 自动追番流程的完整教程

## License

MIT
