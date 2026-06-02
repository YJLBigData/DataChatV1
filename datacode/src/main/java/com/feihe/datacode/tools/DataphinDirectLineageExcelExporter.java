package com.feihe.datacode.tools;

import org.apache.logging.log4j.Level;
import org.apache.logging.log4j.core.config.Configurator;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.CellStyle;
import org.apache.poi.ss.usermodel.FillPatternType;
import org.apache.poi.ss.usermodel.Font;
import org.apache.poi.ss.usermodel.HorizontalAlignment;
import org.apache.poi.ss.usermodel.IndexedColors;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.VerticalAlignment;
import org.apache.poi.xssf.streaming.SXSSFWorkbook;

import java.io.BufferedReader;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Properties;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 导出 Dataphin 表级直系血缘和任务明细。
 *
 * 直系血缘只包含目标表作为 source 或 target 的一跳关系，不继续递归扩展邻居表。
 */
public class DataphinDirectLineageExcelExporter {
    private static final String DRIVER = "com.aliyun.odps.jdbc.OdpsDriver";
    private static final String DEFAULT_ENDPOINT = "http://service.cn-beijing.maxcompute.aliyun.com/api";
    private static final String DEFAULT_PROJECT = "firmus_dataphin_prd_ads";
    private static final String LINEAGE_TABLE = "firmus_dataphin_prd_ads.ads_dataphin_table_blood_relationship";
    private static final String TASK_TABLE = "firmus_dataphin_prd_ads.ads_dataphin_vdm_node_detail";
    private static final int EXCEL_CELL_LIMIT = 32767;

    public static void main(String[] args) throws Exception {
        quietThirdPartyLogs();
        Cli cli = Cli.parse(args);
        String table = cli.get("table", "ads_ec_fn_gross_profit_day_df").trim();
        String tableProject = cli.get("project", "").trim();
        String output = cli.get("output", "output/" + table + "_direct_task_code.xlsx").trim();
        String configPath = cli.get("config", "config/datacode.env").trim();
        String dsArg = cli.get("ds", "").trim();
        int pageSize = Integer.parseInt(cli.get("page-size", "9000"));
        int taskBatchSize = Integer.parseInt(cli.get("task-batch-size", "500"));

        Map<String, String> config = loadConfig(configPath);
        String accessKeyId = firstNotBlank(
                System.getenv("ALIYUN_DATA_PLATFORM_AK"),
                System.getenv("ODPS_ACCESS_KEY_ID"),
                config.get("ALIYUN_DATA_PLATFORM_AK"),
                config.get("ODPS_ACCESS_KEY_ID")
        );
        String accessKeySecret = firstNotBlank(
                System.getenv("ALIYUN_DATA_PLATFORM_SK"),
                System.getenv("ODPS_ACCESS_KEY_SECRET"),
                config.get("ALIYUN_DATA_PLATFORM_SK"),
                config.get("ODPS_ACCESS_KEY_SECRET")
        );
        String endpoint = firstNotBlank(System.getenv("ODPS_ENDPOINT"), config.get("ODPS_ENDPOINT"), DEFAULT_ENDPOINT);
        String defaultProject = firstNotBlank(System.getenv("DATAPHIN_DEFAULT_PROJECT"), config.get("DATAPHIN_DEFAULT_PROJECT"), DEFAULT_PROJECT);

        if (isBlank(accessKeyId) || isBlank(accessKeySecret)) {
            throw new IllegalStateException("未配置 MaxCompute 凭证，请在环境变量或 config/datacode.env 中配置 ALIYUN_DATA_PLATFORM_AK / ALIYUN_DATA_PLATFORM_SK");
        }

        Class.forName(DRIVER);
        Properties props = new Properties();
        props.put("access_id", accessKeyId);
        props.put("access_key", accessKeySecret);
        String jdbcUrl = "jdbc:odps:" + endpoint + "?project=" + defaultProject;

        long start = System.currentTimeMillis();
        try (Connection conn = DriverManager.getConnection(jdbcUrl, props)) {
            String ds = isBlank(dsArg) ? queryLatestDs(conn) : dsArg;
            if (isBlank(ds)) {
                throw new IllegalStateException("血缘表未查询到可用 ds");
            }

            System.out.println("root_table=" + table);
            System.out.println("root_project=" + (isBlank(tableProject) ? "*" : tableProject));
            System.out.println("lineage_ds=" + ds);
            System.out.println("output=" + Paths.get(output).toAbsolutePath().normalize());
            System.out.println("collect_mode=direct-only");

            DirectLineageResult result = collectDirectLineage(conn, tableProject, table, ds, pageSize);
            List<TaskNode> tasks = queryTasks(conn, ds, result.allNodeIds(), taskBatchSize);
            writeExcel(output, tableProject, table, ds, result, tasks, System.currentTimeMillis() - start);

            System.out.println("tables=" + result.tables().size());
            System.out.println("upstream_tables=" + result.upstreamTables().size());
            System.out.println("downstream_tables=" + result.downstreamTables().size());
            System.out.println("edges=" + result.edges.size());
            System.out.println("tasks=" + tasks.size());
            System.out.println("export_tasks=" + countExportTasks(tasks, result));
            System.out.println("done=" + Paths.get(output).toAbsolutePath().normalize());
        }
    }

