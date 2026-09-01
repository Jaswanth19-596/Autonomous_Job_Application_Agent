import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [inputPath, outputPath, firstIndexArg = "0", lastIndexArg = "412"] =
  process.argv.slice(2);

if (!inputPath || !outputPath) {
  throw new Error(
    "Usage: node scripts/remove_initial_job_rows.mjs <input.xlsx> <output.xlsx> [first-index] [last-index]",
  );
}

const firstIndex = Number(firstIndexArg);
const lastIndex = Number(lastIndexArg);
if (!Number.isInteger(firstIndex) || !Number.isInteger(lastIndex) || firstIndex < 0 || lastIndex < firstIndex) {
  throw new Error("Row indices must be non-negative integers with last-index >= first-index.");
}

const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const sheet =
  workbook.worksheets.items.find((worksheet) => worksheet.name === "Applications") ??
  workbook.worksheets.getItemAt(0);
const usedRange = sheet.getUsedRange();
const values = usedRange.values;

if (values.length < 2) {
  throw new Error("The workbook has no job rows to remove.");
}

const header = values[0];
const jobs = values.slice(1);
if (lastIndex >= jobs.length) {
  throw new Error(
    `Cannot remove job indices ${firstIndex}-${lastIndex}: the worksheet contains only ${jobs.length} job row(s).`,
  );
}

const retainedJobs = [
  ...jobs.slice(0, firstIndex),
  ...jobs.slice(lastIndex + 1),
];
const retainedValues = [header, ...retainedJobs];
const columnCount = header.length;
const columnName = (index) => {
  let value = "";
  for (let number = index; number > 0; number = Math.floor((number - 1) / 26)) {
    value = String.fromCharCode(65 + ((number - 1) % 26)) + value;
  }
  return value;
};
const lastColumn = columnName(columnCount);
const originalLastRow = values.length;
const newLastRow = retainedValues.length;

const existingTable = sheet.tables.items[0];
const tableSettings = existingTable
  ? {
      name: existingTable.name,
      style: existingTable.style,
      showBandedRows: existingTable.showBandedRows,
      showBandedColumns: existingTable.showBandedColumns,
      showFilterButton: existingTable.showFilterButton,
      highlightFirstColumn: existingTable.highlightFirstColumn,
      highlightLastColumn: existingTable.highlightLastColumn,
    }
  : null;

if (existingTable) {
  existingTable.delete();
}

sheet.getRange(`A1:${lastColumn}${newLastRow}`).values = retainedValues;
if (newLastRow < originalLastRow) {
  sheet
    .getRange(`A${newLastRow + 1}:${lastColumn}${originalLastRow}`)
    .clear({ applyTo: "contents" });
}

if (tableSettings) {
  const table = sheet.tables.add(
    `A1:${lastColumn}${newLastRow}`,
    true,
    tableSettings.name,
  );
  table.style = tableSettings.style;
  table.showBandedRows = tableSettings.showBandedRows;
  table.showBandedColumns = tableSettings.showBandedColumns;
  table.showFilterButton = tableSettings.showFilterButton;
  table.highlightFirstColumn = tableSettings.highlightFirstColumn;
  table.highlightLastColumn = tableSettings.highlightLastColumn;
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

console.log(
  JSON.stringify({
    inputPath,
    outputPath,
    removedJobRows: lastIndex - firstIndex + 1,
    retainedJobRows: retainedJobs.length,
    firstRetainedWorksheetRow: 2,
  }),
);
