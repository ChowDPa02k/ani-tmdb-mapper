# ANi → TMDB Mapper

将 ANi 发布的动画文件名自动映射到 TMDB (The Movie Database) 的季度和集号。

## 为什么需要这个工具？

ANi 和 TMDB 对同一部动画的「季/集」划分经常不一致：

- **连续编号**: ANi 将第二季从 ep13 开始计，但 TMDB 各季独立编号（ep1 起）
- **季名差异**: ANi 标记「第四季」，TMDB 合并为单一季度
- **X.5 集**: ANi 的 ep12.5 应映射到 TMDB 的特别篇 (S00)
- **中间季缺失**: ANi 可能未获得某些季的授权（正常现象）

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
| `confirmed.json` | 已确认的映射（跳过列表） | ✅ |
| `mapping.json` | 最终输出，供下游使用 | ✅ |
| `ani_directory_cache.db` | ANi 目录结构 SQLite 缓存 | ✅ |
| `.env` | API Key 和代理配置 | ❌ |
| `*_context.json` | LLM 上下文数据（临时） | ❌ |
| `*_prompt.md` | LLM 提示词（临时） | ❌ |

## 映射格式 (mapping.json)

```json
{
  "custom_season_mapping": {
    "ANi 标题关键词": {
      "season": 1,
      "reserve_keywords": "用于文件名的基础标题",
      "episode_offset": 66
    }
  },
  "season_episode_adjustment": {
    "动画基础名": {
      "2": -24,
      "3": -48
    }
  }
}
```

### custom_season_mapping

当 ANi 认为的「季」与 TMDB 不同时使用。

- **episode_offset**: `ANi_集号 + offset = TMDB_集号`
- 例: Re:Zero 第四季 ep01 → S01E67 (offset=+66)

### season_episode_adjustment

当 ANi 使用连续编号但 TMDB 各季独立编号时。

- 偏移为负数: `ANi_集号 + offset = TMDB_集号`
- 例: 史莱姆第二季 ep25 → S02E01 (offset=-24)

## ANi 目录缓存策略

- SQLite 缓存 ANi 完整目录结构
- 默认只刷新最近 4 个季度目录（约 1 年）
- 更旧的目录使用缓存，减少服务器压力和反爬虫风险
- `--refresh-cache` 强制全量刷新

## License

MIT
