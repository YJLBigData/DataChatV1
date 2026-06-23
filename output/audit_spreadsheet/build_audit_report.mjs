import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "/Users/yangjinlong/app/PythonProject/DataChatV1";
const data = JSON.parse(await fs.readFile(path.join(root, "output/audit/audit_report_data.json"), "utf8"));
const outputDir = path.join(root, "outputs/datachat_audit_20260622");
await fs.mkdir(outputDir, { recursive: true });

const wb = Workbook.create();

function addSheet(name, rows, widths = []) {
  const ws = wb.worksheets.add(name);
  ws.showGridLines = false;
  if (!rows.length) return ws;
  const rowCount = rows.length;
  const colCount = rows[0].length;
  const rng = ws.getRangeByIndexes(0, 0, rowCount, colCount);
  rng.values = rows;
  const header = ws.getRangeByIndexes(0, 0, 1, colCount);
  header.format.fill.color = "#1F4E79";
  header.format.font.color = "#FFFFFF";
  header.format.font.bold = true;
  header.format.wrapText = true;
  rng.format.borders = { preset: "inside", style: "thin", color: "#D9E2F3" };
  ws.getRangeByIndexes(0, 0, rowCount, colCount).format.wrapText = true;
  ws.freezePanes.freezeRows(1);
  widths.forEach((w, i) => {
    ws.getRangeByIndexes(0, i, rowCount, 1).format.columnWidth = w;
  });
  return ws;
}

addSheet(
  "审计摘要",
  [["指标", "结果"], ...data.summary.map((r) => [r["指标"], r["结果"]])],
  [24, 92],
);

addSheet(
  "问题清单",
  [
    ["优先级", "模块", "位置", "问题", "证据", "建议"],
    ...data.code_issues.map((r) => [r["优先级"], r["模块"], r["位置"], r["问题"], r["证据"], r["建议"]]),
  ],
  [10, 18, 36, 48, 52, 52],
);

addSheet(
  "功能测试",
  [
    ["类别", "用例", "结果", "证据"],
    ...data.functional_tests.map((r) => [r["类别"], r["用例"], r["结果"], r["证据"]]),
  ],
  [18, 42, 18, 72],
);

addSheet(
  "样例问数判定",
  [
    ["编号", "轮次", "判定", "返回行数", "问题", "主要问题/说明", "返回字段", "实际SQL", "正例SQL/建议SQL", "截图"],
    ...data.question_rows.map((r) => [
      r["编号"], r["轮次"], r["判定"], r["返回行数"], r["问题"], r["主要问题/说明"],
      r["返回字段"], r["实际SQL"], r["正例SQL/建议SQL"], r["截图"],
    ]),
  ],
  [8, 8, 12, 10, 48, 60, 42, 82, 82, 36],
);

addSheet(
  "结构建议",
  [
    ["领域", "当前问题", "企业级拆分建议"],
    ["后端入口", "main.py 约1100行，混合路由、SSE、语义层管理、会话落库和静态资源。", "拆成 app/api/routes/chat.py、services/chat_service.py、services/sse_stream.py、services/semantic_admin.py。"],
    ["NL2SQL", "planner.py 约1249行，时间、指标、维度、排序、追问上下文和规则兜底耦合。", "拆成 time_parser、metric_resolver、dimension_resolver、context_resolver、ranking_parser、plan_validator，并建立口径测试集。"],
    ["测试", "业务工具 test_runner.py 被 pytest 收集；样例问数没有自动判定。", "工具文件避免 test_ 命名；把本次 Excel 样例沉淀为可重复的 accuracy eval，CI 输出 PASS/WARN/FAIL。"],
    ["依赖", "start.sh 用抽样 import 判断依赖，不跟踪 requirements 变化。", "写 requirements hash 到 venv，hash 变化就 pip install；启动后执行 pip check。"],
    ["前端", "移动端管理表格可见但拥挤。", "管理页移动端用卡片列表 + 详情抽屉；高频按钮固定在行尾或底部操作栏。"],
  ],
  [18, 66, 78],
);

const statusCol = wb.worksheets.getItem("样例问数判定").getRange("C2:C200");
statusCol.conditionalFormats.add("containsText", {
  text: "FAIL",
  format: { fill: { color: "#FCE4D6" }, font: { color: "#9C0006", bold: true } },
});
statusCol.conditionalFormats.add("containsText", {
  text: "WARN",
  format: { fill: { color: "#FFF2CC" }, font: { color: "#9C6500", bold: true } },
});
statusCol.conditionalFormats.add("containsText", {
  text: "PASS",
  format: { fill: { color: "#E2F0D9" }, font: { color: "#006100", bold: true } },
});

for (const ws of wb.worksheets.items) {
  const used = ws.getUsedRange();
  used.format.autofitRows();
}

const out = await SpreadsheetFile.exportXlsx(wb);
await out.save(path.join(outputDir, "DataChatV1_strict_audit_20260622.xlsx"));

for (const ws of wb.worksheets.items) {
  const preview = await wb.render({ sheetName: ws.name, autoCrop: "all", scale: 1, format: "png" });
  const bytes = new Uint8Array(await preview.arrayBuffer());
  await fs.writeFile(path.join(outputDir, `${ws.name}.png`), bytes);
}

const errors = await wb.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errors.ndjson);
console.log(path.join(outputDir, "DataChatV1_strict_audit_20260622.xlsx"));
