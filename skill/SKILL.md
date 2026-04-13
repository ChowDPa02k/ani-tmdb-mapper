---
name: ani-tmdb-mapper
description: "ANi RSS → TMDB 季集映射工具。当用户提到 ANi、动画映射、TMDB 季集、mapping.json、ani-tmdb-mapper 时触发。也适用于定时检查 ANi 新番映射的场景。"
---

# ANi → TMDB Season/Episode Mapper

自动化映射 ANi 动画标题到 TMDB 季集编号。

## 工作目录

```
ani-tmdb-mapper/   # 仓库根目录
```

## 完整工作流（按顺序执行）

### Step 1: 运行映射脚本

```bash
cd ani-tmdb-mapper
python3 ani_tmdb_mapper.py --no-cache-refresh
```

脚本会：
- 拉取 ANi RSS，解析当前番剧标题
- 与 confirmed.json 精确匹配，跳过已确认的
- 对未确认的：查 TMDB、查 ANi 历史目录
- 输出 `mapping_context.json`（结构化数据）和 `mapping_prompt.md`（人类可读）

### Step 2: 检查是否有新增未映射项

- 如果脚本输出 `🎉 All titles are already mapped!` → 直接进入 Step 5
- 如果有 `📋 N unmapped items found` → 继续 Step 3

### Step 3: 分析未映射标题（你就是 LLM）

读取 `mapping_context.json`，对每个未映射标题判断：

**判断规则：**
1. **S1 新番，TMDB 也是 S1** → 写 `{ "tmdb_id": N, "tmdb_season": 1 }` 到 confirmed.json
2. **多季且有连续编号** → 需要写 `episode_offset`（ANi_ep + offset = TMDB_ep）
   - 例：ANi S2 从 ep13 开始，TMDB S02E01 → offset = -12
3. **TMDB 只有1季但 ANi 拆成多季** → 写 `episode_offset` 累加
   - 例：Re:Zero TMDB S01=85ep, ANi S4=ep67 → offset = 66
4. **电影精确匹配** → 跳过（脚本已自动处理）
5. **TMDB 数据滞后**（ANi ep 数超出 TMDB 记录）→ 写备注 `_note`，标记观察
6. **中间季缺漏** → 正常现象（ANi 可能没买版权），只映射现有的
7. **Split-cour / 上下分割放送** → 使用以下辅助信息判断：
   - **episode_type**（TMDB 原生字段）：`standard` = 普通集，`mid_season` = 季中大结局(cour分界)，`finale` = 本季大结局
   - **air_date 对比**：将 ANi pub_date 与 TMDB episode air_date 对齐，找到最接近的集数
   - **弧线名称变化**：TMDB episode name 出现新弧线名时，可能是新 cour 的开始
   - **air_date 间隔**：TMDB 中相邻 episode 的 air_date 出现 >30 天间隔，通常是 cour 间歇期
   - 例：ANi "XXXX Part2 - 01"，TMDB S01 有 24 集，E12 标记 `mid_season`，E13 的 air_date 与 ANi pub_date 吻合 → 大概率 offset = -12

**tmdb_id 来源**：从 mapping_context.json 中获取，每个条目的 `tmdb.tmdb_id` 字段。

**边界条件（禁止映射）：**
- **总集篇/ recap**：如果 ANi 当前只有总集篇（如 "Season 2" 仅发布了第0集或总集回顾），**不要**写入 confirmed.json。等到该季第1集正式发布后再确认映射。总集篇的集数编号可能不代表最终季集结构。
- **仅预告/SP**：同理，特别篇、预告不计入正式季集映射。

**更新 confirmed.json：**
- 读取现有 confirmed.json
- 在 `mappings` 中添加新条目（key = 精确完整标题）
- 写回文件

### Step 4: 重新生成 mapping.json 并发布

```bash
# 重新生成 mapping.json
cd ani-tmdb-mapper
python3 -c "from ani_tmdb_mapper import ConfirmedMappingManager, generate_mapping_json; generate_mapping_json(ConfirmedMappingManager())"

# 发布（自动版本号、commit、tag、push、GitHub Release）
bash release.sh
```

版本号规则：`YYYY.SS.NNN`（SS = ANi 放送季度: 1=冬, 4=春, 7=夏, 10=秋；NNN 每季度从 001 递增）。

jsDelivr URL：
- **固定最新版**：`https://cdn.jsdelivr.net/gh/ChowDPa02k/ani-tmdb-mapper@main/mapping.json`
- 指定版本：`https://cdn.jsdelivr.net/gh/ChowDPa02k/ani-tmdb-mapper@{VERSION}/mapping.json`

### Step 5: 汇报结果

输出：
- 新增映射数（或 "无变化"）
- 新版本号和 jsDelivr URL
- 任何需要人工关注的项目（如 TMDB 数据滞后）

## 关键设计

- **confirmed.json**: 精确匹配（key = 完整 ANi 解析标题），无子串匹配
- **前向兼容**: S1 条目不会误匹配未来 S2 标题
- **增量迭代**: 每次只处理新增标题，已确认的直接跳过
- **mapping.json**: 从 confirmed.json 派生，剥离内部字段（_note 等），保留 tmdb_id
- **mappings_kubespider.json**: KubeSpider 格式，不含 tmdb_id，自动生成 custom_season_mapping + season_episode_adjustment

## ANi 目录缓存

```bash
# 全量刷新（首次或季度切换时）
python3 -c "from ani_tmdb_mapper import AniDirectoryCache; AniDirectoryCache().force_refresh_all()"

# 正常运行只刷新最近4个季度
python3 ani_tmdb_mapper.py
```

## 注意事项

- `.env` 包含 TMDB_API_KEY 和 HTTP_PROXY，不要泄露
- `ani_directory_cache.db` 是 SQLite 缓存，纳入 git 跟踪
- confirmed.json 的 key 必须与 RSS 解析出的标题**完全一致**
- 季度切换时可能有大量新番涌入，注意 Token 消耗
