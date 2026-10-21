/**
 * 运营前端查询契约测试（Node 内置 test；Node ≥23.6 直接剥离 TS 类型）。
 * 覆盖：自定义业务日“包含结束日”→ 半开区间、预设窗口、指标状态与格式化。
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  customWindow,
  formatNumber,
  formatPercent,
  metricStatusLabel,
  metricValue,
  windowQuery,
} from "../lib/operations/query.ts";

test("自定义日期包含结束日并转为 Asia/Shanghai 半开区间", () => {
  const value = customWindow("2026-10-01", "2026-10-03");
  assert.equal(value.window, "custom");
  assert.equal(value.start, "2026-09-30T16:00:00.000Z");
  assert.equal(value.end, "2026-10-03T16:00:00.000Z");
});

test("预设窗口只发送 window", () => {
  assert.deepEqual(windowQuery("last_7_days", "", ""), { window: "last_7_days" });
  assert.deepEqual(windowQuery("today", "2026-10-01", "2026-10-03"), {
    window: "today",
  });
});

test("自定义窗口缺日期不静默回退", () => {
  assert.deepEqual(customWindow("", "2026-10-03"), { window: "custom" });
  assert.deepEqual(customWindow("2026-10-01", ""), { window: "custom" });
});

test("缺失指标不等于真实零", () => {
  assert.equal(metricValue({ value: 0, status: "measured" }), "0");
  assert.equal(metricValue({ value: null, status: "not_collected" }), "—");
  assert.equal(metricValue({ value: null, status: "unknown" }), "—");
  assert.equal(metricStatusLabel({ value: null, status: "partial" }), "部分缺失");
});

test("数字/百分比格式化稳定", () => {
  assert.equal(formatNumber(1234), "1,234");
  assert.equal(formatNumber(null), "—");
  assert.equal(formatPercent(0.5), "50.0%");
  assert.equal(formatPercent(null), "—");
});