    private static void quietThirdPartyLogs() {
        Configurator.setRootLevel(Level.WARN);
        Configurator.setLevel("com.aliyun.odps", Level.ERROR);
        Configurator.setLevel("com.aliyun.odps.jdbc", Level.ERROR);
    }

    private static DirectLineageResult collectDirectLineage(Connection conn, String project, String table, String ds, int pageSize) throws Exception {
        String condition = rootCondition(project, table);
        long total = queryCount(conn, "SELECT COUNT(1) AS cnt FROM " + LINEAGE_TABLE
                + " WHERE ds = '" + escapeSql(ds) + "' AND (" + condition + ")");
        System.out.println("direct_edges_total=" + total);
        if (pageSize <= 0 || pageSize > 9000) {
            pageSize = 9000;
        }

        DirectLineageResult result = new DirectLineageResult(project, table);
        if (total == 0) {
            return result;
        }

        String columns = "id,source_project,source_table,source_table_original,"
                + "target_project,target_table,target_table_original,"
                + "target_owner_name,target_modifier_name,"
                + "CAST(target_gmt_create AS STRING) AS target_gmt_create,"
                + "CAST(target_gmt_modified AS STRING) AS target_gmt_modified,"
                + "time_wimdow,strategy,source_node_id,target_node_id,ds";
        for (long start = 0; start < total; start += pageSize) {
            long end = Math.min(start + pageSize, total);
            String sql = "SELECT " + columns + " FROM ("
                    + "SELECT " + columns + ","
                    + "ROW_NUMBER() OVER (ORDER BY id,source_project,source_table,target_project,target_table,source_node_id,target_node_id) AS rn "
                    + "FROM " + LINEAGE_TABLE + " "
                    + "WHERE ds = '" + escapeSql(ds) + "' AND (" + condition + ")"
                    + ") t WHERE rn > " + start + " AND rn <= " + end;
            for (Map<String, String> row : query(conn, sql)) {
                result.addEdge(LineageEdge.from(row));
            }
            System.out.printf(Locale.ROOT, "direct_edges_fetched=%d/%d%n", result.edges.size(), total);
        }
        if (result.edges.size() != total) {
            throw new IllegalStateException("直系血缘分页读取不完整，expected=" + total + ", actual=" + result.edges.size());
        }
        return result;
    }

    private static String rootCondition(String project, String table) {
        String escapedTable = escapeSql(table);
        if (isBlank(project)) {
            return "source_table = '" + escapedTable + "' OR target_table = '" + escapedTable + "'";
        }
        String escapedProject = escapeSql(project);
        return "(source_project = '" + escapedProject + "' AND source_table = '" + escapedTable + "') "
                + "OR (target_project = '" + escapedProject + "' AND target_table = '" + escapedTable + "')";
    }

    private static String queryLatestDs(Connection conn) throws Exception {
        List<Map<String, String>> rows = query(conn, "SELECT MAX(ds) AS ds FROM " + LINEAGE_TABLE);
        return rows.isEmpty() ? "" : rows.get(0).getOrDefault("ds", "");
    }

    private static long queryCount(Connection conn, String sql) throws Exception {
        List<Map<String, String>> rows = query(conn, sql);
        if (rows.isEmpty()) {
            return 0L;
        }
        String value = firstNotBlank(rows.get(0).get("cnt"), rows.get(0).get("count"));
        return isBlank(value) ? 0L : Long.parseLong(value);
    }

