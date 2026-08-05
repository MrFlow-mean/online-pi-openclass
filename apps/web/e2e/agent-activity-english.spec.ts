import { expect, test } from "@playwright/test";

import {
  publicAgentActivityLabel,
  publicAgentActivityText,
} from "../src/lib/agent-activity";

const CJK_TEXT = /[\u3400-\u9fff]/;

test("legacy activity labels are rendered in English", () => {
  const labels = [
    "OpenClass 已完成当前步骤",
    "OpenClass 已完成模型运行",
    "OpenClass 已生成模型结果",
    "模型连接中断，正在重试",
    "工具调用完成",
  ].map(publicAgentActivityLabel);

  expect(labels).toEqual([
    "OpenClass completed the current step",
    "OpenClass completed the model run",
    "OpenClass generated the model result",
    "Model connection interrupted; retrying",
    "Tool call completed",
  ]);
  expect(labels.some((label) => CJK_TEXT.test(label))).toBe(false);
});

test("legacy activity details are rendered in English", () => {
  const details = [
    "openai_codex / gpt-5.6-sol 已返回本步骤结果。",
    "模型结果阶段已完成，共接收 219 个字符。",
    "正在按照 ExampleResult 的结构要求检查结果。",
  ].map(publicAgentActivityText);

  expect(details).toEqual([
    "openai_codex / gpt-5.6-sol returned the result for this step.",
    "Model output completed; received 219 characters.",
    "Checking the result against the ExampleResult schema.",
  ]);
  expect(details.some((detail) => CJK_TEXT.test(detail))).toBe(false);
});
