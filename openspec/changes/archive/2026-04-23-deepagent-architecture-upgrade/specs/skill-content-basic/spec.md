## 新增需求

### 需求：air-quality-realtime Skill 总纲
系统 SHALL 在 `skills/basic/air-quality-realtime/SKILL.md` 创建实时空气质量查询 Skill 的总纲文件，YAML frontmatter 包含 name: air-quality-realtime，覆盖问题 1、2、4，allowed-tools 为 query_city_daily_air_data、query_city_realtime_air_data、query_multiple_cities_air_data、query_weather_data、get_current_time，metadata.requires_weather 为 true。

#### 场景： air-quality-realtime Skill 元数据加载
- **WHEN** basic-agent 的 SkillsMiddleware 加载 `skills/basic/` 目录
- **THEN** air-quality-realtime 的 name 和 description 出现在 system prompt 的 Skills 列表中，描述包含触发关键词："空气质量怎么样"、"实时查询"、"当前AQI"

### 需求：ranking-assessment Skill 总纲
系统 SHALL 在 `skills/basic/ranking-assessment/SKILL.md` 创建排名考核查询 Skill 的总纲文件，覆盖问题 6、8，allowed-tools 为 query_province_ranking、query_assessment_ranking、get_current_time，metadata.requires_weather 为 false。

#### 场景： ranking-assessment Skill 元数据加载
- **WHEN** basic-agent 的 SkillsMiddleware 加载 `skills/basic/` 目录
- **THEN** ranking-assessment 的 description 包含触发关键词："排名第几"、"排名"、"考核"、"PM2.5排名"、"县域考核"

### 需求：compliance-feasibility Skill 总纲
系统 SHALL 在 `skills/basic/compliance-feasibility/SKILL.md` 创建达标可行性研判 Skill 的总纲文件，覆盖问题 10、11，allowed-tools 为 query_compliance_control、query_good_day_feasibility、query_weather_data、get_current_time，metadata.requires_weather 为 true。

#### 场景： compliance-feasibility Skill 元数据加载
- **WHEN** basic-agent 的 SkillsMiddleware 加载 `skills/basic/` 目录
- **THEN** compliance-feasibility 的 description 包含触发关键词："能保良吗"、"保良"、"能避免重污染吗"、"规避重污染"、"达标可行性"

### 需求：comparison-composition Skill 总纲
系统 SHALL 在 `skills/basic/comparison-composition/SKILL.md` 创建同比占比分析 Skill 的总纲文件，覆盖问题 3、7，allowed-tools 为 query_index_composition、query_year_comparison、get_current_time，metadata.requires_weather 为 false。

#### 场景： comparison-composition Skill 元数据加载
- **WHEN** basic-agent 的 SkillsMiddleware 加载 `skills/basic/` 目录
- **THEN** comparison-composition 的 description 包含触发关键词："同比"、"较去年"、"改善没"、"占比"、"六因子"

### 需求：station-extreme Skill 总纲
系统 SHALL 在 `skills/basic/station-extreme/SKILL.md` 创建站点极值查询 Skill 的总纲文件，覆盖问题 12、13，allowed-tools 为 query_worst_station、query_polluted_stations、get_current_time，metadata.requires_weather 为 false。

#### 场景： station-extreme Skill 元数据加载
- **WHEN** basic-agent 的 SkillsMiddleware 加载 `skills/basic/` 目录
- **THEN** station-extreme 的 description 包含触发关键词："最差站点"、"哪个站点污染"、"轻度污染站点"

### 需求：regional-benchmark Skill 总纲
系统 SHALL 在 `skills/basic/regional-benchmark/SKILL.md` 创建区域对标统计 Skill 的总纲文件，覆盖问题 5、9，allowed-tools 为 query_regional_comparison、query_regional_statistics、get_current_time，metadata.requires_weather 为 false。

#### 场景： regional-benchmark Skill 元数据加载
- **WHEN** basic-agent 的 SkillsMiddleware 加载 `skills/basic/` 目录
- **THEN** regional-benchmark 的 description 包含触发关键词："哪个城市好"、"对标"、"有多少个城市保良"、"达标统计"

### 需求：trend-analysis Skill 总纲
系统 SHALL 在 `skills/basic/trend-analysis/SKILL.md` 创建趋势分析 Skill 的总纲文件，覆盖问题 14，allowed-tools 为 query_trend_analysis、query_weather_data、get_current_time，metadata.requires_weather 为 true。

#### 场景： trend-analysis Skill 元数据加载
- **WHEN** basic-agent 的 SkillsMiddleware 加载 `skills/basic/` 目录
- **THEN** trend-analysis 的 description 包含触发关键词："趋势"、"变化趋势"、"过去一周"、"PM2.5趋势"

### 需求：SKILL.md 正文模式选择指引段落
每个 SKILL.md 的 YAML frontmatter 之后的正文 SHALL 包含固定的模式选择指引段落和通用说明段落，引导 SubAgent 根据 mode 标记读取对应变体文件。

#### 场景： SKILL.md 正文结构合规
- **WHEN** 读取任意 basic 模块 SKILL.md 的正文内容
- **THEN** 正文包含"模式选择指引"段落（列出 fast 和 expert 两个模式及对应文件路径）和"通用说明"段落（权限校验和数据获取刚性流程）