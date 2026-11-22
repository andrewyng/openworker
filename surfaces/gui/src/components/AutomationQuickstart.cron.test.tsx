// A schedule at midnight must survive the quickstart: cronFor parsed the hour with
// `Number(h) || 9`, and 0 is falsy, so picking 00:30 in the day × time picker wrote
// `30 9 * * *` — the automation silently moved from 00:30 to 09:30. The same bug the
// editor half has (#941, ScheduledView's fromCron); this is the quickstart half.
import { describe, expect, it } from "vitest";
import { cronFor } from "./AutomationQuickstart";

describe("cronFor", () => {
  it("keeps a midnight hour at midnight", () => {
    expect(cronFor("daily", "00:30")).toBe("30 0 * * *");
    expect(cronFor("daily", "00:00")).toBe("0 0 * * *");
    expect(cronFor("weekdays", "00:15")).toBe("15 0 * * 1-5");
  });

  it("keeps ordinary times unchanged", () => {
    expect(cronFor("daily", "09:00")).toBe("0 9 * * *");
    expect(cronFor("daily", "12:00")).toBe("0 12 * * *");
    expect(cronFor("weekdays", "17:30")).toBe("30 17 * * 1-5");
    expect(cronFor("sat", "08:00")).toBe("0 8 * * 6");
  });

  it("falls back to 09:00 only when the time does not parse", () => {
    expect(cronFor("daily", "")).toBe("0 9 * * *");
    expect(cronFor("daily", "xx")).toBe("0 9 * * *");
    expect(cronFor("daily", "09")).toBe("0 9 * * *"); // no minute part
  });

  it("clamps out-of-range values instead of emitting an invalid cron", () => {
    expect(cronFor("daily", "25:99")).toBe("59 23 * * *");
    expect(cronFor("daily", "-1:-5")).toBe("0 0 * * *");
  });
});
