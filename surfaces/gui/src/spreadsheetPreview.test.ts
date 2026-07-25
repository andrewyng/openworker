import { describe, expect, it } from "vitest";
import * as XLSX from "xlsx";

function roundTrip(bookType: "xlsx" | "biff8") {
  const workbook = XLSX.utils.book_new();
  const sheet = XLSX.utils.aoa_to_sheet([
    ["Project", "Score"],
    ["OpenWorker", 84],
  ]);
  XLSX.utils.book_append_sheet(workbook, sheet, "Evidence");
  const bytes = XLSX.write(workbook, { type: "buffer", bookType });
  const parsed = XLSX.read(bytes, { type: "buffer" });
  return XLSX.utils.sheet_to_json(parsed.Sheets.Evidence, {
    header: 1,
    defval: "",
  });
}

describe("spreadsheet preview dependency", () => {
  it("uses the reviewed SheetJS release and preserves xlsx/xls preview support", () => {
    expect(XLSX.version).toBe("0.20.3");
    expect(roundTrip("xlsx")).toEqual([
      ["Project", "Score"],
      ["OpenWorker", 84],
    ]);
    expect(roundTrip("biff8")).toEqual([
      ["Project", "Score"],
      ["OpenWorker", 84],
    ]);
  });
});