    private static List<TaskNode> queryTasks(Connection conn, String ds, Set<String> nodeIds, int batchSize) throws Exception {
        List<String> ids = nodeIds.stream().filter(id -> !isBlank(id)).sorted().collect(Collectors.toList());
        if (ids.isEmpty()) {
            return Collections.emptyList();
        }
        if (batchSize <= 0 || batchSize > 500) {
            batchSize = 500;
        }
        List<TaskNode> tasks = new ArrayList<>();
        for (int i = 0; i < ids.size(); i += batchSize) {
            List<String> batch = ids.subList(i, Math.min(i + batchSize, ids.size()));
            String in = batch.stream().map(id -> "'" + escapeSql(id) + "'").collect(Collectors.joining(","));
            String sql = "SELECT project_name,node_name,owner_name,modifier_name,node_type,operator_type,"
                    + "cron_expression,schedule_interval_type,directorys,content,"
                    + "CAST(gmt_create AS STRING) AS gmt_create,"
                    + "CAST(gmt_modified AS STRING) AS gmt_modified,"
                    + "direc_lev1,direc_lev2,direc_lev3,direc_lev4,direc_lev5,"
                    + "is_downstream,param,node_id,ds "
                    + "FROM " + TASK_TABLE + " "
                    + "WHERE ds = '" + escapeSql(ds) + "' AND node_id IN (" + in + ")";
            for (Map<String, String> row : query(conn, sql)) {
                tasks.add(TaskNode.from(row));
            }
            System.out.printf(Locale.ROOT, "tasks_fetched=%d node_batch=%d%n", tasks.size(), batch.size());
        }
        tasks.sort(Comparator.comparing((TaskNode t) -> nullToEmpty(t.projectName))
                .thenComparing(t -> nullToEmpty(t.nodeName))
                .thenComparing(t -> nullToEmpty(t.nodeId)));
        return tasks;
    }

    private static void writeExcel(String output, String project, String table, String ds,
                                   DirectLineageResult result, List<TaskNode> tasks, long elapsedMs) throws IOException {
        Path outputPath = Paths.get(output).toAbsolutePath().normalize();
        if (outputPath.getParent() != null) {
            Files.createDirectories(outputPath.getParent());
        }

        try (SXSSFWorkbook workbook = new SXSSFWorkbook(200);
             FileOutputStream out = new FileOutputStream(outputPath.toFile())) {
            workbook.setCompressTempFiles(true);
            CellStyle headerStyle = headerStyle(workbook);
            CellStyle wrapStyle = wrapStyle(workbook);

            writeSummarySheet(workbook, headerStyle, project, table, ds, result, tasks, elapsedMs);
            writeTablesSheet(workbook, headerStyle, result);
            writeEdgesSheet(workbook, headerStyle, result);
            writeTasksSheet(workbook, headerStyle, wrapStyle, tasks, result);
            workbook.write(out);
        }
    }

