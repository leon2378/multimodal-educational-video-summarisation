import { describe, expect, it } from "vitest";

import { ApiError, errorFrom, retryAfterSeconds, unwrap } from "./api";

describe("retryAfterSeconds", () => {
  it("reads seconds or a date", () => {
    const now = Date.parse("2026-09-30T12:00:00Z");
    const at = (value: string) => retryAfterSeconds(new Response(null, { headers: { "Retry-After": value } }), now);
    expect(at("30")).toBe(30);
    expect(at("Wed, 30 Sep 2026 12:05:00 GMT")).toBe(300);
    expect(at("soon")).toBeNull();
    expect(retryAfterSeconds(new Response(null))).toBeNull();
  });
});

describe("errorFrom", () => {
  it("keeps the API's message and status", () => {
    const error = errorFrom(new Response(null, { status: 403 }), { detail: "Only its owner can change this lecture." });
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 403, message: "Only its owner can change this lecture.", retryAfterS: null });
  });

  it("falls back to the status when there's no message", () => {
    expect(errorFrom(new Response(null, { status: 502 }), null).message).toBe("HTTP 502");
    // Validation errors carry a list, not a message.
    expect(errorFrom(new Response(null, { status: 422 }), { detail: [{ msg: "x" }] }).message).toBe("HTTP 422");
  });
});

describe("unwrap", () => {
  it("returns the data or throws the refusal", () => {
    expect(unwrap({ data: { ok: 1 }, response: new Response() })).toEqual({ ok: 1 });
    const refused = { error: { detail: "Sign in to do this." }, response: new Response(null, { status: 401 }) };
    expect(() => unwrap(refused)).toThrow("Sign in to do this.");
  });
});
