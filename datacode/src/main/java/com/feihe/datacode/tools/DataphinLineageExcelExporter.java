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
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Properties;
import java.util.Queue;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 递归导出 Dataphin 表级血缘连通关系和任务明细。
 *
 * 示例：
 * java -cp "target/classes:$(cat target/classpath.txt)" \
 *   com.feihe.datacode.tools.DataphinLineageExcelExporter \
 *   --table ads_ec_fn_gross_profit_day_df \
 *   --output output/ads_ec_fn_gross_profit_day_df_lineage.xlsx
 */
public class DataphinLineageExcelExporter {
    private static final String DRIVER = "com.aliyun.odps.jdbc.OdpsDriver";
    private static final String DEFAULT_ENDPOINT = "http://service.cn-beijing.maxcompute.aliyun.com/api";
    private static final String DEFAULT_PROJECT = "firmus_dataphin_prd_ads";
    private static final String LINEAGE_TABLE = "firmus_dataphin_prd_ads.ads_dataphin_table_blood_relationship";
    private static final String TASK_TABLE = "firmus_dataphin_prd_ads.ads_dataphin_vdm_node_detail";
    private static final int DEFAULT_BATCH_SIZE = 500;
    private static final int DEFAULT_MAX_TABLES = 100000;
    private static final int DEFAULT_MAX_ITERATIONS = 300;
    private static final int EXCEL_CELL_LIMIT = 32767;

    public static void main(String[] args) throws Exception {
        quietThirdPartyLogs();
        Cli cli = Cli.parse(args);
        String rootTable = cli.get("table", "ads_ec_fn_gross_profit_day_df").trim();
        String output = cli.get("output", "output/" + rootTable + "_lineage.xlsx").trim();
        String configPath = cli.get("config", "config/datacode.env").trim();
        int maxTables = Integer.parseInt(cli.get("max-tables", String.valueOf(DEFAULT_MAX_TABLES)));
        int maxIterations = Integer.parseInt(cli.get("max-iterations", String.valueOf(DEFAULT_MAX_ITERATIONS)));
        int batchSize = Integer.parseInt(cli.get("batch-size", String.valueOf(DEFAULT_BATCH_SIZE)));
        int edgePageSize = Integer.parseInt(cli.get("edge-page-size", "9000"));
        int taskPageSize = Integer.parseInt(cli.get("task-page-size", "2000"));
        boolean fullScan = Boolean.parseBoolean(cli.get("full-scan", "true"));
        boolean taskFullScan = Boolean.parseBoolean(cli.get("task-full-scan", "true"));
        String dsArg = cli.get("ds", "").trim();

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
        String project = firstNotBlank(System.getenv("DATAPHIN_DEFAULT_PROJECT"), config.get("DATAPHIN_DEFAULT_PROJECT"), DEFAULT_PROJECT);

        if (isBlank(accessKeyId) || isBlank(accessKeySecret)) {
            throw new IllegalStateException("未配置 MaxCompute 凭证，请在环境变量或 config/datacode.env 中配置 ALIYUN_DATA_PLATFORM_AK / ALIYUN_DATA_PLATFORM_SK");
        }

        Class.forName(DRIVER);
        Properties props = new Properties();
        props.put("access_id", accessKeyId);
        props.put("access_key", accessKeySecret);
        String jdbcUrl = "jdbc:odps:" + endpoint + "?project=" + project;

        long start = System.currentTimeMillis();
        try (Connection conn = DriverManager.getConnection(jdbcUrl, props)) {
            String ds = isBlank(dsArg) ? queryLatestDs(conn) : dsArg;
            if (isBlank(ds)) {
                throw new IllegalStateException("血缘表未查询到可用 ds");
            }

            System.out.println("root_table=" + rootTable);
            System.out.println("lineage_ds=" + ds);
            System.out.println("output=" + Paths.get(output).toAbsolutePath().normalize());
            System.out.println("collect_mode=" + (fullScan ? "full-scan" : "iterative"));

            Graph graph = fullScan
                    ? collectGraphFullScan(conn, rootTable, ds, maxTables, edgePageSize)
                    : collectGraph(conn, rootTable, ds, maxTables, maxIterations, batchSize);
            computeRoles(graph, rootTable);
            List<TaskNode> tasks = taskFullScan
                    ? queryTasksFullScan(conn, ds, graph.allNodeIds(), taskPageSize)
                    : queryTasks(conn, ds, graph.allNodeIds(), batchSize);
            writeExcel(output, rootTable, ds, graph, tasks, System.currentTimeMillis() - start);

            System.out.println("tables=" + graph.finalTables(rootTable).size());
            System.out.println("edges=" + graph.edges.size());
            System.out.println("tasks=" + tasks.size());
            System.out.println("done=" + Paths.get(output).toAbsolutePath().normalize());
        }
    }

