import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("renders the product identity in the main content", () => {
    render(<App />);

    const main = screen.getByRole("main");
    const heading = within(main).getByRole("heading", {
      level: 1,
      name: "Industry Intelligence Platform",
    });

    expect(heading).toBeInTheDocument();
  });
});
