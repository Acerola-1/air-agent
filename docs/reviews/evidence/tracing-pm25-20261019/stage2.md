# 阶段二污染物扩展

- 画像：PM10、O3、NO2、SO2 使用 ug/m3；CO 使用 mg/m3。每类有独立 QC 范围、异常绝对下限和显示单位。
- O3：保留小时序列；`daily_max_8h` 按当地日每小时结束的 8h 窗口计算，至少 6/8 有效；跨日窗口标 `cross_day_8h_window`，部分窗口标 `partial_8h_window`；不输出 VOCs 敏感性或光化学贡献。
- PM2.5：24h 滑动只作补充统计，标 `pm25_24h_sliding_supplementary`，不是自然日均或法规评价。
- PM10/NO2/SO2/CO：未定义独立评价口径时记录 `evaluation_profile_not_defined`，不套用 PM2.5。
- 多污染物：不按不同单位原始浓度排序；首要污染物只接受平台字段或共享标准化依据；不同平均时段/标准化方法返回不可用及限制。
- 报告正文标题、画布标题、单位和评价段按 request 的污染物画像生成；阶段二测试断言非 PM2.5 污染物的画布标题不含 PM2.5。
- 测试：`test_stage2_pollutants.py` 10 passed；覆盖五类正常路径、CO 单位、O3 8h/跨日、PM2.5 24h 补充、多污染物不可比。
