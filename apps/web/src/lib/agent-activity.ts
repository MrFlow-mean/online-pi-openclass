const LEGACY_ACTIVITY_COPY: Record<string, string> = {
  "OpenClass 工作进展": "OpenClass progress",
  "OpenClass 正在思考": "OpenClass is thinking",
  "制定工作计划": "Preparing work plan",
  "运行命令": "Running command",
  "更新板书文档": "Updating board document",
  "调用工具": "Using tool",
  "协同处理": "Collaborating",
  "搜索网络": "Searching the web",
  "查看图片": "Viewing image",
  "生成图片": "Generating image",
  "整理上下文": "Organizing context",
  "OpenClass 已完成思考": "OpenClass finished thinking",
  "工作计划已更新": "Work plan updated",
  "命令执行完成": "Command completed",
  "板书文档已更新": "Board document updated",
  "工具调用完成": "Tool call completed",
  "协同处理完成": "Collaboration completed",
  "网络搜索完成": "Web search completed",
  "图片查看完成": "Image viewed",
  "图片生成完成": "Image generated",
  "上下文整理完成": "Context organized",
  "OpenClass 已连接模型": "OpenClass connected to the model",
  "OpenClass 已完成模型运行": "OpenClass completed the model run",
  "OpenClass 正在推理": "OpenClass is reasoning",
  "OpenClass 已完成推理": "OpenClass completed reasoning",
  "OpenClass 正在生成结果": "OpenClass is generating the result",
  "OpenClass 已生成模型结果": "OpenClass generated the model result",
  "OpenClass 模型运行未完成": "OpenClass model run did not complete",
  "模型连接中断，正在重试": "Model connection interrupted; retrying",
  "OpenClass 正在校验模型结果": "OpenClass is validating the model result",
  "模型结果需要结构修复": "Model result needs structural repair",
  "OpenClass 已校验模型结果": "OpenClass validated the model result",
  "OpenClass 正在处理当前步骤": "OpenClass is processing the current step",
  "OpenClass 已完成当前步骤": "OpenClass completed the current step",
  "OpenClass 当前步骤未完成": "OpenClass did not complete the current step",
  "用户已停止当前模型请求。": "The user stopped the current model request.",
  "模型进程返回失败状态。": "The model process returned a failure status.",
  "模型没有返回可用的助手结果。": "The model did not return a usable assistant result.",
  "首次结果未满足结构要求，正在请求模型修复。":
    "The first result did not match the required structure; requesting a repaired result.",
  "模型结果已通过结构校验。": "The model result passed schema validation.",
};

export function publicAgentActivityText(text: string): string {
  const branded = text.replace(/^(?:Codex|OpenAI)\b/i, "OpenClass");
  const fixedCopy = LEGACY_ACTIVITY_COPY[branded];
  if (fixedCopy) {
    return fixedCopy;
  }

  let match = branded.match(/^正在使用 (.+) 处理当前步骤。$/);
  if (match) {
    return `Using ${match[1]} to process the current step.`;
  }
  match = branded.match(/^(.+) 已返回本步骤结果。$/);
  if (match) {
    return `${match[1]} returned the result for this step.`;
  }
  match = branded.match(/^模型(推理|结果)阶段已完成(?:，共接收 (\d+) 个字符)?。$/);
  if (match) {
    const noun = match[1] === "推理" ? "reasoning" : "output";
    return `Model ${noun} completed${match[2] ? `; received ${match[2]} characters` : ""}.`;
  }
  match = branded.match(/^模型正在生成(推理|结果)(?:，已接收 (\d+) 个字符)?(；逐字私有思维不写入聊天记录)?。$/);
  if (match) {
    const noun = match[1] === "推理" ? "reasoning" : "output";
    const characterCount = match[2] ? `; received ${match[2]} characters` : "";
    const privacyNote = match[3] ? "; private chain-of-thought is not recorded in chat history" : "";
    return `The model is generating ${noun}${characterCount}${privacyNote}.`;
  }
  match = branded.match(/^模型请求在 (.+) 秒后超时。$/);
  if (match) {
    return `The model request timed out after ${match[1]} seconds.`;
  }
  match = branded.match(/^正在进行第 (\d+) 次模型请求。$/);
  if (match) {
    return `Starting model request attempt ${match[1]}.`;
  }
  match = branded.match(/^正在按照 (.+) 的结构要求检查结果。$/);
  if (match) {
    return `Checking the result against the ${match[1]} schema.`;
  }
  return branded;
}

export function publicAgentActivityLabel(label: string): string {
  return publicAgentActivityText(label);
}
