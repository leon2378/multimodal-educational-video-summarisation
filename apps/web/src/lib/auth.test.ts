import { describe, expect, it } from "vitest";

import { authHeaders, setTokenSource } from "./auth";

describe("authHeaders", () => {
  it("sends no token without Clerk", async () => {
    expect(await authHeaders()).toEqual({});
  });

  it("sends the session token as a bearer token once there is one", async () => {
    setTokenSource(async () => "session-token");
    expect(await authHeaders()).toEqual({ Authorization: "Bearer session-token" });
  });

  it("sends none when signed out, or when getting the token fails", async () => {
    setTokenSource(async () => null);
    expect(await authHeaders()).toEqual({});
    setTokenSource(() => Promise.reject(new Error("offline")));
    expect(await authHeaders()).toEqual({});
  });
});
