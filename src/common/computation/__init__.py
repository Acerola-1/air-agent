"""显式 LangGraph 的受限数据计算."""

COMPUTATION_SYSTEM_RULES = """
## 全量归档计算契约
结构化资料归档后，使用 compute_artifact 对问题范围完整计算，字段样本仅用于理解格式。
compute_artifact 的 code 参数必须是同步 JavaScript 函数体。它执行函数体，必须显式 return 一个包含 result 和非空 scope 的对象；最后一行表达式不会自动返回值。
data 是完整冻结 JSON。使用 helpers.scan(JSON_pointer, callback) 遍历实际记录数组，在同一回调中完成筛选与统计。
根数组的合法示例：let count=0; helpers.scan('', row => {count++;}); return {result:{count},scope:'完整根数组，无筛选'};
对象内数组应使用 manifest 给出的实际路径。按问题补充时间、站点、因子、过滤条件、单位及缺失值处理。
不得使用 Python、Markdown 代码围栏或异步代码；不得依赖 print 或最后一行变量输出；不能用样本或未成功计算的结果给出精确结论。
错误修正仅有一次机会，修正时同时核对 JavaScript 语法、完整扫描和 return {result,scope}，不要只改报错的一个字符。
""".strip()
