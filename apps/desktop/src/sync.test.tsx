import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { createMockBridge, type MockOptions } from "./bridge/mock";
import type { SyncConflict } from "./bridge/types";

const conflict = (over: Partial<SyncConflict> = {}): SyncConflict => ({
  id: "cf-1", item_kind: "document", item_id: "d-1", client_id: "laptop", conflict_type: "stale", base_revision: 3, server_revision: 4, local_summary: {}, server_summary: {},
  state: "open", resolution: null, resolution_note: null, detected_at: "2026-03-02T10:00:00Z", resolved_at: null, ...over,
});

async function boot(opts: MockOptions = {}) {
  const bridge = createMockBridge({ restore: true, ...opts });
  const user = userEvent.setup();
  render(<App bridge={bridge} />);
  await user.click(await screen.findByRole("button", { name: "Open Alpha Trading LLC" }));
  await screen.findByRole("heading", { name: "Workspace", level: 1 });
  await user.click(await screen.findByRole("button", { name: /^Synchronization/ }));
  await screen.findByRole("heading", { name: "Synchronization", level: 1 });
  return { bridge, user };
}
const panel = () => screen.findByRole("region", { name: "Cloud synchronization" });

describe("Cloud synchronization (Phase 12)", () => {
  it("is not 'Synchronized' until the server has been asked and confirms nothing is open", async () => {
    const { user } = await boot();
    const p = within(await panel());
    expect(await p.findByText("Pending")).toBeInTheDocument();
    expect(p.getByText("Not yet checked against the server.")).toBeInTheDocument();
    await user.click(p.getByRole("button", { name: "Check with the server" }));
    expect(await p.findByText("Synchronized")).toBeInTheDocument();
    expect(p.getByText(/Checked with the server: 3 change/)).toBeInTheDocument();
  });

  it("shows server-counted conflicts and records a decision that never claims to change the record", async () => {
    const { bridge, user } = await boot({ syncConflicts: [conflict()] });
    const p = within(await panel());
    expect(await p.findByText("Conflict detected")).toBeInTheDocument();
    const list = await p.findByRole("list", { name: "Open conflicts" });
    expect(within(list).getByText(/the server has a newer version/)).toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: "Apply my change on the new version" }));
    expect(await p.findByText(/send your change on top of server revision 4/i)).toBeInTheDocument();
    expect(bridge.calls).toContain("cloudSync:conflict_resolve");
    await waitFor(() => expect(p.queryByRole("list", { name: "Open conflicts" })).not.toBeInTheDocument());
  });

  it("offers only 'keep the server copy' and 'discard' for a locked record", async () => {
    await boot({ syncConflicts: [conflict({ id: "cf-2", item_kind: "transaction", conflict_type: "locked" })] });
    const list = await within(await panel()).findByRole("list", { name: "Open conflicts" });
    expect(within(list).getByText(/this record is locked on the server/)).toBeInTheDocument();
    expect(within(list).getByRole("button", { name: "Keep the server copy" })).toBeInTheDocument();
    expect(within(list).queryByRole("button", { name: /Apply my change/ })).not.toBeInTheDocument();
  });

  it("says 'not confirmed' instead of 'Synchronized' when the server cannot be asked", async () => {
    const { user } = await boot({ syncStatusUnavailable: true });
    const p = within(await panel());
    await user.click(await p.findByRole("button", { name: "Check with the server" }));
    expect(await p.findByText("The server could not confirm the state.")).toBeInTheDocument();
    expect(p.getByText("Not confirmed")).toBeInTheDocument();
    expect(p.queryByText("Synchronized")).not.toBeInTheDocument();
  });
});
