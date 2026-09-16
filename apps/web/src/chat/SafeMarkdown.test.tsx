import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SafeMarkdown } from "./SafeMarkdown";

describe("SafeMarkdown", () => {
  it("renders report tables without enabling HTML or executable links", () => {
    render(
      <SafeMarkdown
        content={
          "| 指标 | 2025 |\n| --- | ---: |\n| **ROE** | 44.13% |\n| <img src=x onerror=alert(1)> | [危险](javascript:alert(1)) |"
        }
      />,
    );
    expect(screen.getByRole("table")).toBeVisible();
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(screen.getByRole("columnheader", { name: "2025" })).toBeVisible();
    expect(screen.getByRole("cell", { name: "44.13%" })).toBeVisible();
    expect(document.querySelector("img")).toBeNull();
    expect(screen.queryByRole("link", { name: "危险" })).toBeNull();
  });
  it("renders useful Markdown without interpreting raw HTML", () => {
    render(
      <SafeMarkdown
        content={'## 结论\n\n- **增长**稳定\n- 使用 `Runtime`\n\n<script>alert("x")</script>'}
      />,
    );

    expect(screen.getByRole("heading", { name: "结论" })).toBeVisible();
    expect(screen.getByText("增长")).toBeVisible();
    expect(screen.getByText("Runtime")).toBeVisible();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText(/<script>/)).toBeVisible();
  });

  it("allows web links and refuses executable link schemes", () => {
    render(
      <SafeMarkdown content="[可信来源](https://example.com/report) [危险链接](javascript:alert(1))" />,
    );

    expect(screen.getByRole("link", { name: "可信来源" })).toHaveAttribute(
      "href",
      "https://example.com/report",
    );
    expect(screen.queryByRole("link", { name: "危险链接" })).toBeNull();
    expect(screen.getByText("危险链接")).toHaveClass("markdown-invalid-link");
  });
});
