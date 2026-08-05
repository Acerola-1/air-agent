"""基于 Ollama 的 Vanna 适配器，提供 SQL 校验和可视化辅助能力."""

import logging
import re
from abc import ABC
from typing import Any, List, Tuple, Union

import pandas as pd
import sqlparse
from sqlparse.sql import Comparison, Identifier, IdentifierList, Where
from sqlparse.tokens import Keyword, Number, Operator, Punctuation, String
from vanna.ollama import Ollama

logger = logging.getLogger(__name__)


class VannaOllamaSqlAdapter(Ollama, ABC):
    """扩展 Vanna 的 Ollama 适配器，增加 SQL 提取和校验工具."""

    def __init__(self, config=None):
        """初始化适配器."""
        super().__init__(config)
        # Ollama.__init__ 没有调用 super().__init__()，手动初始化 VannaBase
        from vanna.base import VannaBase

        VannaBase.__init__(self, config=config)

    def extract_sql(self, llm_response: str) -> str:
        """从 LLM 响应中提取第一条 SQL 语句."""
        # 模型可能返回思考标签、Markdown 代码块或转义下划线；先归一化成纯文本。
        llm_response = re.sub(r"<think>.*?</think>", "", llm_response, flags=re.DOTALL)
        llm_response = llm_response.replace("\\_", "_")
        llm_response = llm_response.replace("\\", "")

        # 优先提取 ```sql 代码块，其次兜底提取 SELECT/WITH 语句。
        sql_block = re.search(
            r"```sql\n((.|\n)*?)(?=;|\[|```)", llm_response, re.DOTALL
        )
        select_with = re.search(
            r"(select|(with.*?as \())(.*?)(?=;|\[|```)",
            llm_response,
            re.IGNORECASE | re.DOTALL,
        )

        if sql_block:
            self.log(
                f"Output from LLM: {llm_response} \nExtracted SQL: {sql_block.group(1)}"
            )
            return sql_block.group(1).replace("```", "")

        if select_with:
            self.log(
                f"Output from LLM: {llm_response} \nExtracted SQL: {select_with.group(0)}"
            )
            return select_with.group(0)

        return llm_response

    def rename_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """重命名常见列名，便于展示."""
        rename_dict: dict[str, str] = {}
        for col in df.columns:
            lower_col = col.lower()
            if "region" in lower_col or "city" in lower_col:
                rename_dict[col] = "城市"
            elif "time" in lower_col:
                rename_dict[col] = "时间"
        return df.rename(columns=rename_dict)

    def check_expected_table_by_question(
        self, question: str, sql: str
    ) -> Tuple[bool, str, List[str]]:
        """校验问题对应的预期表名."""

        def has_any(text: str, keywords: set[str]) -> bool:
            """文本包含任一关键词时返回 True."""
            return any(kw in text for kw in keywords)

        q = question.lower().strip()
        sql_lower = sql.lower()

        hourly_keywords = {"时", "小时", "点"}
        daily_keywords = {"日", "号", "天"}
        accumulate_keywords = {"累计", "累积"}
        today_keywords = {"今"}

        is_hourly = has_any(q, hourly_keywords)
        is_daily = has_any(q, daily_keywords)
        has_accumulate = has_any(q, accumulate_keywords)
        has_today = has_any(q, today_keywords)

        # 根据用户时间粒度约束模型选择正确数仓表，避免小时/日/累计表混用。
        if is_hourly and not has_accumulate:
            expected_table = "dc_dwb_air_hourly_city_air_data"
        elif is_daily and not has_accumulate and not has_today:
            expected_table = "dc_dwb_air_daily_city_air_data"
        elif is_daily and has_accumulate:
            expected_table = "dc_dws_air_city_air_data_dt"
        elif has_today and not is_hourly and not has_accumulate:
            expected_table = "dc_dws_air_city_air_data_dt"
        else:
            return True, "", []

        # 从 FROM/JOIN 中提取实际访问表，支持 schema.table 和简单别名。
        table_pattern = (
            r"\b(?:from|join)\s+([a-z0-9_]+(?:\.[a-z0-9_]+)?)(?:\s+as\s+[a-z0-9_]+)?\b"
        )
        used_tables = list({m.group(1) for m in re.finditer(table_pattern, sql_lower)})
        logger.debug("Used tables in SQL: %s", used_tables)

        return expected_table in used_tables, expected_table, used_tables

    def validate_sql_syntax(self, sql: str) -> tuple[bool, str]:
        """校验基础 SELECT 查询的 SQL 语法."""
        try:
            parsed = sqlparse.parse(sql)
            if not parsed:
                return False, "SQL语法错误: 无法解析SQL语句"

            # 当前 Text2SQL 只允许 SELECT 查询，禁止写入、DDL 等破坏性语句。
            stmt = parsed[0]
            if stmt.get_type() != "SELECT":
                return False, "SQL语法错误: 不支持的语句类型"

            # sqlparse 对部分未闭合文本容错较强，这里补充基础成对符号检查。
            if sql.count("'") % 2 != 0:
                return False, "SQL语法错误: 未闭合的单引号"
            if sql.count('"') % 2 != 0:
                return False, "SQL语法错误: 未闭合的双引号"
            if sql.count("(") != sql.count(")"):
                return False, "SQL语法错误: 未闭合的括号"

            return True, "SQL语法正确"
        except sqlparse.exceptions.SQLParseError as e:  # type: ignore[attr-defined]
            return False, f"SQL语法错误: {str(e)}"
        except Exception as e:
            return False, f"SQL语法检查异常: {str(e)}"

    def determine_visualization_type(
        self,
        question: str | None = None,
        sql: str | None = None,
        df_metadata: str | None = None,
        df: pd.DataFrame | None = None,
        **kwargs,
    ) -> str:
        """为查询结果选择可视化类型."""
        data_features = []
        time_cols = []
        category_cols = []
        numeric_cols = []

        if df is not None:
            # 空结果或单值结果没有图表展示价值，直接返回 none。
            if df.empty:
                return "none"
            if len(df) > 25:
                logger.info("Result has more than 25 rows; forcing table output.")
                return "table"
            if len(df) == 1 or len(df.columns) == 1:
                return "none"

            # 基础特征
            data_features.append(f"▩ 数据规模：{len(df)}行{len(df.columns)}列")

            # 列类型分析
            type_counts = df.dtypes.value_counts().to_dict()
            type_analysis = [f"{k}({v}列)" for k, v in type_counts.items()]
            data_features.append(f"▩ 类型分布：{', '.join(type_analysis)}")

            # 内容特征提取
            content_features = []
            for col in df.columns:
                col_data = df[col]
                # 按列类型提取给模型看的数据指纹，减少图表类型判断的随机性。
                if self._is_time_like_column(col, col_data):
                    time_cols.append(col)
                    content_features.append(
                        f"{col}：时间类型（范围{col_data.min()}~{col_data.max()}）"
                    )
                elif pd.api.types.is_numeric_dtype(col_data):
                    numeric_cols.append(col)
                    stats = f"数值范围[{col_data.min():.2f}-{col_data.max():.2f}]"
                    if col_data.nunique() > 10:
                        stats += (
                            " 高方差" if col_data.std() > col_data.mean() else " 低方差"
                        )
                    content_features.append(f"{col}：{stats}")
                else:
                    unique_count = col_data.nunique()
                    if 2 <= unique_count <= 10:
                        category_cols.append((col, unique_count))
                    sample = "、".join(
                        col_data.dropna()
                        .sample(min(3, len(col_data)), replace=False)
                        .astype(str)
                    )
                    content_features.append(
                        f"{col}：{unique_count}种值（示例：{sample}）"
                    )

            try:
                summary = "▩ 内容摘要：\n" + "\n".join(
                    str(feat) for feat in content_features
                )
                data_features.append(summary)
            except Exception as e:
                data_features.append(f"▩ 内容摘要生成失败：{str(e)}")

        # 构建决策规则
        system_msg = """你是一个专业的可视化分析师，请根据用户提供的数据和问题描述，自主判断最适合的图表展示方式，直接返回以下关键字之一：
- `line`（折线图）
- `bar`（柱状图）
- `table`（表格）
- `none`（无需展示）

无需解释理由，仅返回关键词。
【强制规则】
1. 数据为空 → none
2. 单行单列 → none
"""
        #         system_msg = """作为数据分析专家，请按以下层级判断展示方式（直接返回小写关键字）：

        # 【强制规则】
        # 1. 数据为空 → none
        # 2. 单行单列 → none

        # 【图表类型判断】按优先级选择：
        # ● 折线图(line)：满足以下任一
        #    - 包含时间列 + 数值列
        #    - 问题含：趋势/变化/增长率
        #    - X 轴为连续型数据

        # ● 柱状图(bar)：满足以下任一
        #    - 分类列(2-10种) + 数值列
        #    - 问题含：比较/排名/分布
        #    - 需要显示离散数据对比

        # 【表格特征】(table)：不满足图表条件时，或者对于有多个时间点,多个因子的,返回table

        # 【默认】table"""

        # 构建上下文
        context = []
        # 把问题、SQL、样例数据和结构化特征压成短上下文，让模型只做类型分类。
        if question:
            context.append(f"● 问题：{question}")
        if sql:
            context.append(f"● SQL特征：{sql}")
        if df is not None and not df.empty:
            context.append(f"查询结果: {df.head(6)}")
        if df_metadata:
            context.append(f"● 元数据：{df_metadata}")
        if data_features:
            context.append("● 数据指纹：\n" + "\n".join(data_features))

        # 添加结构化特征
        feature_report = []
        if time_cols:
            feature_report.append(f"时间列：{', '.join(time_cols)}")
        if category_cols:
            cats = [f"{col}({count}种)" for col, count in category_cols]
            feature_report.append(f"分类列：{', '.join(cats)}")
        if numeric_cols:
            feature_report.append(f"数值列：{', '.join(numeric_cols)}")

        if feature_report:
            context.append("● 结构化特征：\n" + "\n".join(feature_report))

        deterministic_type = self._deterministic_visualization_type(
            df=df,
            time_cols=time_cols,
            category_cols=category_cols,
            numeric_cols=numeric_cols,
        )
        use_llm_fallback = bool(kwargs.pop("llm_fallback", False))
        if deterministic_type != "llm" or not use_llm_fallback:
            return "table" if deterministic_type == "llm" else deterministic_type

        system_msg = "数据分析任务：\n" + "\n".join(context) + "\n\n" + system_msg

        message_log = [
            self.system_message(system_msg),
            self.user_message("请严格按层级规则输出line/bar/table/none的其中一个"),
        ]
        logger.debug("message_log: %s", message_log)

        # 获取并清洗响应
        response = self.submit_prompt(message_log, kwargs=kwargs).strip().lower()

        # 智能匹配逻辑
        if any(k in response for k in ["none"]):
            return "none"
        if any(k in response for k in ["line"]):
            return "line"
        if any(k in response for k in ["bar"]):
            return "bar"
        if any(k in response for k in ["table"]):
            return "table"
        return "none"

    @staticmethod
    def _is_time_like_column(col: str, col_data: pd.Series) -> bool:
        """根据列名和 dtype 判断是否为时间列."""
        normalized = str(col).lower()
        if any(
            token in normalized
            for token in (
                "time",
                "date",
                "datetime",
                "hour_key",
                "day_key",
                "month_key",
                "year_key",
                "时间",
                "日期",
                "小时",
                "月份",
                "年份",
            )
        ):
            return True
        return bool(pd.api.types.is_datetime64_any_dtype(col_data))

    @staticmethod
    def _deterministic_visualization_type(
        *,
        df: pd.DataFrame | None,
        time_cols: list[str],
        category_cols: list[tuple[str, int]],
        numeric_cols: list[str],
    ) -> str:
        """用确定性规则选择图表类型，返回 llm 表示需要兜底."""
        if df is None or df.empty:
            return "none"
        if len(df) > 25:
            return "table"
        if len(df) == 1 or len(df.columns) == 1:
            return "none"
        if time_cols and numeric_cols:
            return "line"
        if category_cols and numeric_cols:
            return "bar"
        return "llm"

    def ask(
        self,
        question: Union[str, None] = None,
        print_results: bool = True,
        auto_train: bool = True,
        visualize: bool = True,  # 为 False 时不生成 plotly 代码
        allow_llm_to_see_data: bool = False,
        max_retries: int = 1,
    ) -> Any:  # type: ignore[override]
        """接收问题并返回 SQL、CSV 输出和可视化类型."""
        if question is None:
            question = input("Enter a question: ")

        retry_count = 0
        last_sql: str | None = None
        error_message = ""
        df = None

        while retry_count <= max_retries:
            try:
                # 首次直接生成 SQL；重试时把错误和上次 SQL 一并喂回模型修正。
                if retry_count == 0:
                    sql = self.generate_sql(
                        question=question, allow_llm_to_see_data=allow_llm_to_see_data
                    )
                else:
                    error_prompt = (
                        "Previous SQL execution error or empty result.\n"
                        f"Error info:\n{error_message}\n"
                        f"The last generated SQL was:\n{last_sql}\n"
                        f"Please generate a correct SQL for the question:\n{question}"
                    )
                    sql = self.generate_sql(
                        question=error_prompt,
                        allow_llm_to_see_data=allow_llm_to_see_data,
                    )

                last_sql = sql

                # 执行前先做表名和语法校验，尽量在查询数据库前拦截错误 SQL。
                is_valid, expected_table, used_tables = (
                    self.check_expected_table_by_question(question=question, sql=sql)
                )
                if not is_valid and expected_table:
                    logger.warning(
                        "Table validation failed. expected=%s used=%s",
                        expected_table,
                        used_tables,
                    )
                    raise ValueError(f"表名不正确，请使用表 {expected_table}")

                syntax_valid, syntax_msg = self.validate_sql_syntax(sql)
                if not syntax_valid:
                    raise ValueError(f"SQL语法错误: {syntax_msg}")

                if print_results:
                    try:
                        from IPython.display import (  # type: ignore[import]
                            Code,
                            display,
                        )

                        display(Code(sql))
                    except Exception:
                        logger.info("%s", sql)

                if self.run_sql_is_set is False:
                    logger.warning("Database is not connected; cannot run SQL query.")
                    return None if print_results else (sql, None, None)

                df = self.run_sql(sql)

                # 空结果通常代表 SQL 条件或表选择错误，允许模型带错误上下文重试。
                if print_results:
                    try:
                        from IPython.display import display  # type: ignore[import]

                        display(df)
                    except Exception:
                        logger.info("%s", df)

                if df.empty:
                    raise ValueError("SQL query returned empty result.")

                break
            except Exception as e:
                error_message = str(e)
                retry_count += 1

                if retry_count > max_retries:
                    logger.error(
                        "Failed after %s retries. Last error: %s",
                        max_retries,
                        error_message,
                    )
                    return None if print_results else (last_sql, None, None)

                logger.warning(
                    "SQL execution error: %s. Retrying to generate SQL.",
                    error_message,
                )

        if df is None:
            return None if print_results else (last_sql, None, None)

        if len(df) > 0 and auto_train:
            self.add_question_sql(question=question, sql=last_sql)  # type: ignore[arg-type]

        # 前端目前消费 CSV 文本和可视化类型；展示字段先做常见中文化。
        df_chinese = self.rename_columns(df)
        csv = df_chinese.to_csv(sep=";", index=False)

        if not visualize:
            return last_sql, csv, None

        try:
            visualization_type = self.determine_visualization_type(
                question=question,
                sql=last_sql,
                df_metadata=f"Running df.dtypes gives:\n {df.dtypes}",
                df=df,
            )
        except Exception as e:
            logger.exception("Couldn't determine visualization type: %s", e)
            return None if print_results else (last_sql, csv, None)

        return last_sql, csv, visualization_type

    @staticmethod
    def is_valid_chinese_region_name(name: str) -> bool:
        """名称看起来像合法中文行政区划时返回 True."""
        if not name:
            return False
        region_suffixes = [
            "省",
            "市",
            "县",
            "区",
            "镇",
            "乡",
            "街道",
            "旗",
            "自治区",
            "自治州",
            "盟",
        ]
        if not re.fullmatch(r"[\u4e00-\u9fff]+", name):
            return False
        if not any(name.endswith(suffix) for suffix in region_suffixes):
            return False
        if len(name) < 2:
            return False
        return True

    @classmethod
    def validate_chinese_region_names(cls, where_conditions, fields_to_check=None):
        """校验 WHERE 条件中的地区名字段，并返回 (ok, errors)."""
        if fields_to_check is None:
            fields_to_check = {"full_name", "region_name"}
        errors = []
        for cond in where_conditions:
            field = cond.get("field", "").lower()
            value = cond.get("value", "")
            if field in fields_to_check:
                if not cls.is_valid_chinese_region_name(str(value)):
                    errors.append(
                        f"字段 `{cond.get('field')}` 的值 `{value}` 不是合法的中文地区名,城市名不能有英文,且应以常见行政区划后缀结尾,请按合法中文地区名格式修改"
                    )
        return len(errors) == 0, errors

    def extract_from_tables(self, stmt):
        """从已解析的 SQL 语句中提取表名."""
        from_seen = False
        from_tables = []
        stop_keywords = ("WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "UNION")
        tokens = stmt.tokens
        idx = 0
        while idx < len(tokens):
            token = tokens[idx]
            # 找到 FROM 后开始收集表名，遇到 WHERE/GROUP 等子句边界停止。
            if token.is_keyword and token.normalized == "FROM":
                from_seen = True
                idx += 1
                continue
            if from_seen:
                if token.is_keyword and token.normalized in stop_keywords:
                    break
                if isinstance(token, IdentifierList):
                    for identifier in token.get_identifiers():
                        real_name = identifier.get_real_name()
                        if real_name:
                            from_tables.append(real_name)
                elif isinstance(token, Identifier):
                    real_name = token.get_real_name()
                    if real_name:
                        from_tables.append(real_name)
                elif token.is_keyword and token.normalized == "JOIN":
                    next_idx, next_token = stmt.token_next(idx)
                    if isinstance(next_token, Identifier):
                        real_name = next_token.get_real_name()
                        if real_name:
                            from_tables.append(real_name)
                        idx = next_idx
                elif token.is_keyword and token.normalized == "ON":
                    while idx < len(tokens):
                        token = tokens[idx]
                        if token.is_keyword and token.normalized in stop_keywords:
                            break
                        idx += 1
                    break
            idx += 1
        return from_tables

    def check_and_filter_select_fields(self, select_fields, from_tables):
        """按表级白名单校验 SELECT 字段."""
        whitelist_map = {
            "dc_dwb_air_daily_city_air_data": {
                "update_time",
                "region_key",
                "id",
                "day_key",
                "province_code",
                "storage_time",
                "pm25",
                "pm10",
                "so2",
                "no2",
                "o3",
                "co",
                "pm25_iaqi",
                "pm10_iaqi",
                "so2_iaqi",
                "no2_iaqi",
                "o3_iaqi",
                "co_iaqi",
                "o3_8h",
                "integrated_index",
                "aqi",
                "aqi_level",
                "pm25_level",
                "pm10_level",
                "so2_level",
                "no2_level",
                "o3_level",
                "co_level",
                "tvoc",
                "tsp",
                "max_pollution",
                "max_pollution_en",
                "aqi_category",
                "aqi_color",
                "o3_8h_level",
                "source",
                "tsp_level",
                "region_name",
                "parent_region_name",
            },
            "dc_dwb_air_hourly_city_air_data": {
                "update_time",
                "region_key",
                "id",
                "hour_key",
                "province_code",
                "storage_time",
                "pm25",
                "pm10",
                "so2",
                "no2",
                "o3",
                "co",
                "pm25_iaqi",
                "pm10_iaqi",
                "so2_iaqi",
                "no2_iaqi",
                "o3_iaqi",
                "co_iaqi",
                "o3_8h",
                "integrated_index",
                "aqi",
                "aqi_level",
                "pm25_level",
                "pm10_level",
                "so2_level",
                "no2_level",
                "o3_level",
                "co_level",
                "tvoc",
                "tsp",
                "max_pollution",
                "max_pollution_en",
                "aqi_category",
                "aqi_color",
                "o3_8h_level",
                "source",
                "o3_8h_iaqi",
                "tsp_level",
                "region_name",
                "parent_region_name",
            },
            "dc_dws_air_city_air_data_dt": {
                "year_key",
                "update_time",
                "region_key",
                "id",
                "hour_key",
                "province_code",
                "pm25",
                "pm10",
                "so2",
                "no2",
                "o3",
                "co",
                "pm25_iaqi",
                "pm10_iaqi",
                "so2_iaqi",
                "no2_iaqi",
                "o3_iaqi",
                "co_iaqi",
                "o3_8h",
                "integrated_index",
                "aqi",
                "aqi_level",
                "pm25_level",
                "pm10_level",
                "so2_level",
                "no2_level",
                "o3_level",
                "co_level",
                "tvoc",
                "tsp",
                "o3_8h_level",
                "max_pollution",
                "max_pollution_en",
                "aqi_category",
                "aqi_color",
                "o3_8h_iaqi",
                "tsp_level",
                "region_name",
                "parent_region_name",
                "source",
            },
        }
        from_tables_lower = [t.lower() for t in from_tables]
        matched_tables = []
        for table_name in whitelist_map:
            for ft in from_tables_lower:
                if table_name in ft:
                    matched_tables.append(table_name)
        if not matched_tables:
            # 未匹配任何白名单表，不做过滤
            return True, []

        # 只对已知空气质量表做字段白名单校验，未知表保持向后兼容放行。
        invalid_fields = []
        for field in select_fields:
            pure_field = field.split(".")[-1].lower()
            found_in_any = False
            for table in matched_tables:
                if pure_field in whitelist_map[table]:
                    found_in_any = True
                    break
            if not found_in_any:
                invalid_fields.append(field)
        return len(invalid_fields) == 0, invalid_fields

    @staticmethod
    def extract_value(token):
        """尽可能将值 token 解析为 Python 基础类型."""
        if token is None:
            return None
        if token.ttype in (String.Single, String.Symbol):
            return token.value.strip("'\"")
        elif token.ttype == Number:
            try:
                return int(token.value)
            except ValueError:
                try:
                    return float(token.value)
                except ValueError:
                    return token.value
        elif isinstance(token, Identifier):
            return token.get_real_name().split(".")[-1]
        else:
            return str(token.value)

    def extract_sql_components(self, sql: str) -> dict[str, Any]:
        """从 SQL 中提取 SELECT 字段、FROM 表和 WHERE 条件."""
        # 统一关键字和运算符空格，降低 sqlparse token 形态差异。
        sql = sqlparse.format(
            sql, reindent=False, keyword_case="upper", strip_comments=True
        )
        for op in ["<=", ">=", "<>", "!=", "<", ">", "="]:
            sql = sql.replace(op, f" {op} ")
        parsed = sqlparse.parse(sql)
        if not parsed:
            return {}

        stmt = parsed[0]  # type: ignore[index]
        components = {
            "select": [],
            "from": [],
            "where": [],
            "fields_valid": True,
            "fields_errors": [],
            "where_value_valid": True,
            "where_value_errors": [],
        }

        select_seen = False
        for token in stmt.tokens:
            # 提取 SELECT 字段，星号查询暂不纳入字段白名单校验。
            if token.is_keyword and token.normalized == "SELECT":
                select_seen = True
                continue
            if select_seen:
                if token.is_keyword and token.normalized in ("FROM", "WHERE"):
                    select_seen = False
                    continue
                if token.ttype == Operator and token.value == "*":
                    continue
                if isinstance(token, IdentifierList):
                    for identifier in token.get_identifiers():
                        if isinstance(identifier, Identifier):
                            components["select"].append(identifier.get_real_name())
                elif isinstance(token, Identifier):
                    components["select"].append(token.get_real_name())
                elif token.is_group:
                    for t in token.flatten():
                        if isinstance(t, Identifier):
                            components["select"].append(t.get_real_name())

        components["from"] = self.extract_from_tables(stmt)

        where_clause = next((t for t in stmt.tokens if isinstance(t, Where)), None)
        if where_clause:
            # 提取简单 WHERE 条件，主要用于中文地区名合法性校验。
            for token in where_clause.tokens:
                if isinstance(token, Comparison):
                    left = token.left
                    if isinstance(left, Identifier):
                        field = left.get_real_name().split(".")[-1]
                    else:
                        field = str(left)
                    next_tok = token.token_next(1)
                    op_token = next_tok[1] if next_tok else None
                    op = op_token.value.strip() if op_token else None
                    right = token.right
                    value = self.extract_value(right)
                    if field and op and value is not None:
                        components["where"].append(
                            {"field": field, "operator": op, "value": value}
                        )
                elif token.ttype == Keyword and token.value.upper() in ("AND", "OR"):
                    continue
                elif isinstance(token, Identifier):
                    field = token.get_real_name().split(".")[-1]
                    next_token = token.token_next(1)
                    if (
                        next_token
                        and hasattr(next_token[1], "ttype")
                        and next_token[1].ttype in (Operator, Punctuation)
                    ):  # type: ignore[optional-member]
                        op = next_token[1].value.strip()  # type: ignore[optional-member]
                        next_next = next_token[1].token_next(1)  # type: ignore[optional-member]
                        value_token = next_next[1] if next_next else None
                        value = self.extract_value(value_token)
                        components["where"].append(
                            {"field": field, "operator": op, "value": value}
                        )
                elif token.ttype == Operator and token.value in (
                    "<",
                    ">",
                    "=",
                    "<=",
                    ">=",
                    "!=",
                    "<>",
                ):
                    prev_token = token.token_prev(1)[1] if token.token_prev(1) else None
                    next_token = token.token_next(1)[1] if token.token_next(1) else None
                    if prev_token and isinstance(prev_token, Identifier):
                        field = prev_token.get_real_name().split(".")[-1]
                        op = token.value
                        value = self.extract_value(next_token) if next_token else None
                        if field and op and value is not None:
                            components["where"].append(
                                {"field": field, "operator": op, "value": value}
                            )

        is_valid, invalid_fields = self.check_and_filter_select_fields(
            components["select"], components["from"]
        )
        if not is_valid:
            components["fields_valid"] = False
            components["fields_errors"].append(
                f"sql包含非法字段: {', '.join(invalid_fields)}"
            )
        else:
            components["fields_valid"] = True

        passed, err_msgs = self.validate_chinese_region_names(components["where"])
        if not passed:
            components["where_value_valid"] = False
            components["where_value_errors"] = err_msgs
        else:
            components["where_value_valid"] = True
            components["where_value_errors"] = []

        return components
