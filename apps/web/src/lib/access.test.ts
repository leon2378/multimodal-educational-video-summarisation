import { describe, expect, it } from "vitest";

import { allowance, canChange, refusal, resetText, waitText } from "./access";
import { ApiError, type Me } from "./api";

const quotas = {
  questions_today: 4,
  questions_per_day: 30,
  questions_per_minute: 5,
  uploads_today: 3,
  uploads_per_day: 3,
  upload_bytes: 1_000_000_000,
  paused: false,
  resets_at: "2026-10-01T00:00:00Z",
};
const user = { id: "u1", email: null, name: null };
const signedIn: Me = { auth: true, signed_in: true, admin: false, user, quotas };
const admin: Me = { auth: true, signed_in: true, admin: true, user, quotas: null };
const visitor: Me = { auth: true, signed_in: false, admin: false, user: null, quotas: null };
// Sign-in off at the API: one local user, who is an admin.
const local: Me = { auth: false, signed_in: true, admin: true, user: null, quotas: null };

describe("canChange", () => {
  it("lets owners and admins change things, and nobody else", () => {
    expect(canChange(signedIn, { owner_id: "u1" })).toBe(true);
    expect(canChange(signedIn, { owner_id: "u2" })).toBe(false);
    expect(canChange(signedIn, { owner_id: null })).toBe(false);
    expect(canChange(admin, { owner_id: "u2" })).toBe(true);
    expect(canChange(visitor, { owner_id: null })).toBe(false);
    expect(canChange(undefined, { owner_id: "u1" })).toBe(false);
  });

  it("allows everything with sign-in off", () => {
    expect(canChange(local, { owner_id: null })).toBe(true);
  });
});

describe("allowance", () => {
  it("counts what's left today, never below zero", () => {
    expect(allowance(quotas)).toEqual({ questions: 26, uploads: 0 });
    expect(allowance({ ...quotas, uploads_today: 5 }).uploads).toBe(0);
  });
});

describe("resetText", () => {
  it("gives the time and how long until then", () => {
    const now = new Date("2026-09-30T19:00:00Z");
    expect(resetText("2026-10-01T00:00:00Z", now, "UTC")).toBe("00:00, in 5 h");
    expect(resetText("2026-10-01T00:00:00Z", now, "Australia/Sydney")).toBe("10:00, in 5 h");
    expect(resetText("2026-09-30T18:00:00Z", now, "UTC")).toBe("18:00");
  });
});

describe("waitText and refusal", () => {
  it("adds a short wait to a limit's message", () => {
    const minute = new ApiError("That's 5 questions in a minute, the most allowed. Wait a moment.", 429, 42);
    expect(waitText(minute)).toBe("Try again in 42 s.");
    expect(refusal(minute)).toBe("That's 5 questions in a minute, the most allowed. Wait a moment. Try again in 42 s.");
  });

  it("leaves long waits and other errors as they are", () => {
    const day = new ApiError("You've asked 30 questions today, the most a day. More at 00:00 UTC.", 429, 18_000);
    expect(waitText(day)).toBeNull();
    expect(refusal(day)).toBe(day.message);
    expect(refusal(new ApiError("Sign in to do this.", 401))).toBe("Sign in to do this.");
    expect(refusal(new Error("offline"))).toBe("offline");
  });
});
