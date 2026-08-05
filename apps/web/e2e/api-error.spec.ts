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
    responseErrorMessage(response, "Chat service connection failed (HTTP 502), please try again later.")
  ).resolves.toBe("Chat service connection failed (HTTP 502), please try again later.");
  expect(
    userFacingApiErrorMessage(
      "Codex platform proxy request failed: Server error '502 Bad Gateway'",
      "Chat failed"
    )
  ).toBe("The model service connection failed, please try again later.");
  expect(userFacingApiErrorMessage("502 Bad Gateway", "Request failed")).toBe(
    "Request failed"
  );
});

test("structured and plain business errors remain visible", async () => {
  const response = new Response(
    JSON.stringify({ detail: "Data is still being processed" }),
    {
      status: 409,
      headers: { "content-type": "application/json" },
    }
  );

  await expect(responseErrorMessage(response, "Request failed")).resolves.toBe(
    "Data is still being processed"
  );
  expect(userFacingApiErrorMessage("The current course does not exist", "Request failed")).toBe(
    "The current course does not exist"
  );
});
