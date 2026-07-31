import { expect, test } from "@playwright/test";

import {
  responseErrorMessage,
  userFacingApiErrorMessage,
} from "../src/lib/api";

test("gateway HTML is replaced with a concise transport error", async () => {
  const response = new Response(
    "<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>",
    {
      status: 502,
      headers: { "content-type": "text/html" },
    }
  );

  await expect(
    responseErrorMessage(response, "聊天服务连接失败（HTTP 502），请稍后重试。")
  ).resolves.toBe("聊天服务连接失败（HTTP 502），请稍后重试。");
  expect(
    userFacingApiErrorMessage(
      "Codex platform proxy request failed: Server error '502 Bad Gateway'",
      "聊天失败"
    )
  ).toBe("模型服务连接失败，请稍后重试。");
  expect(userFacingApiErrorMessage("502 Bad Gateway", "请求失败")).toBe(
    "请求失败"
  );
});

test("structured and plain business errors remain visible", async () => {
  const response = new Response(
    JSON.stringify({ detail: "资料仍在处理中" }),
    {
      status: 409,
      headers: { "content-type": "application/json" },
    }
  );

  await expect(responseErrorMessage(response, "请求失败")).resolves.toBe(
    "资料仍在处理中"
  );
  expect(userFacingApiErrorMessage("当前课程不存在", "请求失败")).toBe(
    "当前课程不存在"
  );
});