    private static void quietThirdPartyLogs() {
        Configurator.setRootLevel(Level.WARN);
        Configurator.setLevel("com.aliyun.odps", Level.ERROR);
        Configurator.setLevel("com.aliyun.odps.jdbc", Level.ERROR);
    }

    private static Graph collectGraphFullScan(Connection conn, String rootTable, String ds, int maxTables, int edgePageSize) throws Exception {
        List<LineageEdge> allEdges = queryAllEdges(conn, ds, edgePageSize);
        System.out.println("lineage_edges_scanned=" + allEdges.size());

        Map<TableKey, List<LineageEdge>> incidentEdges = new HashMap<>();
        Set<TableKey> roots = new LinkedHashSet<>();
        for (LineageEdge edge : allEdges) {
            TableKey source = edge.sourceKey();
            TableKey target = edge.targetKey();
            incidentEdges.computeIfAbsent(source, key -> new ArrayList<>()).add(edge);
            incidentEdges.computeIfAbsent(target, key -> new ArrayList<>()).add(edge);
            if (source.table.equalsIgnoreCase(rootTable)) {
                roots.add(source);
            }
            if (target.table.equalsIgnoreCase(rootTable)) {
                roots.add(target);
            }
        }
        if (roots.isEmpty()) {
            throw new IllegalStateException("血缘表 ds=" + ds + " 中未找到目标表：" + rootTable);
        }

        Graph graph = new Graph();
        Queue<TableKey> pending = new ArrayDeque<>();
        for (TableKey root : roots) {
            graph.discover(root, 0);
            pending.add(root);
        }

        int processed = 0;
        while (!pending.isEmpty()) {
            TableKey current = pending.poll();
            processed++;
            int nextDistance = graph.distance.getOrDefault(current, 0) + 1;
            for (LineageEdge edge : incidentEdges.getOrDefault(current, Collections.emptyList())) {
                graph.addEdge(edge);
                TableKey source = edge.sourceKey();
                TableKey target = edge.targetKey();
                if (graph.discover(source, nextDistance)) {
                    pending.add(source);
                }
                if (graph.discover(target, nextDistance)) {
                    pending.add(target);
                }
                if (graph.tables.size() > maxTables) {
                    throw new IllegalStateException("递归发现表数量超过 max-tables=" + maxTables + "，请检查是否血缘范围过大或调大参数");
                }
            }
            if (processed % 1000 == 0) {
                System.out.printf(Locale.ROOT, "component_scan_processed=%d discovered_tables=%d discovered_edges=%d%n",
                        processed, graph.tables.size(), graph.edges.size());
            }
        }
        System.out.printf(Locale.ROOT, "component_scan_processed=%d discovered_tables=%d discovered_edges=%d%n",
                processed, graph.tables.size(), graph.edges.size());
        return graph;
    }