    private static void writeSummarySheet(SXSSFWorkbook workbook, CellStyle headerStyle, String project, String table, String ds,
                                          DirectLineageResult result, List<TaskNode> tasks, long elapsedMs) {
        Sheet sheet = workbook.createSheet("汇总");
        List<String[]> rows = new ArrayList<>();
        rows.add(new String[]{"目标表项目", isBlank(project) ? "*" : project});
        rows.add(new String[]{"目标表", table});
        rows.add(new String[]{"血缘分区ds", ds});
        rows.add(new String[]{"导出时间", LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"))});
        rows.add(new String[]{"直系相关表数量", String.valueOf(result.tables().size())});
        rows.add(new String[]{"直接上游表数量", String.valueOf(result.upstreamTables().size())});
        rows.add(new String[]{"直接下游表数量", String.valueOf(result.downstreamTables().size())});
        rows.add(new String[]{"直系血缘边数量", String.valueOf(result.edges.size())});
        rows.add(new String[]{"任务数量", String.valueOf(tasks.size())});
        rows.add(new String[]{"导出任务数量", String.valueOf(countExportTasks(tasks, result))});
        rows.add(new String[]{"耗时毫秒", String.valueOf(elapsedMs)});
        rows.add(new String[]{"说明", "只包含目标表作为 source 或 target 的一跳直系血缘；导出任务只打标，不导出任务代码 content。"});
        for (int i = 0; i < rows.size(); i++) {
            Row row = sheet.createRow(i);
            Cell key = row.createCell(0);
            key.setCellValue(rows.get(i)[0]);
            key.setCellStyle(headerStyle);
            row.createCell(1).setCellValue(rows.get(i)[1]);
        }
        sheet.setColumnWidth(0, 24 * 256);
        sheet.setColumnWidth(1, 90 * 256);
    }

    private static void writeTablesSheet(SXSSFWorkbook workbook, CellStyle headerStyle, DirectLineageResult result) {
        Sheet sheet = workbook.createSheet("直系相关表");
        List<String> headers = list("relation", "project", "table_name", "table_key", "related_edge_count", "node_ids");
        writeHeader(sheet, headerStyle, headers);

        List<TableKey> tables = result.tables().stream()
                .sorted(Comparator.comparing((TableKey key) -> relationOrder(result.relation(key)))
                        .thenComparing(key -> key.project)
                        .thenComparing(key -> key.table))
                .collect(Collectors.toList());
        int rowIdx = 1;
        for (TableKey key : tables) {
            Row row = sheet.createRow(rowIdx++);
            int col = 0;
            write(row, col++, result.relation(key));
            write(row, col++, key.project);
            write(row, col++, key.table);
            write(row, col++, key.key());
            write(row, col++, String.valueOf(result.edgeCount(key)));
            write(row, col, String.join("\n", result.nodeIds(key)));
        }
        setWidths(sheet, 26, 28, 42, 72, 18, 60);
    }

    private static void writeEdgesSheet(SXSSFWorkbook workbook, CellStyle headerStyle, DirectLineageResult result) {
        Sheet sheet = workbook.createSheet("直系血缘边");
        List<String> headers = list(
                "direction", "id", "source_project", "source_table", "source_table_original",
                "target_project", "target_table", "target_table_original",
                "target_owner_name", "target_modifier_name", "target_gmt_create", "target_gmt_modified",
                "time_wimdow", "strategy", "source_node_id", "target_node_id", "ds"
        );
        writeHeader(sheet, headerStyle, headers);

        List<LineageEdge> edges = new ArrayList<>(result.edges.values());
        edges.sort(Comparator.comparing((LineageEdge e) -> result.edgeDirection(e))
                .thenComparing(e -> e.sourceProject)
                .thenComparing(e -> e.sourceTable)
                .thenComparing(e -> e.targetProject)
                .thenComparing(e -> e.targetTable));
        int rowIdx = 1;
        for (LineageEdge edge : edges) {
            Row row = sheet.createRow(rowIdx++);
            int col = 0;
            write(row, col++, result.edgeDirection(edge));
            write(row, col++, edge.id);
            write(row, col++, edge.sourceProject);
            write(row, col++, edge.sourceTable);
            write(row, col++, edge.sourceTableOriginal);
            write(row, col++, edge.targetProject);
            write(row, col++, edge.targetTable);
            write(row, col++, edge.targetTableOriginal);
            write(row, col++, edge.targetOwnerName);
            write(row, col++, edge.targetModifierName);
            write(row, col++, edge.targetGmtCreate);
            write(row, col++, edge.targetGmtModified);
            write(row, col++, edge.timeWindow);
            write(row, col++, edge.strategy);
            write(row, col++, edge.sourceNodeId);
            write(row, col++, edge.targetNodeId);
            write(row, col, edge.ds);
        }
        setWidths(sheet, 18, 42, 28, 42, 42, 28, 42, 42, 24, 24, 22, 22, 18, 18, 28, 28, 12);
    }

    private static void writeTasksSheet(SXSSFWorkbook workbook, CellStyle headerStyle, CellStyle wrapStyle,
                                        List<TaskNode> tasks, DirectLineageResult result) {
        Sheet sheet = workbook.createSheet("任务明细");
        List<String> headers = list(
                "node_id", "lineage_relation", "is_export_task", "export_reason", "code_exported",
                "related_tables", "appears_in_edges", "project_name", "node_name", "owner_name", "modifier_name",
                "node_type", "operator_type", "cron_expression", "schedule_interval_type", "directorys",
                "gmt_create", "gmt_modified", "direc_lev1", "direc_lev2", "direc_lev3", "direc_lev4", "direc_lev5",
                "is_downstream", "param", "ds", "content"
        );
        writeHeader(sheet, headerStyle, headers);

        Map<String, Integer> nodeEdgeCount = result.nodeEdgeCount();
        int rowIdx = 1;
        for (TaskNode task : tasks) {
            TaskLineageInfo lineageInfo = result.taskLineageInfo(task.nodeId);
            ExportFlag exportFlag = detectExportTask(task, lineageInfo);
            Row row = sheet.createRow(rowIdx++);
            int col = 0;
            write(row, col++, task.nodeId);
            write(row, col++, String.join(",", lineageInfo.relations));
            write(row, col++, exportFlag.exportTask ? "是" : "否");
            write(row, col++, exportFlag.reason);
            write(row, col++, exportFlag.exportTask ? "否" : "是");
            write(row, col++, String.join("\n", lineageInfo.tableKeys));
            write(row, col++, String.valueOf(nodeEdgeCount.getOrDefault(task.nodeId, 0)));
            write(row, col++, task.projectName);
            write(row, col++, task.nodeName);
            write(row, col++, task.ownerName);
            write(row, col++, task.modifierName);
            write(row, col++, task.nodeType);
            write(row, col++, task.operatorType);
            write(row, col++, task.cronExpression);
            write(row, col++, task.scheduleIntervalType);
            write(row, col++, task.directorys);
            write(row, col++, task.gmtCreate);
            write(row, col++, task.gmtModified);
            write(row, col++, task.direcLev1);
            write(row, col++, task.direcLev2);
            write(row, col++, task.direcLev3);
            write(row, col++, task.direcLev4);
            write(row, col++, task.direcLev5);
            write(row, col++, task.isDownstream);
            write(row, col++, task.param);
            write(row, col++, task.ds);
            Cell contentCell = row.createCell(col);
            contentCell.setCellValue(exportFlag.exportTask ? "" : cleanExcelCell(task.content));
            contentCell.setCellStyle(wrapStyle);
        }
        setWidths(sheet, 30, 28, 16, 42, 14, 72, 16, 28, 48, 24, 24, 16, 22, 24, 16, 60, 22, 22, 22, 22, 22, 22, 22, 14, 24, 12, 100);
    }

    private static long countExportTasks(List<TaskNode> tasks, DirectLineageResult result) {
        return tasks.stream()
                .filter(task -> detectExportTask(task, result.taskLineageInfo(task.nodeId)).exportTask)
                .count();
    }

    private static ExportFlag detectExportTask(TaskNode task, TaskLineageInfo lineageInfo) {
        List<String> reasons = new ArrayList<>();
        String nodeName = nullToEmpty(task.nodeName).toLowerCase(Locale.ROOT);
        String operatorType = nullToEmpty(task.operatorType).toLowerCase(Locale.ROOT);
        String nodeType = nullToEmpty(task.nodeType).toLowerCase(Locale.ROOT);
        String directorys = nullToEmpty(task.directorys).toLowerCase(Locale.ROOT);
        String content = nullToEmpty(task.content).toLowerCase(Locale.ROOT);
        String relatedTables = String.join(" ", lineageInfo.tableKeys).toLowerCase(Locale.ROOT);

        if (nodeName.startsWith("exp_") || relatedTables.contains(".exp_") || relatedTables.startsWith("exp_")) {
            reasons.add("任务名或表名为 exp_ 导出命名");
        }
        if (nodeName.endsWith("_adb") || relatedTables.contains("_adb")) {
            reasons.add("任务名或表名包含 ADB 导出标识");
        }
        if (containsAny(operatorType, "export", "datax", "di_export")
                || containsAny(nodeType, "导出", "export")
                || containsAny(directorys, "导出", "export")
                || containsAny(content, "导出任务", "export")) {
            reasons.add("任务元信息包含导出标识");
        }
        return new ExportFlag(!reasons.isEmpty(), String.join("；", reasons));
    }

    private static boolean containsAny(String text, String... needles) {
        for (String needle : needles) {
            if (text.contains(needle.toLowerCase(Locale.ROOT))) {
                return true;
            }
        }
        return false;
    }

    private static List<Map<String, String>> query(Connection conn, String sql) throws Exception {
        try (Statement statement = conn.createStatement();
             ResultSet rs = statement.executeQuery(sql)) {
            ResultSetMetaData metaData = rs.getMetaData();
            int columns = metaData.getColumnCount();
            List<Map<String, String>> rows = new ArrayList<>();
            while (rs.next()) {
                Map<String, String> row = new LinkedHashMap<>();
                for (int i = 1; i <= columns; i++) {
                    String name = metaData.getColumnLabel(i);
                    if (isBlank(name)) {
                        name = metaData.getColumnName(i);
                    }
                    Object value = rs.getObject(i);
                    row.put(name.toLowerCase(Locale.ROOT), value == null ? "" : String.valueOf(value));
                }
                rows.add(row);
            }
            return rows;
        }
    }

    private static Map<String, String> loadConfig(String configPath) throws IOException {
        Path path = Paths.get(configPath);
        if (!Files.exists(path)) {
            return Collections.emptyMap();
        }
        Map<String, String> config = new LinkedHashMap<>();
        try (BufferedReader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            String line;
            while ((line = reader.readLine()) != null) {
                String trimmed = line.trim();
                if (trimmed.isEmpty() || trimmed.startsWith("#") || !trimmed.contains("=")) {
                    continue;
                }
                int idx = trimmed.indexOf('=');
                String key = trimmed.substring(0, idx).trim();
                String value = trimmed.substring(idx + 1).trim();
                if ((value.startsWith("\"") && value.endsWith("\"")) || (value.startsWith("'") && value.endsWith("'"))) {
                    value = value.substring(1, value.length() - 1);
                }
                config.put(key, value);
            }
        }
        return config;
    }

    private static CellStyle headerStyle(SXSSFWorkbook workbook) {
        CellStyle style = workbook.createCellStyle();
        style.setFillForegroundColor(IndexedColors.GREY_25_PERCENT.getIndex());
        style.setFillPattern(FillPatternType.SOLID_FOREGROUND);
        style.setAlignment(HorizontalAlignment.CENTER);
        style.setVerticalAlignment(VerticalAlignment.CENTER);
        Font font = workbook.createFont();
        font.setBold(true);
        style.setFont(font);
        return style;
    }

    private static CellStyle wrapStyle(SXSSFWorkbook workbook) {
        CellStyle style = workbook.createCellStyle();
        style.setWrapText(true);
        style.setVerticalAlignment(VerticalAlignment.TOP);
        return style;
    }

    private static void writeHeader(Sheet sheet, CellStyle style, List<String> headers) {
        Row row = sheet.createRow(0);
        for (int i = 0; i < headers.size(); i++) {
            Cell cell = row.createCell(i);
            cell.setCellValue(headers.get(i));
            cell.setCellStyle(style);
        }
        sheet.createFreezePane(0, 1);
    }

    private static void write(Row row, int col, String value) {
        row.createCell(col).setCellValue(cleanExcelCell(value));
    }

    private static String cleanExcelCell(String value) {
        String text = nullToEmpty(value).replaceAll("[\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F]", " ");
        if (text.length() > EXCEL_CELL_LIMIT) {
            return text.substring(0, EXCEL_CELL_LIMIT - 30) + "\n-- Excel单元格超长已截断";
        }
        return text;
    }

    private static void setWidths(Sheet sheet, int... widths) {
        for (int i = 0; i < widths.length; i++) {
            sheet.setColumnWidth(i, Math.min(widths[i], 120) * 256);
        }
    }

    private static int relationOrder(String relation) {
        switch (relation) {
            case "root":
                return 0;
            case "direct_upstream":
                return 1;
            case "direct_downstream":
                return 2;
            case "direct_upstream_and_downstream":
                return 3;
            default:
                return 4;
        }
    }

    private static String escapeSql(String value) {
        return nullToEmpty(value).replace("'", "''");
    }

    private static String firstNotBlank(String... values) {
        for (String value : values) {
            if (!isBlank(value)) {
                return value.trim();
            }
        }
        return "";
    }

    private static boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private static List<String> list(String... values) {
        List<String> result = new ArrayList<>();
        Collections.addAll(result, values);
        return result;
    }

    static class DirectLineageResult {
        final String rootProject;
        final String rootTable;
        final Map<String, LineageEdge> edges = new LinkedHashMap<>();

        DirectLineageResult(String rootProject, String rootTable) {
            this.rootProject = nullToEmpty(rootProject);
            this.rootTable = nullToEmpty(rootTable);
        }

        void addEdge(LineageEdge edge) {
            edges.putIfAbsent(edge.edgeKey(), edge);
        }

        Set<TableKey> rootTables() {
            Set<TableKey> roots = new LinkedHashSet<>();
            for (LineageEdge edge : edges.values()) {
                if (isRoot(edge.sourceKey())) {
                    roots.add(edge.sourceKey());
                }
                if (isRoot(edge.targetKey())) {
                    roots.add(edge.targetKey());
                }
            }
            if (roots.isEmpty() && !isBlank(rootProject)) {
                roots.add(new TableKey(rootProject, rootTable));
            }
            return roots;
        }

        Set<TableKey> upstreamTables() {
            Set<TableKey> tables = new LinkedHashSet<>();
            for (LineageEdge edge : edges.values()) {
                if (isRoot(edge.targetKey()) && !isRoot(edge.sourceKey())) {
                    tables.add(edge.sourceKey());
                }
            }
            return tables;
        }

        Set<TableKey> downstreamTables() {
            Set<TableKey> tables = new LinkedHashSet<>();
            for (LineageEdge edge : edges.values()) {
                if (isRoot(edge.sourceKey()) && !isRoot(edge.targetKey())) {
                    tables.add(edge.targetKey());
                }
            }
            return tables;
        }

        Set<TableKey> tables() {
            Set<TableKey> tables = new LinkedHashSet<>();
            tables.addAll(rootTables());
            tables.addAll(upstreamTables());
            tables.addAll(downstreamTables());
            return tables;
        }

        boolean isRoot(TableKey key) {
            if (!key.table.equalsIgnoreCase(rootTable)) {
                return false;
            }
            return isBlank(rootProject) || key.project.equalsIgnoreCase(rootProject);
        }

        String relation(TableKey key) {
            if (isRoot(key)) {
                return "root";
            }
            boolean upstream = upstreamTables().contains(key);
            boolean downstream = downstreamTables().contains(key);
            if (upstream && downstream) {
                return "direct_upstream_and_downstream";
            }
            if (upstream) {
                return "direct_upstream";
            }
            if (downstream) {
                return "direct_downstream";
            }
            return "direct_related";
        }

        String edgeDirection(LineageEdge edge) {
            boolean sourceRoot = isRoot(edge.sourceKey());
            boolean targetRoot = isRoot(edge.targetKey());
            if (sourceRoot && targetRoot) {
                return "root_self";
            }
            if (targetRoot) {
                return "direct_upstream";
            }
            if (sourceRoot) {
                return "direct_downstream";
            }
            return "direct_related";
        }

        int edgeCount(TableKey key) {
            int count = 0;
            for (LineageEdge edge : edges.values()) {
                if (edge.sourceKey().equals(key) || edge.targetKey().equals(key)) {
                    count++;
                }
            }
            return count;
        }

        Set<String> nodeIds(TableKey key) {
            Set<String> ids = new LinkedHashSet<>();
            for (LineageEdge edge : edges.values()) {
                if (edge.sourceKey().equals(key) && !isBlank(edge.sourceNodeId)) {
                    ids.add(edge.sourceNodeId);
                }
                if (edge.targetKey().equals(key) && !isBlank(edge.targetNodeId)) {
                    ids.add(edge.targetNodeId);
                }
            }
            return ids;
        }

        Set<String> allNodeIds() {
            Set<String> ids = new LinkedHashSet<>();
            for (LineageEdge edge : edges.values()) {
                if (!isBlank(edge.sourceNodeId)) {
                    ids.add(edge.sourceNodeId);
                }
                if (!isBlank(edge.targetNodeId)) {
                    ids.add(edge.targetNodeId);
                }
            }
            return ids;
        }

        Map<String, Integer> nodeEdgeCount() {
            Map<String, Integer> count = new HashMap<>();
            for (LineageEdge edge : edges.values()) {
                if (!isBlank(edge.sourceNodeId)) {
                    count.merge(edge.sourceNodeId, 1, Integer::sum);
                }
                if (!isBlank(edge.targetNodeId)) {
                    count.merge(edge.targetNodeId, 1, Integer::sum);
                }
            }
            return count;
        }

        TaskLineageInfo taskLineageInfo(String nodeId) {
            TaskLineageInfo info = new TaskLineageInfo();
            if (isBlank(nodeId)) {
                return info;
            }
            for (LineageEdge edge : edges.values()) {
                boolean sourceNode = nodeId.equals(edge.sourceNodeId);
                boolean targetNode = nodeId.equals(edge.targetNodeId);
                if (!sourceNode && !targetNode) {
                    continue;
                }

                String direction = edgeDirection(edge);
                info.edgeDirections.add(direction);
                if (sourceNode) {
                    TableKey source = edge.sourceKey();
                    info.tableKeys.add(source.key());
                    if (isRoot(source)) {
                        info.relations.add("root");
                    } else if ("direct_upstream".equals(direction)) {
                        info.relations.add("direct_upstream");
                    } else {
                        info.relations.add("direct_source");
                    }
                }
                if (targetNode) {
                    TableKey target = edge.targetKey();
                    info.tableKeys.add(target.key());
                    if (isRoot(target)) {
                        info.relations.add("root");
                    } else if ("direct_downstream".equals(direction)) {
                        info.relations.add("direct_downstream");
                    } else {
                        info.relations.add("direct_target");
                    }
                }
            }
            return info;
        }
    }

    static class TaskLineageInfo {
        final Set<String> relations = new LinkedHashSet<>();
        final Set<String> tableKeys = new LinkedHashSet<>();
        final Set<String> edgeDirections = new LinkedHashSet<>();
    }

    static class ExportFlag {
        final boolean exportTask;
        final String reason;

        ExportFlag(boolean exportTask, String reason) {
            this.exportTask = exportTask;
            this.reason = reason;
        }
    }

    static class TableKey {
        final String project;
        final String table;

        TableKey(String project, String table) {
            this.project = nullToEmpty(project);
            this.table = nullToEmpty(table);
        }

        String key() {
            return isBlank(project) ? table : project + "." + table;
        }

        @Override
        public boolean equals(Object o) {
            if (this == o) {
                return true;
            }
            if (!(o instanceof TableKey)) {
                return false;
            }
            TableKey tableKey = (TableKey) o;
            return project.equalsIgnoreCase(tableKey.project) && table.equalsIgnoreCase(tableKey.table);
        }

        @Override
        public int hashCode() {
            return Objects.hash(project.toLowerCase(Locale.ROOT), table.toLowerCase(Locale.ROOT));
        }
    }

    static class LineageEdge {
        String id;
        String sourceProject;
        String sourceTable;
        String sourceTableOriginal;
        String targetProject;
        String targetTable;
        String targetTableOriginal;
        String targetOwnerName;
        String targetModifierName;
        String targetGmtCreate;
        String targetGmtModified;
        String timeWindow;
        String strategy;
        String sourceNodeId;
        String targetNodeId;
        String ds;

        static LineageEdge from(Map<String, String> row) {
            LineageEdge edge = new LineageEdge();
            edge.id = row.getOrDefault("id", "");
            edge.sourceProject = row.getOrDefault("source_project", "");
            edge.sourceTable = row.getOrDefault("source_table", "");
            edge.sourceTableOriginal = row.getOrDefault("source_table_original", "");
            edge.targetProject = row.getOrDefault("target_project", "");
            edge.targetTable = row.getOrDefault("target_table", "");
            edge.targetTableOriginal = row.getOrDefault("target_table_original", "");
            edge.targetOwnerName = row.getOrDefault("target_owner_name", "");
            edge.targetModifierName = row.getOrDefault("target_modifier_name", "");
            edge.targetGmtCreate = row.getOrDefault("target_gmt_create", "");
            edge.targetGmtModified = row.getOrDefault("target_gmt_modified", "");
            edge.timeWindow = row.getOrDefault("time_wimdow", "");
            edge.strategy = row.getOrDefault("strategy", "");
            edge.sourceNodeId = row.getOrDefault("source_node_id", "");
            edge.targetNodeId = row.getOrDefault("target_node_id", "");
            edge.ds = row.getOrDefault("ds", "");
            return edge;
        }

        TableKey sourceKey() {
            return new TableKey(sourceProject, sourceTable);
        }

        TableKey targetKey() {
            return new TableKey(targetProject, targetTable);
        }

        String edgeKey() {
            if (!isBlank(id)) {
                return id;
            }
            return sourceProject + "." + sourceTable + "->" + targetProject + "." + targetTable + "#" + sourceNodeId + "#" + targetNodeId;
        }
    }

    static class TaskNode {
        String projectName;
        String nodeName;
        String ownerName;
        String modifierName;
        String nodeType;
        String operatorType;
        String cronExpression;
        String scheduleIntervalType;
        String directorys;
        String content;
        String gmtCreate;
        String gmtModified;
        String direcLev1;
        String direcLev2;
        String direcLev3;
        String direcLev4;
        String direcLev5;
        String isDownstream;
        String param;
        String nodeId;
        String ds;

        static TaskNode from(Map<String, String> row) {
            TaskNode task = new TaskNode();
            task.projectName = row.getOrDefault("project_name", "");
            task.nodeName = row.getOrDefault("node_name", "");
            task.ownerName = row.getOrDefault("owner_name", "");
            task.modifierName = row.getOrDefault("modifier_name", "");
            task.nodeType = row.getOrDefault("node_type", "");
            task.operatorType = row.getOrDefault("operator_type", "");
            task.cronExpression = row.getOrDefault("cron_expression", "");
            task.scheduleIntervalType = row.getOrDefault("schedule_interval_type", "");
            task.directorys = row.getOrDefault("directorys", "");
            task.content = row.getOrDefault("content", "");
            task.gmtCreate = row.getOrDefault("gmt_create", "");
            task.gmtModified = row.getOrDefault("gmt_modified", "");
            task.direcLev1 = row.getOrDefault("direc_lev1", "");
            task.direcLev2 = row.getOrDefault("direc_lev2", "");
            task.direcLev3 = row.getOrDefault("direc_lev3", "");
            task.direcLev4 = row.getOrDefault("direc_lev4", "");
            task.direcLev5 = row.getOrDefault("direc_lev5", "");
            task.isDownstream = row.getOrDefault("is_downstream", "");
            task.param = row.getOrDefault("param", "");
            task.nodeId = row.getOrDefault("node_id", "");
            task.ds = row.getOrDefault("ds", "");
            return task;
        }
    }

    static class Cli {
        private final Map<String, String> values = new HashMap<>();

        static Cli parse(String[] args) {
            Cli cli = new Cli();
            for (int i = 0; i < args.length; i++) {
                String arg = args[i];
                if (!arg.startsWith("--")) {
                    continue;
                }
                String key;
                String value;
                int eq = arg.indexOf('=');
                if (eq > 0) {
                    key = arg.substring(2, eq);
                    value = arg.substring(eq + 1);
                } else {
                    key = arg.substring(2);
                    value = i + 1 < args.length && !args[i + 1].startsWith("--") ? args[++i] : "true";
                }
                cli.values.put(key, value);
            }
            return cli;
        }

        String get(String key, String defaultValue) {
            return values.getOrDefault(key, defaultValue);
        }
    }
}
