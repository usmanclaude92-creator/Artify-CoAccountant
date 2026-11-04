import { describe, expect, it } from "vitest";
import { ApiProblem, problemMessage } from "./client";

const errors = { unauthenticated: "Bad credentials", generic: "Something went wrong" };

describe("problemMessage", () => {
  it("maps known API error codes to localized text", () => {
    expect(problemMessage(new ApiProblem({ code: "unauthenticated" }, 401), errors)).toBe("Bad credentials");
  });
  it("falls back to the server's detail for unknown codes", () => {
    expect(problemMessage(new ApiProblem({ code: "novel", detail: "Explained." }, 400), errors)).toBe("Explained.");
  });
  it("uses a generic message for non-API errors", () => {
    expect(problemMessage(new Error("boom"), errors)).toBe("Something went wrong");
  });
});