    private static Graph collectGraph(Connection conn, String rootTable, String ds, int maxTables, int maxIterations, int batchSize) throws Exception {
        Graph graph = new Graph();
        TableKey seed = new TableKey("", rootTable);
        Queue<TableKey> pending = new ArrayDeque<>();
        pending.add(seed);
        graph.discover(seed, 0);

        int iteration = 0;
        while (!pending.isEmpty()) {
            if (++iteration > maxIterations) {
                throw new IllegalStateException("递归超过 max-iterations=" + maxIterations + "，请检查是否血缘范围过大或调大参数");
            }
            List<TableKey> batch = new ArrayList<>();
            while (!pending.isEmpty() && batch.size() < batchSize) {
                TableKey key = pending.poll();
                if (graph.queried.add(key)) {
                    batch.add(key);
                }
            }
            if (batch.isEmpty()) {
                continue;
            }
            int currentDepth = batch.stream().mapToInt(key -> graph.distance.getOrDefault(key, 0)).min().orElse(0);
            List<LineageEdge> edges = queryEdges(conn, ds, batch);
            System.out.printf(Locale.ROOT, "iteration=%d batch=%d new_edges=%d discovered_tables=%d%n",
                    iteration, batch.size(), edges.size(), graph.tables.size());

            for (LineageEdge edge : edges) {
                if (graph.addEdge(edge)) {
                    boolean sourceNew = graph.discover(edge.sourceKey(), currentDepth + 1);
                    boolean targetNew = graph.discover(edge.targetKey(), currentDepth + 1);
                    if (graph.tables.size() > maxTables) {
                        throw new IllegalStateException("递归发现表数量超过 max-tables=" + maxTables + "，请检查是否血缘范围过大或调大参数");
                    }
                    if (sourceNew) {
                        pending.add(edge.sourceKey());
                    }
                    if (targetNew) {
                        pending.add(edge.targetKey());
                    }
                }
            }
        }
        return graph;
    }

    private static String queryLatestDs(Connection conn) throws Exception {
        String sql = "SELECT MAX(ds) AS ds FROM " + LINEAGE_TABLE;
        List<Map<String, String>> rows = query(conn, sql);
        if (rows.isEmpty()) {
            return "";
        }
        return rows.get(0).getOrDefault("ds", "");
    }

    private static List<LineageEdge> queryAllEdges(Connection conn, String ds, int pageSize) throws Exception {
        long total = queryCount(conn, "SELECT COUNT(1) AS cnt FROM " + LINEAGE_TABLE + " WHERE ds = '" + escapeSql(ds) + "'");
        System.out.println("lineage_edges_total=" + total);
        if (total <= 0) {
            return Collections.emptyList();
        }
        if (pageSize <= 0 || pageSize > 9000) {
            pageSize = 9000;
        }

        List<LineageEdge> edges = new ArrayList<>();
        String selectColumns = "id,source_project,source_table,source_table_original,"
                + "target_project,target_table,target_table_original,"
                + "target_owner_name,target_modifier_name,"
                + "CAST(target_gmt_create AS STRING) AS target_gmt_create,"
                + "CAST(target_gmt_modified AS STRING) AS target_gmt_modified,"
                + "time_wimdow,strategy,source_node_id,target_node_id,ds";
        for (long start = 0; start < total; start += pageSize) {
            long end = Math.min(start + pageSize, total);
            String sql = "SELECT " + selectColumns + " FROM ("
                    + "SELECT " + selectColumns + ","
                    + "ROW_NUMBER() OVER (ORDER BY id,source_project,source_table,target_project,target_table,source_node_id,target_node_id) AS rn "
                    + "FROM " + LINEAGE_TABLE + " "
                    + "WHERE ds = '" + escapeSql(ds) + "'"
                    + ") t WHERE rn > " + start + " AND rn <= " + end;
            List<Map<String, String>> rows = query(conn, sql);
            for (Map<String, String> row : rows) {
                edges.add(LineageEdge.from(row));
            }
            System.out.printf(Locale.ROOT, "lineage_edges_fetched=%d/%d%n", edges.size(), total);
        }
        if (edges.size() != total) {
            throw new IllegalStateException("血缘边分页读取不完整，expected=" + total + ", actual=" + edges.size());
        }
        return edges;
    }

