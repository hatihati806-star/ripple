import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Leaderboard } from "./Leaderboard";
import type { WaterBody } from "../domain/types";

function body(overrides: Partial<WaterBody> & Pick<WaterBody, "id">): WaterBody {
  return {
    name: overrides.id,
    kind: "lake",
    lat: 45,
    lon: -80,
    area_km2: 500,
    curated: true,
    series: [],
    rain_mm: [],
    latest_risk: 0.5,
    latest_ndci: 0.05,
    latest_ndti: -0.2,
    observed_frames: 4,
    total_frames: 11,
    ...overrides,
  };
}

const noop = () => {};

describe("Leaderboard", () => {
  it("renders the ranked mean and the peak beside it", () => {
    render(
      <Leaderboard
        bodies={[body({ id: "l", latest_risk: 0.42, latest_peak: 0.91, latest_cells: 400 })]}
        selectedId={null}
        onSelect={noop}
        onClose={noop}
      />,
    );
    const row = screen.getAllByRole("listitem")[0];
    expect(within(row).getByText("0.42")).toBeInTheDocument();
    expect(within(row).getByText("peak 0.91")).toBeInTheDocument();
    expect(within(row).getByText("l")).toBeInTheDocument();
  });

  it("renders a demoted body below the ranked ones with its flag", () => {
    const ranked = body({ id: "ranked", name: "Ranked Lake", latest_risk: 0.31 });
    const demoted = body({
      id: "georgian-bay",
      name: "Georgian Bay",
      latest_risk: 0.493,
      latest_peak: 1.0,
      latest_cells: 2743,
      latest_cell_fraction: 0.968,
    });
    render(
      <Leaderboard bodies={[demoted, ranked]} selectedId={null} onSelect={noop} onClose={noop} />,
    );

    const rows = screen.getAllByRole("listitem");
    expect(within(rows[0]).getByText("Ranked Lake")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Georgian Bay")).toBeInTheDocument();
    expect(within(rows[1]).getByText(/Peak-driven/)).toBeInTheDocument();
    expect(within(rows[1]).getByText(/listed after the ranked readings/)).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 readings are flagged/)).toBeInTheDocument();
  });

  it("shows a caution from the pipeline without inventing a peak problem", () => {
    const salt = body({
      id: "great-salt-lake",
      name: "Great Salt Lake",
      latest_risk: 0.97,
      latest_peak: 1.0,
      latest_cells: 610,
      latest_cell_fraction: 0.993,
      caution: "Hypersaline: mineral and halophilic colour dominates the spectrum",
    });
    render(<Leaderboard bodies={[salt]} selectedId={null} onSelect={noop} onClose={noop} />);
    expect(screen.getByText(/Index caution/)).toBeInTheDocument();
    expect(screen.getByText(/Hypersaline/)).toBeInTheDocument();
  });

  it("selects a row when clicked", () => {
    const onSelect = vi.fn();
    render(
      <Leaderboard bodies={[body({ id: "lake-x", name: "Lake X" })]} selectedId={null}
        onSelect={onSelect} onClose={noop} />,
    );
    screen.getByRole("button", { name: /Lake X/ }).click();
    expect(onSelect).toHaveBeenCalledWith("lake-x");
  });

  it("states the ranking basis in the header", () => {
    render(
      <Leaderboard bodies={[body({ id: "a" })]} selectedId={null} onSelect={noop}
        onClose={noop} />,
    );
    expect(screen.getByText(/mean over its own water/)).toBeInTheDocument();
  });
});