    private static long queryCount(Connection conn, String sql) throws Exception {
        List<Map<String, String>> rows = query(conn, sql);
        if (rows.isEmpty()) {
            return 0L;
        }
        String value = firstNotBlank(rows.get(0).get("cnt"), rows.get(0).get("count"));
        return isBlank(value) ? 0L : Long.parseLong(value);
    }

    private static List<LineageEdge> queryEdges(Connection conn, String ds, List<TableKey> keys) throws Exception {
        String condition = keys.stream()
                .map(DataphinLineageExcelExporter::edgeCondition)
                .collect(Collectors.joining(" OR "));
        String sql = "SELECT id,source_project,source_table,source_table_original,"
                + "target_project,target_table,target_table_original,"
                + "target_owner_name,target_modifier_name,"
                + "CAST(target_gmt_create AS STRING) AS target_gmt_create,"
                + "CAST(target_gmt_modified AS STRING) AS target_gmt_modified,"
                + "time_wimdow,strategy,source_node_id,target_node_id,ds "
                + "FROM " + LINEAGE_TABLE + " "
                + "WHERE ds = '" + escapeSql(ds) + "' AND (" + condition + ")";
        List<Map<String, String>> rows = query(conn, sql);
        List<LineageEdge> edges = new ArrayList<>();
        for (Map<String, String> row : rows) {
            edges.add(LineageEdge.from(row));
        }
        return edges;
    }

    private static String edgeCondition(TableKey key) {
        if (isBlank(key.project)) {
            return "(source_table = '" + escapeSql(key.table) + "' OR target_table = '" + escapeSql(key.table) + "')";
        }
        String project = escapeSql(key.project);
        String table = escapeSql(key.table);
        return "((source_project = '" + project + "' AND source_table = '" + table + "') "
                + "OR (target_project = '" + project + "' AND target_table = '" + table + "'))";
    }

    private static List<TaskNode> queryTasks(Connection conn, String ds, Set<String> nodeIds, int batchSize) throws Exception {
        List<String> ids = nodeIds.stream().filter(id -> !isBlank(id)).sorted().collect(Collectors.toList());
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
        }
        tasks.sort(Comparator.comparing((TaskNode t) -> nullToEmpty(t.projectName))
                .thenComparing(t -> nullToEmpty(t.nodeName))
                .thenComparing(t -> nullToEmpty(t.nodeId)));
        return tasks;
    }

    private static List<TaskNode> queryTasksFullScan(Connection conn, String ds, Set<String> nodeIds, int pageSize) throws Exception {
        Set<String> targetNodeIds = nodeIds.stream().filter(id -> !isBlank(id)).collect(Collectors.toSet());
        if (targetNodeIds.isEmpty()) {
            return Collections.emptyList();
        }
        long total = queryCount(conn, "SELECT COUNT(1) AS cnt FROM " + TASK_TABLE + " WHERE ds = '" + escapeSql(ds) + "'");
        System.out.println("tasks_total=" + total);
        if (pageSize <= 0 || pageSize > 5000) {
            pageSize = 2000;
        }

        String innerColumns = "project_name,node_name,owner_name,modifier_name,node_type,operator_type,"
                + "cron_expression,schedule_interval_type,directorys,content,"
                + "CAST(gmt_create AS STRING) AS gmt_create,"
                + "CAST(gmt_modified AS STRING) AS gmt_modified,"
                + "direc_lev1,direc_lev2,direc_lev3,direc_lev4,direc_lev5,"
                + "is_downstream,param,node_id,ds";
        String outerColumns = "project_name,node_name,owner_name,modifier_name,node_type,operator_type,"
                + "cron_expression,schedule_interval_type,directorys,content,gmt_create,gmt_modified,"
                + "direc_lev1,direc_lev2,direc_lev3,direc_lev4,direc_lev5,"
                + "is_downstream,param,node_id,ds";

        List<TaskNode> tasks = new ArrayList<>();
        for (long start = 0; start < total; start += pageSize) {
            long end = Math.min(start + pageSize, total);
            String sql = "SELECT " + outerColumns + " FROM ("
                    + "SELECT " + innerColumns + ","
                    + "ROW_NUMBER() OVER (ORDER BY node_id,project_name,node_name) AS rn "
                    + "FROM " + TASK_TABLE + " "
                    + "WHERE ds = '" + escapeSql(ds) + "'"
                    + ") t WHERE rn > " + start + " AND rn <= " + end;
            for (Map<String, String> row : query(conn, sql)) {
                if (targetNodeIds.contains(row.getOrDefault("node_id", ""))) {
                    tasks.add(TaskNode.from(row));
                }
            }
            System.out.printf(Locale.ROOT, "tasks_scanned=%d/%d matched=%d%n", end, total, tasks.size());
        }
        tasks.sort(Comparator.comparing((TaskNode t) -> nullToEmpty(t.projectName))
                .thenComparing(t -> nullToEmpty(t.nodeName))
                .thenComparing(t -> nullToEmpty(t.nodeId)));
        return tasks;
    }

    private static void computeRoles(Graph graph, String rootTable) {
        Set<TableKey> roots = graph.tables.stream()
                .filter(key -> key.table.equalsIgnoreCase(rootTable) && !isBlank(key.project))
                .collect(Collectors.toCollection(LinkedHashSet::new));
        if (roots.isEmpty()) {
            roots.add(new TableKey("", rootTable));
        }

        Map<TableKey, List<TableKey>> downstream = new HashMap<>();
        Map<TableKey, List<TableKey>> upstream = new HashMap<>();
        Map<TableKey, List<TableKey>> undirected = new HashMap<>();
        for (LineageEdge edge : graph.edges.values()) {
            downstream.computeIfAbsent(edge.sourceKey(), k -> new ArrayList<>()).add(edge.targetKey());
            upstream.computeIfAbsent(edge.targetKey(), k -> new ArrayList<>()).add(edge.sourceKey());
            undirected.computeIfAbsent(edge.sourceKey(), k -> new ArrayList<>()).add(edge.targetKey());
            undirected.computeIfAbsent(edge.targetKey(), k -> new ArrayList<>()).add(edge.sourceKey());
        }
        graph.upstreamTables = reachable(roots, upstream);
        graph.downstreamTables = reachable(roots, downstream);
        graph.undirectedDistance = distances(roots, undirected);
    }

    private static Set<TableKey> reachable(Set<TableKey> roots, Map<TableKey, List<TableKey>> graph) {
        Set<TableKey> seen = new LinkedHashSet<>(roots);
        Queue<TableKey> queue = new ArrayDeque<>(roots);
        while (!queue.isEmpty()) {
            TableKey current = queue.poll();
            for (TableKey next : graph.getOrDefault(current, Collections.emptyList())) {
                if (seen.add(next)) {
                    queue.add(next);
                }
            }
        }
        seen.removeAll(roots);
        return seen;
    }

    private static Map<TableKey, Integer> distances(Set<TableKey> roots, Map<TableKey, List<TableKey>> graph) {
        Map<TableKey, Integer> distances = new HashMap<>();
        Queue<TableKey> queue = new ArrayDeque<>();
        for (TableKey root : roots) {
            distances.put(root, 0);
            queue.add(root);
        }
        while (!queue.isEmpty()) {
            TableKey current = queue.poll();
            int nextDistance = distances.get(current) + 1;
            for (TableKey next : graph.getOrDefault(current, Collections.emptyList())) {
                if (!distances.containsKey(next)) {
                    distances.put(next, nextDistance);
                    queue.add(next);
                }
            }
        }
        return distances;
    }

    private static void writeExcel(String output, String rootTable, String ds, Graph graph, List<TaskNode> tasks, long elapsedMs) throws IOException {
        Path outputPath = Paths.get(output).toAbsolutePath().normalize();
        if (outputPath.getParent() != null) {
            Files.createDirectories(outputPath.getParent());
        }

        try (SXSSFWorkbook workbook = new SXSSFWorkbook(200);
             FileOutputStream out = new FileOutputStream(outputPath.toFile())) {
            workbook.setCompressTempFiles(true);
            CellStyle headerStyle = headerStyle(workbook);
            CellStyle wrapStyle = wrapStyle(workbook);

            writeSummarySheet(workbook, headerStyle, rootTable, ds, graph, tasks, elapsedMs);
            writeTablesSheet(workbook, headerStyle, graph, rootTable);
            writeEdgesSheet(workbook, headerStyle, graph);
            writeTasksSheet(workbook, headerStyle, wrapStyle, tasks, graph);
            workbook.write(out);
        }
    }

    private static void writeSummarySheet(SXSSFWorkbook workbook, CellStyle headerStyle, String rootTable, String ds,
                                          Graph graph, List<TaskNode> tasks, long elapsedMs) {
        Sheet sheet = workbook.createSheet("汇总");
        List<String[]> rows = new ArrayList<>();
        rows.add(new String[]{"目标表", rootTable});
        rows.add(new String[]{"血缘分区ds", ds});
        rows.add(new String[]{"导出时间", LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"))});
        rows.add(new String[]{"表数量", String.valueOf(graph.finalTables(rootTable).size())});
        rows.add(new String[]{"血缘边数量", String.valueOf(graph.edges.size())});
        rows.add(new String[]{"任务数量", String.valueOf(tasks.size())});
        rows.add(new String[]{"耗时毫秒", String.valueOf(elapsedMs)});
        rows.add(new String[]{"说明", "表角色：upstream/downstream 为从目标表按单方向可达；related_mixed 为连通但需要混合上下游路径才能到达。"});
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

    private static void writeTablesSheet(SXSSFWorkbook workbook, CellStyle headerStyle, Graph graph, String rootTable) {
        List<String> headers = list("role", "hop_from_root", "project", "table_name", "table_key", "related_edge_count", "node_ids");
        Sheet sheet = workbook.createSheet("所有相关表");
        writeHeader(sheet, headerStyle, headers);
        List<TableKey> tables = graph.finalTables(rootTable).stream()
                .sorted(Comparator.comparing((TableKey key) -> roleOrder(graph.role(key)))
                        .thenComparing(key -> graph.undirectedDistance.getOrDefault(key, 999999))
                        .thenComparing(key -> key.project)
                        .thenComparing(key -> key.table))
                .collect(Collectors.toList());
        int rowIdx = 1;
        for (TableKey table : tables) {
            Row row = sheet.createRow(rowIdx++);
            int col = 0;
            write(row, col++, graph.role(table));
            write(row, col++, String.valueOf(graph.undirectedDistance.getOrDefault(table, graph.distance.getOrDefault(table, -1))));
            write(row, col++, table.project);
            write(row, col++, table.table);
            write(row, col++, table.key());
            write(row, col++, String.valueOf(graph.edgeCount(table)));
            write(row, col, String.join("\n", graph.nodeIds(table)));
        }
        setWidths(sheet, 18, 14, 28, 42, 72, 18, 60);
    }

    private static void writeEdgesSheet(SXSSFWorkbook workbook, CellStyle headerStyle, Graph graph) {
        List<String> headers = list(
                "id", "source_project", "source_table", "source_table_original",
                "target_project", "target_table", "target_table_original",
                "target_owner_name", "target_modifier_name", "target_gmt_create", "target_gmt_modified",
                "time_wimdow", "strategy", "source_node_id", "target_node_id", "ds"
        );
        Sheet sheet = workbook.createSheet("血缘关系边");
        writeHeader(sheet, headerStyle, headers);
        List<LineageEdge> edges = new ArrayList<>(graph.edges.values());
        edges.sort(Comparator.comparing((LineageEdge e) -> e.sourceProject)
                .thenComparing(e -> e.sourceTable)
                .thenComparing(e -> e.targetProject)
                .thenComparing(e -> e.targetTable));
        int rowIdx = 1;
        for (LineageEdge edge : edges) {
            Row row = sheet.createRow(rowIdx++);
            int col = 0;
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
        setWidths(sheet, 42, 28, 42, 42, 28, 42, 42, 24, 24, 22, 22, 18, 18, 28, 28, 12);
    }

    private static void writeTasksSheet(SXSSFWorkbook workbook, CellStyle headerStyle, CellStyle wrapStyle,
                                        List<TaskNode> tasks, Graph graph) {
        List<String> headers = list(
                "node_id", "appears_in_edges", "project_name", "node_name", "owner_name", "modifier_name",
                "node_type", "operator_type", "cron_expression", "schedule_interval_type", "directorys",
                "gmt_create", "gmt_modified", "direc_lev1", "direc_lev2", "direc_lev3", "direc_lev4", "direc_lev5",
                "is_downstream", "param", "ds", "content"
        );
        Sheet sheet = workbook.createSheet("任务明细");
        writeHeader(sheet, headerStyle, headers);
        Map<String, Integer> nodeEdgeCount = graph.nodeEdgeCount();
        int rowIdx = 1;
        for (TaskNode task : tasks) {
            Row row = sheet.createRow(rowIdx++);
            int col = 0;
            write(row, col++, task.nodeId);
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
            contentCell.setCellValue(cleanExcelCell(task.content));
            contentCell.setCellStyle(wrapStyle);
        }
        setWidths(sheet, 30, 16, 28, 48, 24, 24, 16, 22, 24, 16, 60, 22, 22, 22, 22, 22, 22, 22, 14, 24, 12, 100);
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
        String text = nullToEmpty(value)
                .replaceAll("[\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F]", " ");
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

    private static int roleOrder(String role) {
        switch (role) {
            case "root":
                return 0;
            case "upstream":
                return 1;
            case "downstream":
                return 2;
            case "upstream_and_downstream":
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
        List<String> list = new ArrayList<>();
        Collections.addAll(list, values);
        return list;
    }

    static class Graph {
        final Set<TableKey> tables = new LinkedHashSet<>();
        final Set<TableKey> queried = new HashSet<>();
        final Map<TableKey, Integer> distance = new HashMap<>();
        final Map<String, LineageEdge> edges = new LinkedHashMap<>();
        Set<TableKey> upstreamTables = new LinkedHashSet<>();
        Set<TableKey> downstreamTables = new LinkedHashSet<>();
        Map<TableKey, Integer> undirectedDistance = new HashMap<>();

        boolean discover(TableKey key, int newDistance) {
            boolean added = tables.add(key);
            distance.merge(key, newDistance, Math::min);
            return added;
        }

        boolean addEdge(LineageEdge edge) {
            return edges.putIfAbsent(edge.edgeKey(), edge) == null;
        }

        List<TableKey> finalTables(String rootTable) {
            boolean hasConcreteRoot = tables.stream().anyMatch(key -> !isBlank(key.project) && key.table.equalsIgnoreCase(rootTable));
            return tables.stream()
                    .filter(key -> !(hasConcreteRoot && isBlank(key.project) && key.table.equalsIgnoreCase(rootTable)))
                    .collect(Collectors.toList());
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

        String role(TableKey key) {
            if (undirectedDistance.getOrDefault(key, -1) == 0) {
                return "root";
            }
            boolean upstream = upstreamTables.contains(key);
            boolean downstream = downstreamTables.contains(key);
            if (upstream && downstream) {
                return "upstream_and_downstream";
            }
            if (upstream) {
                return "upstream";
            }
            if (downstream) {
                return "downstream";
            }
            return "related_mixed";
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
